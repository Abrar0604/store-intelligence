"""
Event ingestion endpoint — POST /events/ingest

Accepts batches of up to 500 events.
Idempotent by event_id (INSERT OR IGNORE).
Returns partial success on malformed events.

Handles events in both:
  1. Problem-statement format (uppercase types, event_id/visitor_id)
  2. Official sample format (lowercase types, id_token/track_id, demographics, queue details)
Events are normalised to canonical format before storage.
"""

import logging
from typing import List, Dict, Any
from fastapi import APIRouter, Request, HTTPException
from pydantic import ValidationError

from app.models import Event, IngestResponse, SAMPLE_TO_CANONICAL
from app.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_BATCH_SIZE = 500


def _normalize_event(event: Event) -> dict:
    """
    Normalize an Event model instance to the canonical database format.
    Handles both problem-statement and official sample field mappings.
    """
    # Resolve canonical event type
    canonical_type = event.get_canonical_event_type()

    # Resolve visitor_id from whichever field is populated
    visitor_id = event.get_visitor_id()

    # Resolve store_id
    store_id = event.get_store_id()

    # Resolve event_id
    event_id = event.get_event_id()

    # Resolve timestamp
    timestamp = event.get_timestamp()

    # Extract metadata fields (prefer top-level, fall back to metadata object)
    meta = event.metadata or {}
    zone_name = event.zone_name or (meta.zone_name if hasattr(meta, 'zone_name') else None)
    zone_type = event.zone_type or (meta.zone_type if hasattr(meta, 'zone_type') else None)
    is_revenue_zone = event.is_revenue_zone or (meta.is_revenue_zone if hasattr(meta, 'is_revenue_zone') else None)
    gender_pred = event.gender_pred or (meta.gender_pred if hasattr(meta, 'gender_pred') else None)
    age_pred = event.age_pred or (meta.age_pred if hasattr(meta, 'age_pred') else None)
    age_bucket = event.age_bucket or (meta.age_bucket if hasattr(meta, 'age_bucket') else None)
    group_id = event.group_id or (meta.group_id if hasattr(meta, 'group_id') else None)
    group_size = event.group_size or (meta.group_size if hasattr(meta, 'group_size') else None)
    zone_hotspot_x = event.zone_hotspot_x or (meta.zone_hotspot_x if hasattr(meta, 'zone_hotspot_x') else None)
    zone_hotspot_y = event.zone_hotspot_y or (meta.zone_hotspot_y if hasattr(meta, 'zone_hotspot_y') else None)
    queue_depth = (meta.queue_depth if hasattr(meta, 'queue_depth') else None)
    sku_zone = (meta.sku_zone if hasattr(meta, 'sku_zone') else None)
    session_seq = (meta.session_seq if hasattr(meta, 'session_seq') else None)

    # Queue event details
    queue_join_ts = event.queue_join_ts or (meta.queue_join_ts if hasattr(meta, 'queue_join_ts') else None)
    queue_served_ts = event.queue_served_ts or (meta.queue_served_ts if hasattr(meta, 'queue_served_ts') else None)
    queue_exit_ts = event.queue_exit_ts or (meta.queue_exit_ts if hasattr(meta, 'queue_exit_ts') else None)
    wait_seconds = event.wait_seconds or (meta.wait_seconds if hasattr(meta, 'wait_seconds') else None)
    queue_position_at_join = event.queue_position_at_join or (meta.queue_position_at_join if hasattr(meta, 'queue_position_at_join') else None)
    abandoned = event.abandoned if event.abandoned is not None else (meta.abandoned if hasattr(meta, 'abandoned') else None)

    return {
        "event_id": event_id,
        "store_id": store_id,
        "camera_id": event.camera_id,
        "visitor_id": visitor_id,
        "event_type": canonical_type,
        "timestamp": timestamp,
        "zone_id": event.zone_id,
        "zone_name": zone_name,
        "zone_type": zone_type,
        "is_revenue_zone": is_revenue_zone,
        "dwell_ms": event.dwell_ms,
        "is_staff": event.is_staff,
        "confidence": event.confidence,
        "queue_depth": queue_depth,
        "sku_zone": sku_zone,
        "session_seq": session_seq,
        "gender_pred": gender_pred,
        "age_pred": age_pred,
        "age_bucket": age_bucket,
        "group_id": group_id,
        "group_size": group_size,
        "zone_hotspot_x": zone_hotspot_x,
        "zone_hotspot_y": zone_hotspot_y,
        "queue_join_ts": queue_join_ts,
        "queue_served_ts": queue_served_ts,
        "queue_exit_ts": queue_exit_ts,
        "wait_seconds": wait_seconds,
        "queue_position_at_join": queue_position_at_join,
        "abandoned": abandoned,
    }


INSERT_SQL = """INSERT OR IGNORE INTO events 
   (event_id, store_id, camera_id, visitor_id, event_type, 
    timestamp, zone_id, zone_name, zone_type, is_revenue_zone,
    dwell_ms, is_staff, confidence,
    queue_depth, sku_zone, session_seq,
    gender_pred, age_pred, age_bucket,
    group_id, group_size,
    zone_hotspot_x, zone_hotspot_y,
    queue_join_ts, queue_served_ts, queue_exit_ts,
    wait_seconds, queue_position_at_join, abandoned)
   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""


@router.post("/events/ingest", response_model=IngestResponse)
async def ingest_events(request: Request):
    """
    Ingest a batch of behavioral events.
    
    - Accepts up to 500 events per request
    - Idempotent by event_id (duplicate events are silently ignored)
    - Partial success: valid events are accepted, invalid ones are reported
    - Accepts both problem-statement AND official sample event formats
    """
    trace_id = getattr(request.state, "trace_id", "unknown")
    
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail={
            "error": "invalid_json",
            "message": "Request body must be valid JSON",
            "trace_id": trace_id,
        })

    # Accept both list format and {events: [...]} format
    if isinstance(body, list):
        raw_events = body
    elif isinstance(body, dict) and "events" in body:
        raw_events = body["events"]
    else:
        raise HTTPException(status_code=400, detail={
            "error": "invalid_format",
            "message": "Expected a JSON array of events or {events: [...]}",
            "trace_id": trace_id,
        })

    if len(raw_events) > MAX_BATCH_SIZE:
        raise HTTPException(status_code=400, detail={
            "error": "batch_too_large",
            "message": f"Maximum batch size is {MAX_BATCH_SIZE}, got {len(raw_events)}",
            "trace_id": trace_id,
        })

    accepted = 0
    rejected = 0
    errors: List[Dict[str, Any]] = []

    try:
        db = await get_db()
    except Exception as e:
        logger.error(f"Database unavailable: {e}", extra={"trace_id": trace_id})
        raise HTTPException(status_code=503, detail={
            "error": "database_unavailable",
            "message": "Database is temporarily unavailable. Please retry.",
            "trace_id": trace_id,
        })

    for idx, raw_event in enumerate(raw_events):
        try:
            # Validate event against flexible schema
            event = Event(**raw_event)
            
            # Normalize to canonical format
            norm = _normalize_event(event)

            # Insert with idempotency (INSERT OR IGNORE on event_id)
            await db.execute(
                INSERT_SQL,
                (
                    norm["event_id"],
                    norm["store_id"],
                    norm["camera_id"],
                    norm["visitor_id"],
                    norm["event_type"],
                    norm["timestamp"],
                    norm["zone_id"],
                    norm["zone_name"],
                    norm["zone_type"],
                    norm["is_revenue_zone"],
                    norm["dwell_ms"],
                    norm["is_staff"],
                    norm["confidence"],
                    norm["queue_depth"],
                    norm["sku_zone"],
                    norm["session_seq"],
                    norm["gender_pred"],
                    norm["age_pred"],
                    norm["age_bucket"],
                    norm["group_id"],
                    norm["group_size"],
                    norm["zone_hotspot_x"],
                    norm["zone_hotspot_y"],
                    norm["queue_join_ts"],
                    norm["queue_served_ts"],
                    norm["queue_exit_ts"],
                    norm["wait_seconds"],
                    norm["queue_position_at_join"],
                    norm["abandoned"],
                ),
            )
            accepted += 1

        except ValidationError as e:
            rejected += 1
            errors.append({
                "index": idx,
                "event_id": raw_event.get("event_id", raw_event.get("queue_event_id", "unknown")),
                "error": "validation_error",
                "details": str(e),
            })
        except Exception as e:
            rejected += 1
            errors.append({
                "index": idx,
                "event_id": raw_event.get("event_id", raw_event.get("queue_event_id", "unknown")),
                "error": "processing_error",
                "details": str(e),
            })

    await db.commit()

    logger.info(
        f"Ingested {accepted}/{len(raw_events)} events ({rejected} rejected)",
        extra={
            "trace_id": trace_id,
            "event_count": accepted,
            "rejected_count": rejected,
        },
    )

    return IngestResponse(
        accepted=accepted,
        rejected=rejected,
        errors=errors[:20],  # Cap error details at 20
        total=len(raw_events),
    )
