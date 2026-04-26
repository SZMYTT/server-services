import httpx
import logging
import re
import json
from html.parser import HTMLParser

logger = logging.getLogger("prisma.mcp.browser")

class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.result = []
        self.capture = True

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'nav', 'footer', 'header', 'noscript'):
            self.capture = False

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'nav', 'footer', 'header', 'noscript'):
            self.capture = True
            
    def handle_data(self, data):
        if self.capture:
            text = data.strip()
            if text:
                self.result.append(text)

    def get_text(self):
        return ' '.join(self.result)

async def scrape_rightmove(
    postcode: str,
    max_price: int = 150000,
    min_bedrooms: int = 2,
    max_bedrooms: int = 3,
    max_results: int = 24,
) -> list[dict]:
    """
    Scrape Rightmove for-sale listings by postcode using Playwright.
    Returns a list of listing dicts ready to upsert into property_watchlist.
    Gracefully returns [] if playwright is not installed.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning("[BROWSER] playwright not installed — run: pip install playwright && playwright install chromium")
        return []

    postcode_clean = postcode.upper().replace(" ", "")
    search_url = (
        f"https://www.rightmove.co.uk/property-for-sale/find.html"
        f"?searchType=SALE"
        f"&locationIdentifier=POSTCODE%5E{postcode_clean}"
        f"&maxPrice={max_price}"
        f"&minBedrooms={min_bedrooms}"
        f"&maxBedrooms={max_bedrooms}"
        f"&numberOfPropertiesPerPage={max_results}"
    )

    listings = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
            )
            page = await context.new_page()

            # Block images/fonts to speed up scraping
            await page.route("**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ttf}", lambda r: r.abort())

            await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)

            # Accept cookies if the banner appears
            try:
                await page.click("button#onetrust-accept-btn-handler", timeout=4000)
            except Exception:
                pass

            # Wait for property cards
            try:
                await page.wait_for_selector('[data-test="propertyCard"]', timeout=12000)
            except Exception:
                logger.warning("[RIGHTMOVE] No property cards found for %s", postcode)
                await browser.close()
                return []

            cards = await page.query_selector_all('[data-test="propertyCard"]')
            for card in cards[:max_results]:
                try:
                    # Address
                    addr_el = await card.query_selector('[data-test="address"]')
                    address = (await addr_el.inner_text()).strip() if addr_el else ""

                    # Price
                    price_el = await card.query_selector('[data-test="price"]')
                    price_text = (await price_el.inner_text()).strip() if price_el else ""
                    price = None
                    m = re.search(r"[\d,]+", price_text.replace(",", ""))
                    if m:
                        price = int(m.group().replace(",", ""))

                    # Bedrooms
                    bed_el = await card.query_selector('[data-test="property-bedroom"]')
                    beds_text = (await bed_el.inner_text()).strip() if bed_el else ""
                    beds = None
                    bm = re.search(r"\d+", beds_text)
                    if bm:
                        beds = int(bm.group())

                    # Property type (from summary)
                    type_el = await card.query_selector('[data-test="property-type"]')
                    prop_type = (await type_el.inner_text()).strip() if type_el else ""

                    # Listing URL
                    link_el = await card.query_selector('a[href*="/properties/"]')
                    href = await link_el.get_attribute("href") if link_el else ""
                    listing_url = f"https://www.rightmove.co.uk{href}" if href and href.startswith("/") else href

                    if address:
                        listings.append({
                            "address": address,
                            "postcode": postcode.upper(),
                            "listing_url": listing_url,
                            "source": "rightmove",
                            "asking_price": price,
                            "property_type": prop_type,
                            "bedrooms": beds,
                            "notes": "",
                            "status": "watching",
                        })
                except Exception as exc:
                    logger.debug("[RIGHTMOVE] Card parse error: %s", exc)

            await browser.close()
    except Exception as exc:
        logger.error("[RIGHTMOVE] scrape error: %s", exc)

    logger.info("[RIGHTMOVE] Found %d listings for %s (max £%s)", len(listings), postcode, max_price)
    return listings


async def sync_rightmove_to_watchlist(
    postcode: str,
    max_price: int = 150000,
    min_bedrooms: int = 2,
    max_bedrooms: int = 3,
) -> dict:
    """
    Scrape Rightmove and upsert results into property_watchlist.
    Uses listing_url as the dedup key.
    """
    import os
    import sys
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from services.queue import _get_conn

    listings = await scrape_rightmove(postcode, max_price, min_bedrooms, max_bedrooms)
    if not listings:
        return {"synced": 0, "errors": 0}

    synced = 0
    errors = 0
    try:
        conn = _get_conn()
        cur = conn.cursor()
        for l in listings:
            try:
                cur.execute(
                    """
                    INSERT INTO property_watchlist
                        (address, postcode, listing_url, source, asking_price, property_type, bedrooms, status, notes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (listing_url) DO UPDATE SET
                        asking_price = EXCLUDED.asking_price,
                        status = CASE
                            WHEN property_watchlist.status = 'archived' THEN 'watching'
                            ELSE property_watchlist.status
                        END
                    """,
                    (l["address"], l["postcode"], l["listing_url"] or None, l["source"],
                     l["asking_price"], l["property_type"], l["bedrooms"],
                     l["status"], l["notes"]),
                )
                synced += 1
            except Exception as exc:
                logger.debug("[RIGHTMOVE] upsert error: %s", exc)
                errors += 1
        conn.commit()
        cur.close()
        conn.close()
    except Exception as exc:
        logger.error("[RIGHTMOVE] DB error: %s", exc)
        errors += 1

    logger.info("[RIGHTMOVE] Upserted %d listings, %d errors", synced, errors)
    return {"synced": synced, "errors": errors}


async def exact_scrape(url: str) -> str:
    """Scrapes the text content off a webpage directly using httpx"""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                extractor = TextExtractor()
                extractor.feed(resp.text)
                text = extractor.get_text()
                # Limit return length so we don't blow out the LLM context limits on massive pages
                return text[:15000]
            else:
                logger.warning(f"[BROWSER] Failed to scrape {url} - Status: {resp.status_code}")
                return ""
    except Exception as e:
        logger.warning(f"[BROWSER] Failed to scrape {url}: {e}")
        return ""
