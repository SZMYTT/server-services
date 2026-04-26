import os
import logging
import httpx

logger = logging.getLogger("prisma.erp_notifier")

NTFY_URL = os.getenv("NTFY_URL", "http://localhost:8002")


async def notify(
    title: str,
    body: str,
    topic: str = "prisma-erp",
    priority: int = 3,
    tags: list[str] | None = None,
) -> bool:
    """POST a push notification to ntfy."""
    headers = {
        "Title": title,
        "Priority": str(priority),
        "Content-Type": "text/plain",
    }
    if tags:
        headers["Tags"] = ",".join(tags)

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                f"{NTFY_URL}/{topic}",
                content=body,
                headers=headers,
            )
            resp.raise_for_status()
            return True
    except Exception as exc:
        logger.warning("ntfy notification failed: %s", exc)
        return False


async def notify_new_order(order: dict) -> bool:
    customer = order.get("customer_name", "Unknown")
    total = order.get("total", 0)
    items = order.get("items_summary", "")
    return await notify(
        title="New Candles Order",
        body=f"{customer} — £{total:.2f}{chr(10)}{items}".strip(),
        topic="prisma-candles",
        priority=4,
        tags=["shopping", "candle"],
    )


async def notify_new_booking(booking: dict) -> bool:
    client = booking.get("client_name", "Unknown")
    service = booking.get("service_name", "")
    dt = booking.get("appointment_at", "")
    return await notify(
        title="New Nursing Booking",
        body=f"{client} — {service}{chr(10)}{dt}".strip(),
        topic="prisma-nursing",
        priority=4,
        tags=["calendar", "massage"],
    )


async def notify_low_stock(item: dict) -> bool:
    name = item.get("name", "Unknown item")
    qty = item.get("quantity", 0)
    reorder = item.get("reorder_level", 0)
    return await notify(
        title="Low Stock Alert",
        body=f"{name}: {qty} remaining (reorder at {reorder})",
        topic="prisma-candles",
        priority=4,
        tags=["warning", "package"],
    )


async def notify_new_auction(auction: dict) -> bool:
    title_str = auction.get("title", "Unknown vehicle")
    source = auction.get("source", "").upper()
    price = auction.get("asking_price")
    price_str = f" — £{price:,.0f}" if price else ""
    return await notify(
        title=f"New Auction Alert [{source}]",
        body=f"{title_str}{price_str}",
        topic="prisma-cars",
        priority=3,
        tags=["car", "moneybag"],
    )


async def notify_auction_won(auction: dict) -> bool:
    title_str = auction.get("title", "Unknown vehicle")
    price = auction.get("asking_price") or auction.get("buy_price", 0)
    return await notify(
        title="Auction Won — Added to Inventory",
        body=f"{title_str} bought for £{price:,.0f}",
        topic="prisma-cars",
        priority=5,
        tags=["trophy", "car"],
    )


async def notify_watchlist_update(listing: dict) -> bool:
    address = listing.get("address", "Unknown property")
    status = listing.get("status", "")
    price = listing.get("asking_price")
    price_str = f" — £{price:,.0f}" if price else ""
    return await notify(
        title=f"Property Watchlist: {status.replace('_', ' ').title()}",
        body=f"{address}{price_str}",
        topic="prisma-property",
        priority=3,
        tags=["house", "eyes"],
    )
