"""
Database layer — SQLite with async support.
Handles schema creation, connection management, and graceful degradation.

Supports two POS CSV formats:
  1. Official sample: order_id, order_date, order_time, store_id, product_id, brand_name, total_amount
  2. Problem statement: transaction_id, store_id, timestamp, basket_value_inr
"""

import os
import csv
import aiosqlite
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("DB_PATH", str(Path(__file__).parent.parent / "data" / "store_intelligence.db"))

# Look for POS files in both locations
_POS_CANDIDATES = [
    str(Path(__file__).parent.parent / "data" / "pos_transactions.csv"),
    str(Path(__file__).parent.parent.parent / "POS - sample transactionsb1e826f.csv"),
]

_db: Optional[aiosqlite.Connection] = None


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    store_id TEXT NOT NULL,
    camera_id TEXT NOT NULL,
    visitor_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    zone_id TEXT,
    zone_name TEXT,
    zone_type TEXT,
    is_revenue_zone TEXT,
    dwell_ms INTEGER DEFAULT 0,
    is_staff BOOLEAN DEFAULT FALSE,
    confidence REAL DEFAULT 0.0,
    queue_depth INTEGER,
    sku_zone TEXT,
    session_seq INTEGER,
    gender_pred TEXT,
    age_pred INTEGER,
    age_bucket TEXT,
    group_id TEXT,
    group_size INTEGER,
    zone_hotspot_x REAL,
    zone_hotspot_y REAL,
    queue_join_ts TEXT,
    queue_served_ts TEXT,
    queue_exit_ts TEXT,
    wait_seconds INTEGER,
    queue_position_at_join INTEGER,
    abandoned BOOLEAN,
    ingested_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_events_store ON events(store_id);
CREATE INDEX IF NOT EXISTS idx_events_visitor ON events(visitor_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_zone ON events(zone_id);
CREATE INDEX IF NOT EXISTS idx_events_store_type ON events(store_id, event_type);

CREATE TABLE IF NOT EXISTS pos_transactions (
    transaction_id TEXT PRIMARY KEY,
    store_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    basket_value_inr REAL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_pos_store ON pos_transactions(store_id);
CREATE INDEX IF NOT EXISTS idx_pos_timestamp ON pos_transactions(timestamp);
"""


async def get_db() -> aiosqlite.Connection:
    """Get the database connection, creating schema if needed."""
    global _db
    if _db is None:
        _db = await aiosqlite.connect(DB_PATH)
        _db.row_factory = aiosqlite.Row
        await _db.executescript(SCHEMA_SQL)
        await _db.commit()
        await _load_pos_data(_db)
        logger.info(f"Database initialized at {DB_PATH}")
    return _db


def _detect_pos_format(filepath: str) -> str:
    """Detect which POS CSV format we're dealing with."""
    with open(filepath, "r") as f:
        reader = csv.reader(f)
        header = next(reader)
        header_lower = [h.strip().lower() for h in header]

    if "order_id" in header_lower and "order_date" in header_lower:
        return "official_sample"
    elif "transaction_id" in header_lower:
        return "problem_statement"
    else:
        logger.warning(f"Unknown POS format with headers: {header}")
        return "unknown"


async def _load_pos_data(db: aiosqlite.Connection):
    """Load POS transactions from CSV if not already loaded.
    Handles both the official sample format and the problem statement format."""
    cursor = await db.execute("SELECT COUNT(*) FROM pos_transactions")
    row = await cursor.fetchone()
    if row[0] > 0:
        return  # already loaded

    # Find available POS file
    pos_file = None
    for candidate in _POS_CANDIDATES:
        if os.path.exists(candidate):
            pos_file = candidate
            break

    if not pos_file:
        logger.warning(f"No POS file found. Searched: {_POS_CANDIDATES}")
        return

    fmt = _detect_pos_format(pos_file)
    logger.info(f"Loading POS data from {pos_file} (format: {fmt})")

    if fmt == "official_sample":
        await _load_pos_official(db, pos_file)
    elif fmt == "problem_statement":
        await _load_pos_problem_statement(db, pos_file)
    else:
        logger.warning(f"Skipping POS load — unknown format")


async def _load_pos_official(db: aiosqlite.Connection, filepath: str):
    """Load official sample POS format:
    order_id, order_date, order_time, store_id, product_id, brand_name, total_amount

    Aggregates line items by (order_id, store_id, order_time) into basket totals."""
    orders = {}  # (order_id, store_id) → {timestamp, total}

    with open(filepath, "r") as f:
        reader = csv.DictReader(f)
        for row_data in reader:
            order_id = row_data["order_id"].strip()
            store_id = row_data["store_id"].strip()
            order_date = row_data["order_date"].strip()  # DD-MM-YYYY
            order_time = row_data["order_time"].strip()  # HH:MM:SS
            total_amount = float(row_data["total_amount"].strip())

            # Parse timestamp: DD-MM-YYYY HH:MM:SS → ISO-8601
            try:
                dt = datetime.strptime(f"{order_date} {order_time}", "%d-%m-%Y %H:%M:%S")
                ts = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                logger.warning(f"Skipping POS row with bad timestamp: {order_date} {order_time}")
                continue

            key = (order_id, store_id)
            if key not in orders:
                orders[key] = {"timestamp": ts, "total": 0.0}
            orders[key]["total"] += total_amount

    count = 0
    for (order_id, store_id), data in orders.items():
        await db.execute(
            "INSERT OR IGNORE INTO pos_transactions (transaction_id, store_id, timestamp, basket_value_inr) VALUES (?, ?, ?, ?)",
            (f"TXN_{order_id}", store_id, data["timestamp"], round(data["total"], 2)),
        )
        count += 1

    await db.commit()
    logger.info(f"Loaded {count} POS transactions (aggregated from line items) from {filepath}")


async def _load_pos_problem_statement(db: aiosqlite.Connection, filepath: str):
    """Load problem-statement POS format:
    transaction_id, store_id, timestamp, basket_value_inr"""
    count = 0
    with open(filepath, "r") as f:
        reader = csv.DictReader(f)
        for row_data in reader:
            await db.execute(
                "INSERT OR IGNORE INTO pos_transactions (transaction_id, store_id, timestamp, basket_value_inr) VALUES (?, ?, ?, ?)",
                (
                    row_data["transaction_id"].strip(),
                    row_data["store_id"].strip(),
                    row_data["timestamp"].strip(),
                    float(row_data["basket_value_inr"].strip()),
                ),
            )
            count += 1
    await db.commit()
    logger.info(f"Loaded {count} POS transactions from {filepath}")


async def check_db_health() -> bool:
    """Check database connectivity."""
    try:
        db = await get_db()
        cursor = await db.execute("SELECT 1")
        await cursor.fetchone()
        return True
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return False


async def close_db():
    """Close the database connection."""
    global _db
    if _db:
        await _db.close()
        _db = None
        logger.info("Database connection closed")
