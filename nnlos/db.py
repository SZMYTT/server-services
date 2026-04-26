import os
import logging
from contextlib import contextmanager
from pathlib import Path

import psycopg2

logger = logging.getLogger(__name__)


@contextmanager
def get_conn():
    """Postgres connection with automatic commit/rollback."""
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema():
    """Create the nnlos schema and all tables if they don't exist."""
    schema_path = Path(__file__).parent / "db" / "schema.sql"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(schema_path.read_text())
    logger.info("nnlos schema initialised")
