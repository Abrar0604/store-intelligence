"""
Event emission — constructs structured events conforming to the required schema.
Handles event ID generation, timestamp derivation, and session sequencing.

Supports both the problem-statement schema (uppercase event types, event_id/visitor_id)
and the official sample schema (lowercase event types, id_token/track_id).
The pipeline emits in the problem-statement format; the API normalizes on ingestion.
"""

import uuid
import json
import numpy as np
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
from pathlib import Path


def _to_python(val):
    """Convert numpy types to native Python types for JSON serialization."""
    if isinstance(val, (np.bool_,)):
        return bool(val)
    if isinstance(val, (np.integer,)):
        return int(val)
    if isinstance(val, (np.floating,)):
        return float(val)
    if isinstance(val, np.ndarray):
        return val.tolist()
    return val


class _NumpySafeEncoder(json.JSONEncoder):
    """JSON encoder that handles numpy types transparently."""
    def default(self, obj):
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


class EventEmitter:
    """
    Constructs and emits structured behavioral events.
    Maintains session state for visitor_id sequencing.
    """

    def __init__(self, store_id: str, output_path: Path):
        self.store_id = store_id
        self.output_path = output_path
        self.events: List[Dict[str, Any]] = []
        self.session_counters: Dict[str, int] = {}  # visitor_id → next session_seq

    def _next_session_seq(self, visitor_id: str) -> int:
        """Get and increment session sequence number for a visitor."""
        seq = self.session_counters.get(visitor_id, 0) + 1
        self.session_counters[visitor_id] = seq
        return seq

    def create_event(
        self,
        camera_id: str,
        visitor_id: str,
        event_type: str,
        timestamp: datetime,
        zone_id: Optional[str] = None,
        zone_name: Optional[str] = None,
        zone_type: Optional[str] = None,
        is_revenue_zone: Optional[str] = None,
        dwell_ms: int = 0,
        is_staff: bool = False,
        confidence: float = 0.5,
        queue_depth: Optional[int] = None,
        sku_zone: Optional[str] = None,
        gender_pred: Optional[str] = None,
        age_pred: Optional[int] = None,
        age_bucket: Optional[str] = None,
        group_id: Optional[str] = None,
        group_size: Optional[int] = None,
        zone_hotspot_x: Optional[float] = None,
        zone_hotspot_y: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Create a single structured event conforming to the required schema.
        All numpy types are cast to Python natives to prevent JSON serialization errors.
        """
        session_seq = self._next_session_seq(visitor_id)

        # Cast all values to Python natives to prevent numpy serialization issues
        event = {
            "event_id": str(uuid.uuid4()),
            "store_id": str(self.store_id),
            "camera_id": str(camera_id),
            "visitor_id": str(visitor_id),
            "event_type": str(event_type),
            "timestamp": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ") if isinstance(timestamp, datetime) else str(timestamp),
            "zone_id": str(zone_id) if zone_id else None,
            "dwell_ms": int(_to_python(dwell_ms)),
            "is_staff": bool(_to_python(is_staff)),
            "confidence": round(float(_to_python(confidence)), 3),
            "metadata": {
                "queue_depth": int(_to_python(queue_depth)) if queue_depth is not None else None,
                "sku_zone": str(sku_zone) if sku_zone else None,
                "session_seq": int(session_seq),
                "zone_name": str(zone_name) if zone_name else None,
                "zone_type": str(zone_type) if zone_type else None,
                "is_revenue_zone": str(is_revenue_zone) if is_revenue_zone else None,
                "gender_pred": str(gender_pred) if gender_pred else None,
                "age_pred": int(_to_python(age_pred)) if age_pred is not None else None,
                "age_bucket": str(age_bucket) if age_bucket else None,
                "group_id": str(group_id) if group_id else None,
                "group_size": int(_to_python(group_size)) if group_size is not None else None,
                "zone_hotspot_x": round(float(_to_python(zone_hotspot_x)), 1) if zone_hotspot_x is not None else None,
                "zone_hotspot_y": round(float(_to_python(zone_hotspot_y)), 1) if zone_hotspot_y is not None else None,
            },
        }
        self.events.append(event)
        return event

    def emit_entry(self, camera_id, visitor_id, timestamp, confidence, is_staff=False,
                   gender_pred=None, age_pred=None, age_bucket=None, group_id=None, group_size=None):
        return self.create_event(
            camera_id=camera_id,
            visitor_id=visitor_id,
            event_type="ENTRY",
            timestamp=timestamp,
            confidence=confidence,
            is_staff=is_staff,
            gender_pred=gender_pred,
            age_pred=age_pred,
            age_bucket=age_bucket,
            group_id=group_id,
            group_size=group_size,
        )

    def emit_exit(self, camera_id, visitor_id, timestamp, confidence, is_staff=False,
                  gender_pred=None, age_pred=None, age_bucket=None, group_id=None, group_size=None):
        return self.create_event(
            camera_id=camera_id,
            visitor_id=visitor_id,
            event_type="EXIT",
            timestamp=timestamp,
            confidence=confidence,
            is_staff=is_staff,
            gender_pred=gender_pred,
            age_pred=age_pred,
            age_bucket=age_bucket,
            group_id=group_id,
            group_size=group_size,
        )

    def emit_reentry(self, camera_id, visitor_id, timestamp, confidence, is_staff=False):
        return self.create_event(
            camera_id=camera_id,
            visitor_id=visitor_id,
            event_type="REENTRY",
            timestamp=timestamp,
            confidence=confidence,
            is_staff=is_staff,
        )

    def emit_zone_enter(
        self, camera_id, visitor_id, timestamp, zone_id, confidence,
        sku_zone=None, is_staff=False, zone_name=None, zone_type=None,
        is_revenue_zone=None, zone_hotspot_x=None, zone_hotspot_y=None,
        gender_pred=None, age_pred=None, age_bucket=None,
    ):
        return self.create_event(
            camera_id=camera_id,
            visitor_id=visitor_id,
            event_type="ZONE_ENTER",
            timestamp=timestamp,
            zone_id=zone_id,
            zone_name=zone_name,
            zone_type=zone_type,
            is_revenue_zone=is_revenue_zone,
            confidence=confidence,
            sku_zone=sku_zone,
            is_staff=is_staff,
            zone_hotspot_x=zone_hotspot_x,
            zone_hotspot_y=zone_hotspot_y,
            gender_pred=gender_pred,
            age_pred=age_pred,
            age_bucket=age_bucket,
        )

    def emit_zone_exit(
        self, camera_id, visitor_id, timestamp, zone_id, dwell_ms, confidence,
        sku_zone=None, is_staff=False, zone_name=None, zone_type=None,
        is_revenue_zone=None, zone_hotspot_x=None, zone_hotspot_y=None,
        gender_pred=None, age_pred=None, age_bucket=None,
    ):
        return self.create_event(
            camera_id=camera_id,
            visitor_id=visitor_id,
            event_type="ZONE_EXIT",
            timestamp=timestamp,
            zone_id=zone_id,
            zone_name=zone_name,
            zone_type=zone_type,
            is_revenue_zone=is_revenue_zone,
            dwell_ms=dwell_ms,
            confidence=confidence,
            sku_zone=sku_zone,
            is_staff=is_staff,
            zone_hotspot_x=zone_hotspot_x,
            zone_hotspot_y=zone_hotspot_y,
            gender_pred=gender_pred,
            age_pred=age_pred,
            age_bucket=age_bucket,
        )

    def emit_zone_dwell(
        self, camera_id, visitor_id, timestamp, zone_id, dwell_ms, confidence,
        sku_zone=None, is_staff=False, zone_name=None, zone_type=None,
        is_revenue_zone=None, zone_hotspot_x=None, zone_hotspot_y=None,
    ):
        return self.create_event(
            camera_id=camera_id,
            visitor_id=visitor_id,
            event_type="ZONE_DWELL",
            timestamp=timestamp,
            zone_id=zone_id,
            zone_name=zone_name,
            zone_type=zone_type,
            is_revenue_zone=is_revenue_zone,
            dwell_ms=dwell_ms,
            confidence=confidence,
            sku_zone=sku_zone,
            is_staff=is_staff,
            zone_hotspot_x=zone_hotspot_x,
            zone_hotspot_y=zone_hotspot_y,
        )

    def emit_billing_queue_join(
        self, camera_id, visitor_id, timestamp, confidence, queue_depth, is_staff=False
    ):
        return self.create_event(
            camera_id=camera_id,
            visitor_id=visitor_id,
            event_type="BILLING_QUEUE_JOIN",
            timestamp=timestamp,
            zone_id="BILLING",
            confidence=confidence,
            queue_depth=queue_depth,
            sku_zone="BILLING",
            is_staff=is_staff,
            zone_type="BILLING",
            is_revenue_zone="Yes",
        )

    def emit_billing_queue_abandon(
        self, camera_id, visitor_id, timestamp, confidence, dwell_ms=0, is_staff=False
    ):
        return self.create_event(
            camera_id=camera_id,
            visitor_id=visitor_id,
            event_type="BILLING_QUEUE_ABANDON",
            timestamp=timestamp,
            zone_id="BILLING",
            dwell_ms=dwell_ms,
            confidence=confidence,
            sku_zone="BILLING",
            is_staff=is_staff,
            zone_type="BILLING",
            is_revenue_zone="Yes",
        )

    def flush_to_file(self) -> int:
        """Write all accumulated events to the output JSONL file.
        Uses NumpySafeEncoder to handle any remaining numpy types."""
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.output_path, "w") as f:
            for event in self.events:
                f.write(json.dumps(event, cls=_NumpySafeEncoder) + "\n")
        count = len(self.events)
        print(f"[emit] Wrote {count} events to {self.output_path}")
        return count

    def get_events(self) -> List[Dict[str, Any]]:
        """Return all accumulated events."""
        return self.events.copy()
