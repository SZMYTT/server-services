"""
Phase 1 — Data Ingestion Layer.

Watches Google Drive for MRP Easy CSV exports, parses them, and upserts
into the nnlos Postgres schema. Replaces syncRawData() and syncOtherData()
GAS functions. Runs incrementally — history is never lost.

Strategies per table:
  append_dedup  — INSERT ON CONFLICT DO NOTHING (raw_movements, keeps full history)
  upsert        — INSERT ... ON CONFLICT DO UPDATE (items, boms, vendors, POs)
  replace       — DELETE matching rows then INSERT (criticall, shop/post orders)
"""

import csv
import io
import logging
import os
from datetime import date, datetime
from typing import Optional

from db import get_conn
from mcp import drive

logger = logging.getLogger(__name__)

# ── Config: one entry per MRP Easy CSV export type ────────────────────────────

INGEST_CONFIGS: dict[str, dict] = {
    "raw_movements": {
        "prefix": "stock_movement",
        "archive_env": "DRIVE_ARCHIVE_RAW",
        "strategy": "append_dedup",
    },
    "items": {
        "prefix": "articles",
        "archive_env": "DRIVE_ARCHIVE_ITEMS",
        "strategy": "upsert",
    },
    "boms": {
        "prefix": "parts",
        "archive_env": "DRIVE_ARCHIVE_BOMS",
        "strategy": "upsert",
    },
    "vendors": {
        "prefix": "vendors",
        "archive_env": "DRIVE_ARCHIVE_VENDORS",
        "strategy": "upsert",
    },
    "purchase_orders": {
        "prefix": "purchase_orders",
        "archive_env": "DRIVE_ARCHIVE_PO",
        "strategy": "upsert",
    },
    "inventory": {
        "prefix": "inventory",
        "archive_env": "DRIVE_ARCHIVE_INV",
        "strategy": "upsert",
    },
    "criticall": {
        "prefix": "critical_on_hand",
        "archive_env": "DRIVE_ARCHIVE_CRITICAL",
        "strategy": "replace",
    },
    "shop_orders": {
        "prefix": "customer_orders",
        "contains": "(1)",
        "archive_env": "DRIVE_ARCHIVE_SHOP",
        "strategy": "replace",
        "order_type": "shop",
    },
    "post_orders": {
        "prefix": "customer_orders",
        "excludes": "(1)",
        "archive_env": "DRIVE_ARCHIVE_POST",
        "strategy": "replace",
        "order_type": "post",
    },
}

# ── Value helpers ──────────────────────────────────────────────────────────────

def _s(val: str) -> str:
    """Strip whitespace and quotes."""
    return val.strip().strip('"').strip("'")


def _str(val: str, default: str = "") -> str:
    cleaned = _s(val)
    return cleaned if cleaned else default


def _flt(val: str) -> Optional[float]:
    cleaned = _s(val).replace(",", "")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _int_val(val: str) -> Optional[int]:
    f = _flt(val)
    return int(f) if f is not None else None


def _date(val: str) -> Optional[date]:
    """Parse dd/mm/yyyy or dd/mm/yy to a date object."""
    v = _s(val).split(" ")[0]  # strip time component if present
    if not v:
        return None
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


# ── CSV parsers — one per data type ───────────────────────────────────────────

def _parse_raw_movements(rows: list[list[str]]) -> list[tuple]:
    out = []
    for r in rows:
        if len(r) < 8:
            continue
        row = (
            _date(r[0]),                                # created_date
            _str(r[1]),                                 # lot ('' not NULL)
            _str(r[2]),                                 # site
            _str(r[3]).upper(),                         # part_no
            _str(r[4]),                                 # description
            _str(r[5]),                                 # group_number
            _str(r[6]),                                 # group_name
            _flt(r[7]),                                 # quantity
            _flt(r[8]) if len(r) > 8 else None,        # cost
            _str(r[9]) if len(r) > 9 else "",           # source ('' not NULL)
            _str(r[10]) if len(r) > 10 else None,       # stock_type
        )
        if row[0] and row[3]:  # require date and part_no
            out.append(row)
    return out


def _parse_items(rows: list[list[str]]) -> list[tuple]:
    # Column indices from NNL_INVENTORY docs (0-based, MRP Easy articles export)
    out = []
    for r in rows:
        if len(r) < 17 or not _s(r[0]):
            continue
        out.append((
            _str(r[0]).upper(),   # part_no
            _str(r[1]),           # description
            _str(r[2]),           # group_number
            _str(r[3]),           # group_name
            _flt(r[4]) or 0,      # in_stock         (E)
            _flt(r[5]) or 0,      # packaged          (F)
            _flt(r[7]) or 0,      # available         (H)
            _flt(r[8]) or 0,      # booked            (I)
            _flt(r[9]) or 0,      # expected_total    (J)
            _flt(r[10]) or 0,     # expected_available(K)
            _flt(r[12]) or 0,     # work_in_progress  (M)
            _flt(r[13]) or 0,     # reorder_point     (N)
            _flt(r[14]) or 0,     # min_qty_mfg       (O)
            _flt(r[15]) or 0,     # cost              (P)
            _flt(r[16]) or 0,     # selling_price     (Q)
            _str(r[18]) if len(r) > 18 else None,   # uom          (S)
            _int_val(r[23]) if len(r) > 23 else 0,  # lead_time    (X)
            _str(r[24]) if len(r) > 24 else None,   # vendor_number(Y)
            _str(r[25]) if len(r) > 25 else None,   # vendor_name  (Z)
            _str(r[26]) if len(r) > 26 else None,   # vendor_part_no(AA)
            _str(r[20]) if len(r) > 20 else None,   # is_procured  (U) — stored as text
            _str(r[22]) if len(r) > 22 else None,   # is_inventory (W)
            _str(r[42]) if len(r) > 42 else None,   # stock_type   (AQ)
        ))
    return out


def _parse_boms(rows: list[list[str]]) -> list[tuple]:
    out = []
    for r in rows:
        if len(r) < 10 or not _s(r[2]) or not _s(r[6]):
            continue
        out.append((
            _str(r[0]),           # bom_number
            _str(r[1]),           # bom_name
            _str(r[2]).upper(),   # product_no
            _str(r[3]),           # product_name
            _str(r[4]),           # group_number
            _str(r[5]),           # group_name
            _str(r[6]).upper(),   # part_no
            _str(r[7]),           # part_description
            _str(r[8]),           # uom
            _flt(r[9]) or 0,      # quantity
            _flt(r[10]) if len(r) > 10 else None,   # approx_cost
            _str(r[11]) if len(r) > 11 else None,   # notes
            _str(r[12]) if len(r) > 12 else None,   # bom_type
        ))
    return out


def _parse_vendors(rows: list[list[str]]) -> list[tuple]:
    out = []
    for r in rows:
        if len(r) < 2 or not _s(r[0]):
            continue
        out.append((
            _str(r[0]),           # vendor_number
            _str(r[1]),           # name
            _str(r[2]) if len(r) > 2 else None,    # phone
            _str(r[4]) if len(r) > 4 else None,    # email
            _str(r[5]) if len(r) > 5 else None,    # url
            _str(r[6]) if len(r) > 6 else None,    # address
            _flt(r[7]) if len(r) > 7 else None,    # on_time_pct
            _flt(r[8]) if len(r) > 8 else None,    # avg_delay_days
            _str(r[9]) if len(r) > 9 else "GBP",   # currency
            _int_val(r[10]) if len(r) > 10 else None,  # default_lead_time_days
            _flt(r[11]) if len(r) > 11 else None,   # total_cost
            _str(r[12]) if len(r) > 12 else None,   # supplier_type
            _str(r[13]) if len(r) > 13 else None,   # order_notes
            _int_val(r[14]) if len(r) > 14 else None,  # payment_period
            _str(r[15]) if len(r) > 15 else None,   # payment_period_type
        ))
    return out


def _parse_purchase_orders(rows: list[list[str]]) -> list[tuple]:
    out = []
    for r in rows:
        if len(r) < 20 or not _s(r[0]) or not _s(r[1]):
            continue
        out.append((
            _str(r[0]),           # po_number        (A)
            _str(r[1]).upper(),   # part_no           (B)
            _str(r[2]),           # part_description  (C)
            _str(r[4]),           # vendor_part_no    (E)
            _str(r[5]),           # group_number      (F)
            _str(r[6]),           # group_name        (G)
            _flt(r[7]),           # quantity          (H)
            _str(r[8]),           # lot               (I)
            _str(r[9]),           # site              (J)
            _flt(r[10]),          # total             (K)
            _flt(r[13]),          # unit_cost         (N)
            _str(r[14]) or "GBP", # currency          (O)
            _str(r[19]),          # status            (T)
            _str(r[20]),          # product_status    (U)
            _str(r[21]),          # created_by        (V)
            _date(r[22]) if len(r) > 22 else None,   # created_date   (W)
            _date(r[23]) if len(r) > 23 else None,   # expected_date  (X)
            _date(r[24]) if len(r) > 24 else None,   # arrival_date   (Y)
            _str(r[25]) if len(r) > 25 else None,    # order_id       (Z)
            _date(r[26]) if len(r) > 26 else None,   # order_date     (AA)
            _date(r[29]) if len(r) > 29 else None,   # due_date       (AD)
            _date(r[30]) if len(r) > 30 else None,   # shipped_on     (AE)
            _int_val(r[31]) if len(r) > 31 else None,# delay_days     (AF)
            _str(r[32]) if len(r) > 32 else None,    # vendor_number  (AG)
            _str(r[33]) if len(r) > 33 else None,    # vendor_name    (AH)
            _str(r[35]) if len(r) > 35 else None,    # supplier_type  (AJ)
            _str(r[36]) if len(r) > 36 else None,    # order_notes    (AK)
            _str(r[37]) if len(r) > 37 else None,    # stock_type     (AL)
        ))
    return out


def _parse_inventory(rows: list[list[str]]) -> list[tuple]:
    today = date.today()
    out = []
    for r in rows:
        if len(r) < 5 or not _s(r[0]):
            continue
        out.append((
            _str(r[0]).upper(),   # part_no
            _str(r[1]),           # description
            _str(r[2]),           # group_number
            _str(r[3]),           # group_name
            _flt(r[4]) or 0,      # quantity
            _str(r[5]) if len(r) > 5 else None,   # uom
            _flt(r[6]) if len(r) > 6 else None,   # cost
            _flt(r[7]) if len(r) > 7 else None,   # avg_cost
            _flt(r[8]) if len(r) > 8 else 0,      # wip_quantity
            _str(r[10]) if len(r) > 10 else None, # stock_type
            today,                                 # snapshot_date
        ))
    return out


def _parse_criticall(rows: list[list[str]]) -> list[tuple]:
    out = []
    for r in rows:
        if len(r) < 9 or not _s(r[0]) or not _s(r[4]):
            continue
        out.append((
            _str(r[0]).upper(),   # part_no
            _str(r[1]),           # description
            _str(r[2]),           # group_number
            _str(r[3]),           # group_name
            _str(r[4]),           # site
            _flt(r[5]) or 0,      # in_stock
            _flt(r[6]) or 0,      # available
            _flt(r[7]) or 0,      # expected_available
            _flt(r[8]) or 0,      # reorder_point
            _str(r[9]) if len(r) > 9 else None,   # stock_type
        ))
    return out


def _parse_shop_orders(rows: list[list[str]], order_type: str) -> list[tuple]:
    # SHOP/POST have identical structure: Part No=Z(25), Qty=AE(30), Shipped=AF(31)
    out = []
    for r in rows:
        if len(r) < 31 or not _s(r[25]):
            continue
        out.append((
            _str(r[0]),           # order_number
            _str(r[2]),           # customer_name
            _str(r[3]),           # email
            _str(r[5]),           # status
            _flt(r[7]),           # total
            _date(r[12]),         # created_date
            _date(r[13]),         # delivery_date
            _str(r[25]).upper(),  # part_no          (Z)
            _str(r[26]),          # part_description (AA)
            _str(r[28]),          # group_number     (AC)
            _str(r[29]),          # group_name       (AD)
            _flt(r[30]) or 0,     # quantity         (AE)
            _flt(r[31]) if len(r) > 31 else 0,  # shipped (AF)
            order_type,           # 'shop' or 'post'
        ))
    return out


# ── SQL statements ─────────────────────────────────────────────────────────────

_SQL: dict[str, str] = {
    "raw_movements": """
        INSERT INTO nnlos.raw_movements
            (created_date, lot, site, part_no, description, group_number,
             group_name, quantity, cost, source, stock_type)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (created_date, lot, part_no, quantity, source) DO NOTHING
    """,
    "items": """
        INSERT INTO nnlos.items
            (part_no, description, group_number, group_name, in_stock, packaged,
             available, booked, expected_total, expected_available, work_in_progress,
             reorder_point, min_qty_mfg, cost, selling_price, uom, lead_time_days,
             vendor_number, vendor_name, vendor_part_no, is_procured, is_inventory,
             stock_type)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (part_no) DO UPDATE SET
            description=EXCLUDED.description, group_number=EXCLUDED.group_number,
            group_name=EXCLUDED.group_name, in_stock=EXCLUDED.in_stock,
            available=EXCLUDED.available, booked=EXCLUDED.booked,
            expected_available=EXCLUDED.expected_available,
            work_in_progress=EXCLUDED.work_in_progress,
            reorder_point=EXCLUDED.reorder_point, cost=EXCLUDED.cost,
            selling_price=EXCLUDED.selling_price, lead_time_days=EXCLUDED.lead_time_days,
            vendor_number=EXCLUDED.vendor_number, vendor_name=EXCLUDED.vendor_name,
            stock_type=EXCLUDED.stock_type, synced_at=NOW()
    """,
    "boms": """
        INSERT INTO nnlos.boms
            (bom_number, bom_name, product_no, product_name, group_number, group_name,
             part_no, part_description, uom, quantity, approx_cost, notes, bom_type)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (bom_number, part_no) DO UPDATE SET
            quantity=EXCLUDED.quantity, approx_cost=EXCLUDED.approx_cost,
            notes=EXCLUDED.notes, synced_at=NOW()
    """,
    "vendors": """
        INSERT INTO nnlos.vendors
            (vendor_number, name, phone, email, url, address, on_time_pct,
             avg_delay_days, currency, default_lead_time_days, total_cost,
             supplier_type, order_notes, payment_period, payment_period_type)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (vendor_number) DO UPDATE SET
            name=EXCLUDED.name, email=EXCLUDED.email, url=EXCLUDED.url,
            on_time_pct=EXCLUDED.on_time_pct, avg_delay_days=EXCLUDED.avg_delay_days,
            default_lead_time_days=EXCLUDED.default_lead_time_days,
            supplier_type=EXCLUDED.supplier_type, order_notes=EXCLUDED.order_notes,
            synced_at=NOW()
    """,
    "purchase_orders": """
        INSERT INTO nnlos.purchase_orders
            (po_number, part_no, part_description, vendor_part_no, group_number,
             group_name, quantity, lot, site, total, unit_cost, currency,
             status, product_status, created_by, created_date, expected_date,
             arrival_date, order_id, order_date, due_date, shipped_on, delay_days,
             vendor_number, vendor_name, supplier_type, order_notes, stock_type)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (po_number, part_no) DO UPDATE SET
            status=EXCLUDED.status, product_status=EXCLUDED.product_status,
            expected_date=EXCLUDED.expected_date, arrival_date=EXCLUDED.arrival_date,
            due_date=EXCLUDED.due_date, shipped_on=EXCLUDED.shipped_on,
            delay_days=EXCLUDED.delay_days, synced_at=NOW()
    """,
    "inventory": """
        INSERT INTO nnlos.inventory_snapshot
            (part_no, description, group_number, group_name, quantity, uom,
             cost, avg_cost, wip_quantity, stock_type, snapshot_date)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (part_no, snapshot_date) DO UPDATE SET
            quantity=EXCLUDED.quantity, avg_cost=EXCLUDED.avg_cost,
            wip_quantity=EXCLUDED.wip_quantity, synced_at=NOW()
    """,
    "criticall": """
        INSERT INTO nnlos.criticall
            (part_no, description, group_number, group_name, site,
             in_stock, available, expected_available, reorder_point, stock_type)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (part_no, site) DO UPDATE SET
            in_stock=EXCLUDED.in_stock, available=EXCLUDED.available,
            expected_available=EXCLUDED.expected_available,
            reorder_point=EXCLUDED.reorder_point, synced_at=NOW()
    """,
    "shop_orders": """
        INSERT INTO nnlos.shop_orders
            (order_number, customer_name, email, status, total, created_date,
             delivery_date, part_no, part_description, group_number, group_name,
             quantity, shipped, order_type)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """,
}

_PARSERS = {
    "raw_movements": _parse_raw_movements,
    "items":         _parse_items,
    "boms":          _parse_boms,
    "vendors":       _parse_vendors,
    "purchase_orders": _parse_purchase_orders,
    "inventory":     _parse_inventory,
    "criticall":     _parse_criticall,
    "shop_orders":   lambda rows: _parse_shop_orders(rows, "shop"),
    "post_orders":   lambda rows: _parse_shop_orders(rows, "post"),
}


# ── Sync log helpers ───────────────────────────────────────────────────────────

def _log_start(conn, sync_type: str, filename: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO nnlos.sync_log (sync_type, filename, status, started_at)"
            " VALUES (%s, %s, 'running', NOW()) RETURNING id",
            (sync_type, filename),
        )
        return cur.fetchone()[0]


def _log_finish(conn, log_id: int, rows: int, error: Optional[str] = None) -> None:
    status = "success" if error is None else "failed"
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE nnlos.sync_log SET status=%s, rows_processed=%s,"
            " error_message=%s, completed_at=NOW() WHERE id=%s",
            (status, rows, error, log_id),
        )


# ── Main pipeline ──────────────────────────────────────────────────────────────

def _parse_csv_text(text: str) -> list[list[str]]:
    """Parse CSV text into rows, skipping the header row."""
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    return rows[1:] if rows else []  # skip header


def _run_type(data_type: str, config: dict) -> dict:
    """Fetch, parse, upsert, and archive one CSV type. Returns a result summary."""
    source_folder = os.environ["GOOGLE_DRIVE_SOURCE_FOLDER"]
    archive_folder = os.environ[config["archive_env"]]

    file_meta = drive.get_latest_file(
        folder_id=source_folder,
        prefix=config["prefix"],
        contains=config.get("contains"),
        excludes=config.get("excludes"),
    )
    if not file_meta:
        logger.info("[%s] No file found in Drive — skipping", data_type)
        return {"type": data_type, "status": "skipped", "rows": 0}

    filename = file_meta["name"]
    logger.info("[%s] Found file: %s", data_type, filename)

    text = drive.download_text(file_meta["id"])
    rows = _parse_csv_text(text)
    if not rows:
        logger.warning("[%s] File was empty after parsing", data_type)
        return {"type": data_type, "status": "empty", "rows": 0}

    parser = _PARSERS[data_type]
    parsed = parser(rows)
    logger.info("[%s] Parsed %d rows (from %d raw)", data_type, len(parsed), len(rows))

    log_id = None
    try:
        with get_conn() as conn:
            log_id = _log_start(conn, data_type, filename)
            conn.commit()

            with conn.cursor() as cur:
                strategy = config["strategy"]

                if strategy == "replace" and data_type in ("shop_orders", "post_orders"):
                    order_type = config.get("order_type", "shop")
                    cur.execute(
                        "DELETE FROM nnlos.shop_orders WHERE order_type = %s",
                        (order_type,),
                    )

                cur.executemany(_SQL[data_type], parsed)

            _log_finish(conn, log_id, len(parsed))

        drive.move_to_folder(file_meta["id"], archive_folder)
        logger.info("[%s] Done — %d rows upserted, file archived", data_type, len(parsed))
        return {"type": data_type, "status": "success", "rows": len(parsed), "file": filename}

    except Exception as exc:
        logger.exception("[%s] Failed: %s", data_type, exc)
        if log_id:
            try:
                with get_conn() as conn:
                    _log_finish(conn, log_id, 0, str(exc))
            except Exception:
                pass
        return {"type": data_type, "status": "failed", "error": str(exc)}


def run(types: Optional[list[str]] = None) -> list[dict]:
    """
    Run ingestion for the given data types (or all if types=None).
    Returns a list of result dicts, one per type attempted.
    """
    to_run = types or list(INGEST_CONFIGS.keys())
    results = []
    for data_type in to_run:
        if data_type not in INGEST_CONFIGS:
            logger.warning("Unknown data type: %s", data_type)
            continue
        results.append(_run_type(data_type, INGEST_CONFIGS[data_type]))
    return results


def run_local(file_path: str, data_type: str) -> dict:
    """
    Test mode: ingest a local CSV file directly, no Drive API needed.
    Useful for validating parsers and DB schema before GCP is set up.

    Usage:
        python services/ingestion.py --local /path/to/stock_movement_2024.csv raw_movements
    """
    from pathlib import Path
    path = Path(file_path)
    if not path.exists():
        return {"type": data_type, "status": "failed", "error": f"File not found: {file_path}"}
    if data_type not in INGEST_CONFIGS:
        return {"type": data_type, "status": "failed", "error": f"Unknown type: {data_type}"}

    config = INGEST_CONFIGS[data_type]
    text = path.read_text(encoding="utf-8", errors="replace")
    rows = _parse_csv_text(text)
    if not rows:
        return {"type": data_type, "status": "empty", "rows": 0}

    parser = _PARSERS[data_type]
    parsed = parser(rows)
    logger.info("[%s] LOCAL — parsed %d rows from %s", data_type, len(parsed), path.name)

    log_id = None
    try:
        with get_conn() as conn:
            log_id = _log_start(conn, data_type, path.name)
            conn.commit()
            with conn.cursor() as cur:
                if config["strategy"] == "replace" and data_type in ("shop_orders", "post_orders"):
                    cur.execute(
                        "DELETE FROM nnlos.shop_orders WHERE order_type = %s",
                        (config.get("order_type", "shop"),),
                    )
                cur.executemany(_SQL[data_type], parsed)
            _log_finish(conn, log_id, len(parsed))
        logger.info("[%s] LOCAL — %d rows written to DB", data_type, len(parsed))
        return {"type": data_type, "status": "success", "rows": len(parsed), "file": path.name}
    except Exception as exc:
        logger.exception("[%s] LOCAL failed: %s", data_type, exc)
        if log_id:
            try:
                with get_conn() as conn:
                    _log_finish(conn, log_id, 0, str(exc))
            except Exception:
                pass
        return {"type": data_type, "status": "failed", "error": str(exc)}


if __name__ == "__main__":
    import sys
    from pathlib import Path
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent.parent / ".env")
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Local test mode: python services/ingestion.py --local <file> <type>
    if len(sys.argv) >= 4 and sys.argv[1] == "--local":
        result = run_local(sys.argv[2], sys.argv[3])
        print(result)
        sys.exit(0 if result["status"] == "success" else 1)

    # Drive mode: python services/ingestion.py [type1 type2 ...]
    requested = sys.argv[1:] if len(sys.argv) > 1 else None
    results = run(requested)
    for r in results:
        print(r)
