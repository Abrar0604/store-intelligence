"""
Heatmap endpoint — GET /stores/{store_id}/heatmap

Returns zone visit frequency and average dwell time,
normalised to 0–100 scale. Includes data_confidence flag.
"""

import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Query

from app.models import HeatmapResponse, HeatmapZone
from app.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()

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

# All product zones (exclude non-browsing zones)
ALL_ZONES = [
    "SKINCARE_KOREAN", "SKINCARE_NATURAL", "SKINCARE_CLINICAL",
    "MAKEUP_FACE", "MAKEUP_LIPS_EYES", "FRAGRANCE", "ACCESSORIES", "FOH",
]


@router.get("/stores/{store_id}/heatmap", response_model=HeatmapResponse)
async def get_heatmap(
    store_id: str,
    date: str = Query(None, description="Date filter YYYY-MM-DD"),
):
    """
    Zone visit heatmap with normalised intensity (0–100).
    Includes data_confidence flag ('low' if <20 sessions).
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

    # ── Get total sessions for confidence flag ──────────────────────────
    cursor = await db.execute(
        """SELECT COUNT(DISTINCT visitor_id) FROM events
           WHERE store_id = ? AND is_staff = 0
           AND event_type IN ('ENTRY', 'REENTRY')
           AND timestamp LIKE ?""",
        (store_id, f"{date_prefix}%"),
    )
    row = await cursor.fetchone()
    total_sessions = row[0] if row else 0

    # ── Get zone visit data ─────────────────────────────────────────────
    cursor = await db.execute(
        """SELECT zone_id,
                  COUNT(DISTINCT visitor_id) as unique_visitors,
                  COUNT(*) as visit_count,
                  AVG(CASE WHEN dwell_ms > 0 THEN dwell_ms ELSE NULL END) as avg_dwell
           FROM events
           WHERE store_id = ? AND is_staff = 0
           AND event_type IN ('ZONE_ENTER', 'ZONE_DWELL', 'ZONE_EXIT')
           AND zone_id IS NOT NULL
           AND zone_id NOT IN ('ENTRY_EXIT')
           AND timestamp LIKE ?
           GROUP BY zone_id""",
        (store_id, f"{date_prefix}%"),
    )

    zone_data = {}
    max_visits = 0
    async for row in cursor:
        zone_id = row[0]
        visit_count = row[2]
        avg_dwell = row[3] or 0
        zone_data[zone_id] = {
            "visit_count": visit_count,
            "avg_dwell_ms": round(avg_dwell, 2),
        }
        max_visits = max(max_visits, visit_count)

    # ── Build heatmap with normalised intensity ─────────────────────────
    zones = []
    for zone_id in ALL_ZONES:
        data = zone_data.get(zone_id, {"visit_count": 0, "avg_dwell_ms": 0})
        
        # Normalise to 0–100
        intensity = 0.0
        if max_visits > 0:
            intensity = round((data["visit_count"] / max_visits) * 100, 2)

        confidence = "high" if total_sessions >= 20 else "low"

        zones.append(HeatmapZone(
            zone_id=zone_id,
            zone_name=ZONE_NAMES.get(zone_id, zone_id),
            visit_count=data["visit_count"],
            avg_dwell_ms=data["avg_dwell_ms"],
            intensity=intensity,
            data_confidence=confidence,
        ))

    return HeatmapResponse(
        store_id=store_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        zones=zones,
    )
