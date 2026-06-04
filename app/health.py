"""
Health endpoint — GET /health

Reports service status, database connectivity, last event timestamps,
and STALE_FEED warnings for cameras with >10 min lag.
"""

import time
import logging
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter

from app.models import HealthResponse, StoreHealth
from app.database import get_db, check_db_health

logger = logging.getLogger(__name__)
router = APIRouter()

# Track service start time for uptime
_start_time = time.monotonic()


@router.get("/health", response_model=HealthResponse)
async def get_health():
    """
    Service health check endpoint.
    
    Reports:
    - Service status and uptime
    - Database connectivity
    - Last event timestamp per store
    - STALE_FEED warnings if >10 min since last event
    """
    now = datetime.now(timezone.utc)
    uptime = time.monotonic() - _start_time
    
    # Check database health
    db_healthy = await check_db_health()
    db_status = "connected" if db_healthy else "disconnected"
    overall_status = "healthy" if db_healthy else "degraded"

    stores = []
    
    if db_healthy:
        try:
            db = await get_db()
            
            # Get per-store health
            cursor = await db.execute(
                """SELECT store_id, 
                          MAX(timestamp) as last_event,
                          COUNT(*) as event_count
                   FROM events
                   GROUP BY store_id"""
            )
            
            async for row in cursor:
                store_id = row[0]
                last_event = row[1]
                event_count = row[2]
                
                warnings = []
                status = "OK"
                
                # Check for stale feed
                if last_event:
                    try:
                        last_ts = datetime.fromisoformat(
                            last_event.replace("Z", "+00:00")
                        )
                        lag = (now - last_ts).total_seconds()
                        if lag > 600:  # 10 minutes
                            warnings.append(
                                f"STALE_FEED: Last event {int(lag)}s ago (>{600}s threshold)"
                            )
                            status = "WARN"
                    except (ValueError, TypeError):
                        pass
                
                stores.append(StoreHealth(
                    store_id=store_id,
                    last_event_timestamp=last_event,
                    event_count=event_count,
                    status=status,
                    warnings=warnings,
                ))
                
        except Exception as e:
            logger.error(f"Error fetching store health: {e}")
            overall_status = "degraded"

    return HealthResponse(
        status=overall_status,
        timestamp=now.isoformat(),
        uptime_seconds=round(uptime, 2),
        database=db_status,
        stores=stores,
    )
