"""Open Food Facts barcode lookup service."""

import logging
import httpx

logger = logging.getLogger(__name__)

_OFF_URL = "https://world.openfoodfacts.org/api/v2/product/{barcode}.json"
_FIELDS = "product_name,brands,nutriments,serving_size,serving_quantity"


def _safe_float(val) -> float | None:
    try:
        return float(val) if val not in (None, "", "unknown") else None
    except (TypeError, ValueError):
        return None


async def lookup_barcode(barcode: str) -> dict | None:
    """
    Query Open Food Facts and return a normalised ingredient dict,
    or None if not found / request fails.

    Macros are stored per 100g to match health.ingredients schema.
    """
    url = _OFF_URL.format(barcode=barcode.strip())
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params={"fields": _FIELDS})
        if resp.status_code != 200:
            return None
        data = resp.json()
    except Exception as exc:
        logger.warning("OFF request failed for %s: %s", barcode, exc)
        return None

    if data.get("status") != 1:
        return None

    product = data.get("product", {})
    n = product.get("nutriments", {})

    name = (product.get("product_name") or "").strip()
    if not name:
        return None

    return {
        "name":           name,
        "barcode":        barcode.strip(),
        "brand":          (product.get("brands") or "").strip() or None,
        "serving_size_g": _safe_float(product.get("serving_quantity")) or 100.0,
        "kcal":           _safe_float(n.get("energy-kcal_100g")),
        "protein_g":      _safe_float(n.get("proteins_100g")),
        "carbs_g":        _safe_float(n.get("carbohydrates_100g")),
        "fat_g":          _safe_float(n.get("fat_100g")),
        "fibre_g":        _safe_float(n.get("fiber_100g")),
        "source":         "openfoodfacts",
    }
