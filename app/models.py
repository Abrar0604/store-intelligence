"""
Pydantic models for the Store Intelligence API.
Covers event ingestion, metrics responses, funnel stages, heatmap,
anomalies, and health status.

Accepts events in both:
  1. Problem-statement format (uppercase ENTRY, ZONE_ENTER, etc.)
  2. Official sample format (lowercase entry, zone_entered, queue_completed, etc.)
Normalisation happens at the ingestion layer.
"""

from __future__ import annotations
from datetime import datetime
from typing import Optional, List, Dict, Any, Union
from enum import Enum
from pydantic import BaseModel, Field, field_validator, model_validator
import uuid


# ─── Event Type Mapping ──────────────────────────────────────────────────────
# Maps official sample format → our canonical format
SAMPLE_TO_CANONICAL = {
    "entry": "ENTRY",
    "exit": "EXIT",
    "zone_entered": "ZONE_ENTER",
    "zone_exited": "ZONE_EXIT",
    "zone_dwell": "ZONE_DWELL",
    "queue_completed": "BILLING_QUEUE_JOIN",
    "queue_abandoned": "BILLING_QUEUE_ABANDON",
    "reentry": "REENTRY",
}

VALID_EVENT_TYPES = {
    # Canonical (uppercase)
    "ENTRY", "EXIT", "ZONE_ENTER", "ZONE_EXIT", "ZONE_DWELL",
    "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "REENTRY",
    # Sample format (lowercase)
    "entry", "exit", "zone_entered", "zone_exited", "zone_dwell",
    "queue_completed", "queue_abandoned", "reentry",
}


class EventType(str, Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    ZONE_ENTER = "ZONE_ENTER"
    ZONE_EXIT = "ZONE_EXIT"
    ZONE_DWELL = "ZONE_DWELL"
    BILLING_QUEUE_JOIN = "BILLING_QUEUE_JOIN"
    BILLING_QUEUE_ABANDON = "BILLING_QUEUE_ABANDON"
    REENTRY = "REENTRY"


class AnomalySeverity(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


class AnomalyType(str, Enum):
    BILLING_QUEUE_SPIKE = "BILLING_QUEUE_SPIKE"
    CONVERSION_DROP = "CONVERSION_DROP"
    DEAD_ZONE = "DEAD_ZONE"
    STALE_FEED = "STALE_FEED"


# ─── Event Models ──────────────────────────────────────────────────────────

class EventMetadata(BaseModel):
    """Extended metadata supporting both problem-statement and sample fields."""
    queue_depth: Optional[int] = None
    sku_zone: Optional[str] = None
    session_seq: Optional[int] = None
    # Extended fields from official sample
    zone_name: Optional[str] = None
    zone_type: Optional[str] = None
    is_revenue_zone: Optional[str] = None
    gender_pred: Optional[str] = None
    age_pred: Optional[int] = None
    age_bucket: Optional[str] = None
    group_id: Optional[str] = None
    group_size: Optional[int] = None
    zone_hotspot_x: Optional[float] = None
    zone_hotspot_y: Optional[float] = None
    # Queue event details from official sample
    queue_join_ts: Optional[str] = None
    queue_served_ts: Optional[str] = None
    queue_exit_ts: Optional[str] = None
    wait_seconds: Optional[int] = None
    queue_position_at_join: Optional[int] = None
    abandoned: Optional[bool] = None


class Event(BaseModel):
    """
    Flexible event model that accepts events in BOTH the problem-statement
    schema AND the official sample schema. Fields from the sample format are
    mapped to canonical fields at ingestion time.
    """
    # Primary identity — accept either event_id or queue_event_id
    event_id: Optional[str] = Field(None, description="UUID v4 — globally unique")
    # Store ID — accept either store_id or store_code
    store_id: Optional[str] = Field(None, description="Store identifier")
    store_code: Optional[str] = Field(None, description="Store code (sample format)")
    # Camera
    camera_id: str = Field(..., description="Camera that produced this event")
    # Visitor identity — accept visitor_id, id_token, or track_id
    visitor_id: Optional[str] = Field(None, description="Re-ID token")
    id_token: Optional[str] = Field(None, description="ID token (sample entry/exit format)")
    track_id: Optional[Union[int, str]] = Field(None, description="Track ID (sample zone format)")
    # Event type — accepts both uppercase canonical and lowercase sample format
    event_type: str = Field(..., description="Event type")
    # Timestamp — accept timestamp, event_timestamp, or event_time
    timestamp: Optional[str] = Field(None, description="ISO-8601 UTC timestamp")
    event_timestamp: Optional[str] = Field(None, description="Timestamp (sample entry/exit)")
    event_time: Optional[str] = Field(None, description="Timestamp (sample zone events)")
    # Zone
    zone_id: Optional[str] = Field(None, description="Zone ID")
    zone_name: Optional[str] = Field(None, description="Zone display name")
    zone_type: Optional[str] = Field(None, description="Zone type (SHELF/DISPLAY/BILLING)")
    is_revenue_zone: Optional[str] = Field(None, description="Yes/No")
    # Metrics
    dwell_ms: int = Field(0, ge=0, description="Duration in milliseconds")
    is_staff: bool = Field(False, description="True if person is staff")
    confidence: float = Field(0.5, ge=0.0, le=1.0, description="Detection confidence")
    # Demographics (from sample)
    gender_pred: Optional[str] = Field(None, description="M/F gender prediction")
    age_pred: Optional[int] = Field(None, description="Predicted age")
    age_bucket: Optional[str] = Field(None, description="Age bucket e.g. 25-34")
    is_face_hidden: Optional[bool] = Field(None, description="Face visibility")
    # Groups (from sample)
    group_id: Optional[str] = Field(None, description="Group identifier")
    group_size: Optional[int] = Field(None, description="Number of people in group")
    # Hotspot coordinates (from sample)
    zone_hotspot_x: Optional[float] = Field(None, description="Zone hotspot X coordinate")
    zone_hotspot_y: Optional[float] = Field(None, description="Zone hotspot Y coordinate")
    # Queue details (from sample)
    queue_event_id: Optional[str] = Field(None, description="Queue event UUID (sample)")
    queue_join_ts: Optional[str] = Field(None)
    queue_served_ts: Optional[str] = Field(None)
    queue_exit_ts: Optional[str] = Field(None)
    wait_seconds: Optional[int] = Field(None)
    queue_position_at_join: Optional[int] = Field(None)
    abandoned: Optional[bool] = Field(None)
    # Legacy metadata
    metadata: Optional[EventMetadata] = Field(default_factory=EventMetadata)

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, v):
        if v not in VALID_EVENT_TYPES:
            raise ValueError(f"Invalid event_type: {v}. Must be one of {VALID_EVENT_TYPES}")
        return v

    def get_canonical_event_type(self) -> str:
        """Return the canonical uppercase event type."""
        return SAMPLE_TO_CANONICAL.get(self.event_type, self.event_type.upper())

    def get_store_id(self) -> str:
        """Return store_id from whichever field is populated."""
        return self.store_id or self.store_code or "UNKNOWN"

    def get_visitor_id(self) -> str:
        """Return visitor_id from whichever field is populated."""
        return self.visitor_id or self.id_token or (str(self.track_id) if self.track_id is not None else "UNKNOWN")

    def get_event_id(self) -> str:
        """Return event_id, generating one if not provided."""
        return self.event_id or self.queue_event_id or str(uuid.uuid4())

    def get_timestamp(self) -> str:
        """Return timestamp from whichever field is populated."""
        ts = self.timestamp or self.event_timestamp or self.event_time or self.queue_join_ts
        if ts is None:
            return datetime.utcnow().isoformat() + "Z"
        return ts


class IngestRequest(BaseModel):
    """Wrapper for batch event ingestion. Also accepts raw list."""
    events: List[Event] = Field(default_factory=list)


class IngestResponse(BaseModel):
    accepted: int
    rejected: int
    errors: List[Dict[str, Any]] = Field(default_factory=list)
    total: int


# ─── Metrics Models ───────────────────────────────────────────────────────

class ZoneDwell(BaseModel):
    zone_id: str
    zone_name: str
    avg_dwell_ms: float
    visit_count: int


class MetricsResponse(BaseModel):
    store_id: str
    timestamp: str
    unique_visitors: int
    total_entries: int
    total_exits: int
    conversion_rate: float = Field(ge=0.0, le=1.0)
    avg_dwell_by_zone: List[ZoneDwell]
    current_queue_depth: int
    abandonment_rate: float = Field(ge=0.0, le=1.0)
    total_transactions: int
    total_revenue: float


# ─── Funnel Models ────────────────────────────────────────────────────────

class FunnelStage(BaseModel):
    stage: str
    count: int
    percentage: float = Field(ge=0.0, le=100.0)
    drop_off_pct: float = Field(ge=0.0, le=100.0)


class FunnelResponse(BaseModel):
    store_id: str
    timestamp: str
    session_count: int
    stages: List[FunnelStage]


# ─── Heatmap Models ──────────────────────────────────────────────────────

class HeatmapZone(BaseModel):
    zone_id: str
    zone_name: str
    visit_count: int
    avg_dwell_ms: float
    intensity: float = Field(ge=0.0, le=100.0, description="Normalised 0–100")
    data_confidence: str = Field("high", description="'high' if ≥20 sessions, else 'low'")


class HeatmapResponse(BaseModel):
    store_id: str
    timestamp: str
    zones: List[HeatmapZone]


# ─── Anomaly Models ──────────────────────────────────────────────────────

class Anomaly(BaseModel):
    anomaly_type: AnomalyType
    severity: AnomalySeverity
    description: str
    detected_at: str
    value: Optional[float] = None
    threshold: Optional[float] = None
    suggested_action: str


class AnomalyResponse(BaseModel):
    store_id: str
    timestamp: str
    active_anomalies: List[Anomaly]


# ─── Health Models ────────────────────────────────────────────────────────

class StoreHealth(BaseModel):
    store_id: str
    last_event_timestamp: Optional[str] = None
    event_count: int = 0
    status: str = "OK"
    warnings: List[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str = "healthy"
    timestamp: str
    uptime_seconds: float
    database: str = "connected"
    stores: List[StoreHealth] = Field(default_factory=list)
