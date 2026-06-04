"""
Metrics endpoint — GET /stores/{store_id}/metrics

Computes real-time store analytics:
- Unique visitors (excluding staff)
- Conversion rate (visitors with billing zone presence near POS transactions)
- Average dwell time per zone
- Current queue depth
- Abandonment rate
- Revenue summary
"""

import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Query

from app.models import MetricsResponse, ZoneDwell
from app.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()

# Zone display names
ZONE_NAMES = {
    "SKINCARE_KOREAN": "Korean Skincare",
    "SKINCARE_NATURAL": "Natural Skincare",
    "SKINCARE_CLINICAL": "Clinical Skincare",
    "MAKEUP_FACE": "Face Makeup",
    "MAKEUP_LIPS_EYES": "Lips & Eyes",
    "FRAGRANCE": "Fragrance",
    "ACCESSORIES": "Accessories",
    "FOH": "Front of House",
    "BILLING": "Billing Counter",
    "CASH_COUNTER": "Cash Counter Queue",
}


@router.get("/stores/{store_id}/metrics", response_model=MetricsResponse)
async def get_metrics(
    store_id: str,
    date: str = Query(None, description="Date filter YYYY-MM-DD (defaults to today)"),
):
    """
    Real-time store metrics computation.
    Excludes staff (is_staff=true) from customer metrics.
    """
    try:
        db = await get_db()
    except Exception:
        raise HTTPException(status_code=503, detail={
            "error": "database_unavailable",
            "message": "Database is temporarily unavailable.",
        })

    # Date filter
    if date:
        date_prefix = date
    else:
        date_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # ── Unique visitors (non-staff, with ENTRY events) ──────────────────
    cursor = await db.execute(
        """SELECT COUNT(DISTINCT visitor_id) as cnt FROM events 
           WHERE store_id = ? AND is_staff = 0 
           AND event_type IN ('ENTRY', 'REENTRY')
           AND timestamp LIKE ?""",
        (store_id, f"{date_prefix}%"),
    )
    row = await cursor.fetchone()
    unique_visitors = row[0] if row else 0

    # ── Total entries and exits ─────────────────────────────────────────
    cursor = await db.execute(
        """SELECT event_type, COUNT(*) as cnt FROM events
           WHERE store_id = ? AND is_staff = 0
           AND event_type IN ('ENTRY', 'REENTRY', 'EXIT')
           AND timestamp LIKE ?
           GROUP BY event_type""",
        (store_id, f"{date_prefix}%"),
    )
    entries = 0
    exits = 0
    async for row in cursor:
        if row[0] in ("ENTRY", "REENTRY"):
            entries += row[1]
        elif row[0] == "EXIT":
            exits += row[1]

    # ── Conversion rate ─────────────────────────────────────────────────
    # Visitors who were in billing zone within 5-min window before a POS transaction
    cursor = await db.execute(
        """SELECT COUNT(DISTINCT e.visitor_id) as converted
           FROM events e
           INNER JOIN pos_transactions p ON e.store_id = p.store_id
           WHERE e.store_id = ?
           AND e.is_staff = 0
           AND e.zone_id IN ('BILLING', 'CASH_COUNTER')
           AND e.event_type IN ('ZONE_ENTER', 'ZONE_DWELL', 'BILLING_QUEUE_JOIN')
           AND e.timestamp LIKE ?
           AND datetime(e.timestamp) BETWEEN datetime(p.timestamp, '-5 minutes') AND datetime(p.timestamp)""",
        (store_id, f"{date_prefix}%"),
    )
    row = await cursor.fetchone()
    converted_visitors = row[0] if row else 0

    conversion_rate = 0.0
    if unique_visitors > 0:
        conversion_rate = min(1.0, converted_visitors / unique_visitors)

    # ── Average dwell by zone ───────────────────────────────────────────
    cursor = await db.execute(
        """SELECT zone_id, AVG(dwell_ms) as avg_dwell, COUNT(*) as visit_count
           FROM events
           WHERE store_id = ? AND is_staff = 0
           AND event_type IN ('ZONE_DWELL', 'ZONE_EXIT')
           AND zone_id IS NOT NULL
           AND dwell_ms > 0
           AND timestamp LIKE ?
           GROUP BY zone_id""",
        (store_id, f"{date_prefix}%"),
    )
    dwell_zones = []
    async for row in cursor:
        zone_id = row[0]
        dwell_zones.append(ZoneDwell(
            zone_id=zone_id,
            zone_name=ZONE_NAMES.get(zone_id, zone_id),
            avg_dwell_ms=round(row[1], 2),
            visit_count=row[2],
        ))

    # ── Current queue depth ─────────────────────────────────────────────
    # Count visitors who entered billing but haven't exited yet
    cursor = await db.execute(
        """SELECT COUNT(DISTINCT visitor_id) as in_queue
           FROM events
           WHERE store_id = ? AND is_staff = 0
           AND zone_id IN ('BILLING', 'CASH_COUNTER')
           AND event_type = 'BILLING_QUEUE_JOIN'
           AND visitor_id NOT IN (
               SELECT DISTINCT visitor_id FROM events
               WHERE store_id = ? AND event_type = 'ZONE_EXIT'
               AND zone_id IN ('BILLING', 'CASH_COUNTER')
           )
           AND timestamp LIKE ?""",
        (store_id, store_id, f"{date_prefix}%"),
    )
    row = await cursor.fetchone()
    queue_depth = row[0] if row else 0

    # ── Abandonment rate ────────────────────────────────────────────────
    cursor = await db.execute(
        """SELECT COUNT(DISTINCT visitor_id) as abandoned
           FROM events
           WHERE store_id = ? AND is_staff = 0
           AND event_type = 'BILLING_QUEUE_ABANDON'
           AND timestamp LIKE ?""",
        (store_id, f"{date_prefix}%"),
    )
    row = await cursor.fetchone()
    abandoned = row[0] if row else 0

    cursor = await db.execute(
        """SELECT COUNT(DISTINCT visitor_id) as joined
           FROM events
           WHERE store_id = ? AND is_staff = 0
           AND event_type = 'BILLING_QUEUE_JOIN'
           AND timestamp LIKE ?""",
        (store_id, f"{date_prefix}%"),
    )
    row = await cursor.fetchone()
    joined_queue = row[0] if row else 0

    abandonment_rate = 0.0
    if joined_queue > 0:
        abandonment_rate = min(1.0, abandoned / joined_queue)

    # ── Revenue from POS ────────────────────────────────────────────────
    cursor = await db.execute(
        """SELECT COUNT(*) as txn_count, COALESCE(SUM(basket_value_inr), 0) as total_rev
           FROM pos_transactions
           WHERE store_id = ? AND timestamp LIKE ?""",
        (store_id, f"{date_prefix}%"),
    )
    row = await cursor.fetchone()
    total_transactions = row[0] if row else 0
    total_revenue = round(row[1], 2) if row else 0.0

    return MetricsResponse(
        store_id=store_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        unique_visitors=unique_visitors,
        total_entries=entries,
        total_exits=exits,
        conversion_rate=round(conversion_rate, 4),
        avg_dwell_by_zone=dwell_zones,
        current_queue_depth=queue_depth,
        abandonment_rate=round(abandonment_rate, 4),
        total_transactions=total_transactions,
        total_revenue=total_revenue,
    )
