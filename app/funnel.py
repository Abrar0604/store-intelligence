"""
Funnel endpoint — GET /stores/{store_id}/funnel

Computes a session-based conversion funnel:
  Entry → Zone Visit → Billing Queue → Purchase

Each stage counts unique sessions (visitor_ids), not raw events.
Re-entries do not double-count (same visitor_id = same session).
"""

import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Query

from app.models import FunnelResponse, FunnelStage
from app.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/stores/{store_id}/funnel", response_model=FunnelResponse)
async def get_funnel(
    store_id: str,
    date: str = Query(None, description="Date filter YYYY-MM-DD"),
):
    """
    Session-based conversion funnel.
    
    Stages:
    1. Entry — unique visitors who entered the store
    2. Zone Visit — visitors who visited at least one product zone
    3. Billing Queue — visitors who reached the billing area
    4. Purchase — visitors correlated with a POS transaction
    """
    try:
        db = await get_db()
    except Exception:
        raise HTTPException(status_code=503, detail={
            "error": "database_unavailable",
            "message": "Database is temporarily unavailable.",
        })

    if date:
        date_prefix = date
    else:
        date_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # ── Stage 1: Entry (unique visitors who entered) ────────────────────
    cursor = await db.execute(
        """SELECT COUNT(DISTINCT visitor_id) FROM events
           WHERE store_id = ? AND is_staff = 0
           AND event_type IN ('ENTRY', 'REENTRY')
           AND timestamp LIKE ?""",
        (store_id, f"{date_prefix}%"),
    )
    row = await cursor.fetchone()
    entry_count = row[0] if row else 0

    # ── Stage 2: Zone Visit (visited at least one product zone) ─────────
    cursor = await db.execute(
        """SELECT COUNT(DISTINCT visitor_id) FROM events
           WHERE store_id = ? AND is_staff = 0
           AND event_type IN ('ZONE_ENTER', 'ZONE_DWELL')
           AND zone_id IS NOT NULL
           AND zone_id NOT IN ('ENTRY_EXIT', 'BILLING', 'CASH_COUNTER')
           AND timestamp LIKE ?""",
        (store_id, f"{date_prefix}%"),
    )
    row = await cursor.fetchone()
    zone_visit_count = row[0] if row else 0

    # ── Stage 3: Billing Queue (reached billing zone) ───────────────────
    cursor = await db.execute(
        """SELECT COUNT(DISTINCT visitor_id) FROM events
           WHERE store_id = ? AND is_staff = 0
           AND (event_type = 'BILLING_QUEUE_JOIN' 
                OR (event_type IN ('ZONE_ENTER', 'ZONE_DWELL') AND zone_id IN ('BILLING', 'CASH_COUNTER')))
           AND timestamp LIKE ?""",
        (store_id, f"{date_prefix}%"),
    )
    row = await cursor.fetchone()
    billing_count = row[0] if row else 0

    # ── Stage 4: Purchase (correlated with POS transaction) ─────────────
    cursor = await db.execute(
        """SELECT COUNT(DISTINCT e.visitor_id)
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
    purchase_count = row[0] if row else 0

    # ── Build funnel stages ─────────────────────────────────────────────
    stages = []
    base = max(entry_count, 1)  # avoid division by zero

    stage_data = [
        ("Entry", entry_count),
        ("Zone Visit", zone_visit_count),
        ("Billing Queue", billing_count),
        ("Purchase", purchase_count),
    ]

    for i, (stage_name, count) in enumerate(stage_data):
        percentage = round((count / base) * 100, 2) if base > 0 else 0.0
        
        if i == 0:
            drop_off_pct = 0.0
        else:
            prev_count = stage_data[i - 1][1]
            if prev_count > 0:
                drop_off_pct = round(((prev_count - count) / prev_count) * 100, 2)
            else:
                drop_off_pct = 0.0

        stages.append(FunnelStage(
            stage=stage_name,
            count=count,
            percentage=percentage,
            drop_off_pct=max(0.0, drop_off_pct),
        ))

    return FunnelResponse(
        store_id=store_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        session_count=entry_count,
        stages=stages,
    )
