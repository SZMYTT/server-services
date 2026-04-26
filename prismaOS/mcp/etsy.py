import os
import logging
import httpx

logger = logging.getLogger("prisma.mcp.etsy")

ETSY_BASE = "https://openapi.etsy.com/v3/application"
ETSY_API_KEY = os.getenv("ETSY_API_KEY", "")
ETSY_ACCESS_TOKEN = os.getenv("ETSY_ACCESS_TOKEN", "")
ETSY_SHOP_ID = os.getenv("ETSY_SHOP_ID", "")


def _headers() -> dict:
    return {
        "x-api-key": ETSY_API_KEY,
        "Authorization": f"Bearer {ETSY_ACCESS_TOKEN}",
        "Accept": "application/json",
    }


def _credentials_ok() -> bool:
    if not all([ETSY_API_KEY, ETSY_ACCESS_TOKEN, ETSY_SHOP_ID]):
        logger.warning("[ETSY] Credentials not configured — skipping API call")
        return False
    return True


async def fetch_unread_messages() -> list[dict]:
    """Fetch unread shop conversations from Etsy API v3."""
    if not _credentials_ok():
        return []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{ETSY_BASE}/shops/{ETSY_SHOP_ID}/conversations",
                headers=_headers(),
                params={"limit": 25, "offset": 0},
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            # Filter to unread only
            return [
                {
                    "message_id": str(c.get("conversation_id")),
                    "sender": c.get("last_message", {}).get("from_user_id"),
                    "subject": c.get("subject", ""),
                    "body": c.get("last_message", {}).get("message_body", ""),
                    "received_at": c.get("last_message", {}).get("creation_tsz"),
                    "unread": not c.get("has_unread_messages", False) is False,
                }
                for c in results
                if c.get("has_unread_messages")
            ]
    except httpx.HTTPStatusError as exc:
        logger.error("[ETSY] fetch_unread_messages HTTP %s: %s", exc.response.status_code, exc.response.text)
        return []
    except Exception as exc:
        logger.error("[ETSY] fetch_unread_messages error: %s", exc)
        return []


async def send_reply(conversation_id: str, message: str) -> bool:
    """Reply to an Etsy conversation."""
    if not _credentials_ok():
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{ETSY_BASE}/shops/{ETSY_SHOP_ID}/conversations/{conversation_id}/messages",
                headers={**_headers(), "Content-Type": "application/json"},
                json={"message": message},
            )
            resp.raise_for_status()
            return True
    except httpx.HTTPStatusError as exc:
        logger.error("[ETSY] send_reply HTTP %s: %s", exc.response.status_code, exc.response.text)
        return False
    except Exception as exc:
        logger.error("[ETSY] send_reply error: %s", exc)
        return False


async def fetch_open_orders() -> list[dict]:
    """Fetch open/unshipped orders from Etsy."""
    if not _credentials_ok():
        return []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{ETSY_BASE}/shops/{ETSY_SHOP_ID}/receipts",
                headers=_headers(),
                params={"status": "open", "limit": 100, "offset": 0},
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            return [
                {
                    "etsy_order_id": str(r.get("receipt_id")),
                    "customer_name": r.get("name", ""),
                    "customer_email": r.get("buyer_email", ""),
                    "subtotal": r.get("subtotal", {}).get("amount", 0) / 100,
                    "shipping": r.get("total_shipping_cost", {}).get("amount", 0) / 100,
                    "total": r.get("grandtotal", {}).get("amount", 0) / 100,
                    "shipping_address": ", ".join(filter(None, [
                        r.get("first_line", ""),
                        r.get("city", ""),
                        r.get("state", ""),
                        r.get("zip", ""),
                    ])),
                    "items": [
                        {
                            "listing_id": t.get("listing_id"),
                            "title": t.get("title", ""),
                            "quantity": t.get("quantity", 1),
                            "price": t.get("price", {}).get("amount", 0) / 100,
                        }
                        for t in r.get("transactions", [])
                    ],
                    "status": "open",
                    "created_at": r.get("create_timestamp"),
                }
                for r in results
            ]
    except httpx.HTTPStatusError as exc:
        logger.error("[ETSY] fetch_open_orders HTTP %s: %s", exc.response.status_code, exc.response.text)
        return []
    except Exception as exc:
        logger.error("[ETSY] fetch_open_orders error: %s", exc)
        return []


async def sync_orders_to_db() -> dict:
    """Pull open Etsy orders and upsert into candles_orders table."""
    import sys
    import os
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from services.queue import _get_conn
    import json

    orders = await fetch_open_orders()
    if not orders:
        return {"synced": 0, "errors": 0}

    synced = 0
    errors = 0
    try:
        conn = _get_conn()
        cur = conn.cursor()
        for o in orders:
            try:
                cur.execute(
                    """
                    INSERT INTO candles_orders
                        (etsy_order_id, customer_name, customer_email, subtotal, shipping, total,
                         shipping_address, items, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (etsy_order_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        updated_at = NOW()
                    """,
                    (
                        o["etsy_order_id"],
                        o["customer_name"],
                        o["customer_email"],
                        o["subtotal"],
                        o["shipping"],
                        o["total"],
                        o["shipping_address"],
                        json.dumps(o["items"]),
                        o["status"],
                    ),
                )
                synced += 1
            except Exception as exc:
                logger.error("[ETSY] sync_orders_to_db row error: %s", exc)
                errors += 1
        conn.commit()
        cur.close()
        conn.close()
    except Exception as exc:
        logger.error("[ETSY] sync_orders_to_db DB error: %s", exc)
        errors += 1

    logger.info("[ETSY] Synced %d orders, %d errors", synced, errors)
    return {"synced": synced, "errors": errors}


async def fetch_listing_inventory(listing_id: str) -> dict:
    """Fetch inventory/stock for a specific Etsy listing."""
    if not _credentials_ok():
        return {}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{ETSY_BASE}/listings/{listing_id}/inventory",
                headers=_headers(),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error("[ETSY] fetch_listing_inventory HTTP %s", exc.response.status_code)
        return {}
    except Exception as exc:
        logger.error("[ETSY] fetch_listing_inventory error: %s", exc)
        return {}
