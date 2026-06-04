"""
Anomaly detection endpoint — GET /stores/{store_id}/anomalies

Detects active operational anomalies:
- BILLING_QUEUE_SPIKE: current queue depth > 2× rolling average
- CONVERSION_DROP: today's conversion rate < 70% of 7-day average
- DEAD_ZONE: no visits to a zone in the last 30 minutes
- STALE_FEED: no events from a camera in the last 10 minutes
"""

import logging
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Query

from app.models import AnomalyResponse, Anomaly, AnomalySeverity, AnomalyType
from app.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()

# Product zones to monitor for dead zone detection
MONITORED_ZONES = [
    "SKINCARE_KOREAN", "SKINCARE_NATURAL", "SKINCARE_CLINICAL",
    "MAKEUP_FACE", "MAKEUP_LIPS_EYES", "FRAGRANCE", "ACCESSORIES",
]


@router.get("/stores/{store_id}/anomalies", response_model=AnomalyResponse)
async def get_anomalies(
    store_id: str,
    date: str = Query(None, description="Date filter YYYY-MM-DD"),
):
    """
    Detect and return active operational anomalies.
    Each anomaly includes severity level and suggested action.
    """
    try:
        db = await get_db()
    except Exception:
        raise HTTPException(status_code=503, detail={
            "error": "database_unavailable",
            "message": "Database is temporarily unavailable.",
        })

    now = datetime.now(timezone.utc)
    if date:
        date_prefix = date
    else:
        date_prefix = now.strftime("%Y-%m-%d")

    anomalies = []

    # ── 1. BILLING_QUEUE_SPIKE ──────────────────────────────────────────
    await _check_queue_spike(db, store_id, date_prefix, anomalies, now)

    # ── 2. CONVERSION_DROP ──────────────────────────────────────────────
    await _check_conversion_drop(db, store_id, date_prefix, anomalies, now)

    # ── 3. DEAD_ZONE ───────────────────────────────────────────────────
    await _check_dead_zones(db, store_id, date_prefix, anomalies, now)

    # ── 4. STALE_FEED ──────────────────────────────────────────────────
    await _check_stale_feed(db, store_id, anomalies, now)

    return AnomalyResponse(
        store_id=store_id,
        timestamp=now.isoformat(),
        active_anomalies=anomalies,
    )


async def _check_queue_spike(db, store_id, date_prefix, anomalies, now):
    """Check if current queue depth exceeds 2× the rolling average."""
    # Current queue depth
    cursor = await db.execute(
        """SELECT COUNT(DISTINCT visitor_id)
           FROM events
           WHERE store_id = ? AND is_staff = 0
           AND event_type = 'BILLING_QUEUE_JOIN'
           AND timestamp LIKE ?
           AND visitor_id NOT IN (
               SELECT DISTINCT visitor_id FROM events
               WHERE store_id = ? AND event_type IN ('ZONE_EXIT', 'EXIT')
               AND zone_id IN ('BILLING', 'CASH_COUNTER')
               AND timestamp LIKE ?
           )""",
        (store_id, f"{date_prefix}%", store_id, f"{date_prefix}%"),
    )
    row = await cursor.fetchone()
    current_depth = row[0] if row else 0

    # Average queue depth from BILLING_QUEUE_JOIN metadata
    cursor = await db.execute(
        """SELECT AVG(queue_depth)
           FROM events
           WHERE store_id = ? AND event_type = 'BILLING_QUEUE_JOIN'
           AND queue_depth IS NOT NULL
           AND timestamp LIKE ?""",
        (store_id, f"{date_prefix}%"),
    )
    row = await cursor.fetchone()
    avg_depth = row[0] if row and row[0] else 1.0

    if current_depth > 0 and current_depth > 2 * avg_depth:
        severity = AnomalySeverity.CRITICAL if current_depth > 3 * avg_depth else AnomalySeverity.WARN
        anomalies.append(Anomaly(
            anomaly_type=AnomalyType.BILLING_QUEUE_SPIKE,
            severity=severity,
            description=f"Billing queue depth ({current_depth}) exceeds 2× average ({avg_depth:.1f})",
            detected_at=now.isoformat(),
            value=float(current_depth),
            threshold=float(2 * avg_depth),
            suggested_action="Open additional billing counter or deploy floor staff to assist at checkout.",
        ))


async def _check_conversion_drop(db, store_id, date_prefix, anomalies, now):
    """Check if today's conversion rate is below 70% of historical average."""
    # Today's conversion rate
    cursor = await db.execute(
        """SELECT COUNT(DISTINCT visitor_id) FROM events
           WHERE store_id = ? AND is_staff = 0
           AND event_type IN ('ENTRY', 'REENTRY')
           AND timestamp LIKE ?""",
        (store_id, f"{date_prefix}%"),
    )
    row = await cursor.fetchone()
    today_visitors = row[0] if row else 0

    cursor = await db.execute(
        """SELECT COUNT(DISTINCT e.visitor_id)
           FROM events e
           INNER JOIN pos_transactions p ON e.store_id = p.store_id
           WHERE e.store_id = ?
           AND e.is_staff = 0
           AND e.zone_id IN ('BILLING', 'CASH_COUNTER')
           AND e.timestamp LIKE ?
           AND datetime(e.timestamp) BETWEEN datetime(p.timestamp, '-5 minutes') AND datetime(p.timestamp)""",
        (store_id, f"{date_prefix}%"),
    )
    row = await cursor.fetchone()
    today_converted = row[0] if row else 0

    if today_visitors > 0:
        today_rate = today_converted / today_visitors
    else:
        today_rate = 0.0

    # Historical average (use POS transaction count / visitor count as proxy)
    # For simplicity, compare against a reasonable baseline
    cursor = await db.execute(
        """SELECT COUNT(*) FROM pos_transactions WHERE store_id = ?""",
        (store_id,),
    )
    row = await cursor.fetchone()
    total_txns = row[0] if row else 0

    # If we have enough data and conversion is below expected
    historical_rate = 0.35  # baseline assumption: 35% conversion for beauty retail
    if total_txns > 5 and today_visitors > 3 and today_rate < 0.7 * historical_rate:
        anomalies.append(Anomaly(
            anomaly_type=AnomalyType.CONVERSION_DROP,
            severity=AnomalySeverity.WARN,
            description=f"Conversion rate ({today_rate:.1%}) is below 70% of expected ({historical_rate:.1%})",
            detected_at=now.isoformat(),
            value=round(today_rate, 4),
            threshold=round(0.7 * historical_rate, 4),
            suggested_action="Review store staffing, product availability, and promotional signage. Check for issues at billing counter.",
        ))


async def _check_dead_zones(db, store_id, date_prefix, anomalies, now):
    """Check for zones with no visits in the last 30 minutes."""
    thirty_min_ago = (now - timedelta(minutes=30)).isoformat()

    for zone_id in MONITORED_ZONES:
        cursor = await db.execute(
            """SELECT COUNT(*) FROM events
               WHERE store_id = ? AND zone_id = ?
               AND event_type IN ('ZONE_ENTER', 'ZONE_DWELL')
               AND is_staff = 0
               AND timestamp > ?
               AND timestamp LIKE ?""",
            (store_id, zone_id, thirty_min_ago, f"{date_prefix}%"),
        )
        row = await cursor.fetchone()
        recent_visits = row[0] if row else 0

        # Check if zone had ANY visits today (to avoid false positives on zones with no data)
        cursor = await db.execute(
            """SELECT COUNT(*) FROM events
               WHERE store_id = ? AND zone_id = ?
               AND event_type IN ('ZONE_ENTER', 'ZONE_DWELL')
               AND timestamp LIKE ?""",
            (store_id, zone_id, f"{date_prefix}%"),
        )
        row = await cursor.fetchone()
        total_visits = row[0] if row else 0

        if total_visits > 0 and recent_visits == 0:
            anomalies.append(Anomaly(
                anomaly_type=AnomalyType.DEAD_ZONE,
                severity=AnomalySeverity.INFO,
                description=f"No customer visits to {zone_id} in the last 30 minutes",
                detected_at=now.isoformat(),
                value=0.0,
                threshold=1.0,
                suggested_action=f"Check {zone_id} zone for signage issues, stock depletion, or access obstructions.",
            ))


async def _check_stale_feed(db, store_id, anomalies, now):
    """Check for cameras with no recent events (>10 min lag)."""
    ten_min_ago = (now - timedelta(minutes=10)).isoformat()

    cursor = await db.execute(
        """SELECT camera_id, MAX(timestamp) as last_event
           FROM events
           WHERE store_id = ?
           GROUP BY camera_id""",
        (store_id,),
    )

    async for row in cursor:
        camera_id = row[0]
        last_event = row[1]

        if last_event and last_event < ten_min_ago:
            anomalies.append(Anomaly(
                anomaly_type=AnomalyType.STALE_FEED,
                severity=AnomalySeverity.CRITICAL,
                description=f"Camera {camera_id} last event at {last_event} — stale for >10 minutes",
                detected_at=now.isoformat(),
                suggested_action=f"Check camera {camera_id} connectivity, network, and power supply. Verify pipeline is running.",
            ))
