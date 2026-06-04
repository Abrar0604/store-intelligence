# System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     STORE INTELLIGENCE SYSTEM — ARCH                        │
│               Purplle Brigade Road Bangalore (STORE_BLR_002)                │
└─────────────────────────────────────────────────────────────────────────────┘

                    ┌──────────────────────────┐
                    │   CCTV Camera Feeds       │
                    │  ┌──────┐  ┌──────┐      │
                    │  │CAM 1 │  │CAM 2 │      │
                    │  │Floor │  │Floor │      │
                    │  └──┬───┘  └──┬───┘      │
                    │  ┌──┴───┐  ┌──┴───┐      │
                    │  │CAM 3 │  │CAM 5 │      │
                    │  │Entry │  │Bill  │      │
                    │  └──┬───┘  └──┬───┘      │
                    └─────┼────────┼───────────┘
                          │        │
                          ▼        ▼
              ┌───────────────────────────────┐
              │      DETECTION PIPELINE        │
              │   (pipeline/detect.py)         │
              │                               │
              │  ┌─────────────────────────┐  │
              │  │  YOLOv8n + ByteTrack    │  │  Frame → Detections + Track IDs
              │  │  (5 fps processing)     │  │
              │  └───────────┬─────────────┘  │
              │              │                │
              │  ┌───────────▼─────────────┐  │
              │  │  Zone Classifier         │  │  Centroid → Zone ID
              │  │  (ray-casting polygon)   │  │  (zones.py)
              │  └───────────┬─────────────┘  │
              │              │                │
              │  ┌───────────▼─────────────┐  │
              │  │  Staff Classifier        │  │  Clothing HSV + Persistence
              │  │  (dark torso + duration) │  │  (staff_classifier.py)
              │  └───────────┬─────────────┘  │
              │              │                │
              │  ┌───────────▼─────────────┐  │
              │  │  Visitor Tracker         │  │  State machine per track:
              │  │  (entry/exit/dwell/zone) │  │  ENTRY → ZONE_ENTER → DWELL →
              │  └───────────┬─────────────┘  │  ZONE_EXIT → BILLING → EXIT
              │              │                │
              │  ┌───────────▼─────────────┐  │
              │  │  Event Emitter          │  │  Structured JSONL events
              │  │  (UUID + session seq)   │  │  (emit.py)
              │  └───────────┬─────────────┘  │
              └──────────────┼────────────────┘
                             │
                  ┌──────────▼──────────┐
                  │  data/events.jsonl   │  Persisted event log
                  └──────────┬──────────┘
                             │
                    POST /events/ingest
                             │
              ┌──────────────▼────────────────┐
              │      INTELLIGENCE API           │
              │   (FastAPI — app/main.py)       │
              │                                │
              │  ┌──────────────────────────┐   │
              │  │  Event Ingestion         │   │  Idempotent batch insert
              │  │  (INSERT OR IGNORE)      │   │  up to 500 events/req
              │  └──────────┬───────────────┘   │
              │             │                   │
              │  ┌──────────▼───────────────┐   │
              │  │  SQLite Database          │   │  events + pos_transactions
              │  │  (aiosqlite — async)      │   │  Indexed on store_id,
              │  │                           │   │  visitor_id, event_type,
              │  │  ┌─────────┐ ┌──────────┐ │   │  timestamp, zone_id
              │  │  │ events  │ │pos_txns  │ │   │
              │  │  └─────────┘ └──────────┘ │   │
              │  └──────────┬───────────────┘   │
              │             │                   │
              │  ┌──────────▼───────────────┐   │
              │  │  Analytics Engine         │   │
              │  │  ├─ /metrics              │   │  Unique visitors, conversion
              │  │  ├─ /funnel               │   │  4-stage funnel with drop-off
              │  │  ├─ /heatmap              │   │  Zone visit intensity 0-100
              │  │  └─ /anomalies            │   │  4 anomaly detectors
              │  └──────────┬───────────────┘   │
              │             │                   │
              │  ┌──────────▼───────────────┐   │
              │  │  Production Features      │   │
              │  │  ├─ /health               │   │  DB + camera feed health
              │  │  ├─ /events/stream (SSE)  │   │  Real-time push to dashboard
              │  │  ├─ Structured JSON logs  │   │  trace_id per request
              │  │  └─ Global error handler  │   │  No stack trace leaks
              │  └──────────────────────────┘   │
              └──────────────┬──────────────────┘
                             │
                    GET /dashboard
                             │
              ┌──────────────▼──────────────────┐
              │       LIVE DASHBOARD              │
              │   (dashboard/index.html)          │
              │                                   │
              │  ┌───────────────────────────┐    │
              │  │  Metric Cards             │    │  Visitors, Conversion,
              │  │  (real-time counters)     │    │  Queue, Revenue, Abandon
              │  ├───────────────────────────┤    │
              │  │  Conversion Funnel        │    │  Entry → Zone → Billing
              │  │  (bar chart + dropoffs)   │    │  → Purchase
              │  ├───────────────────────────┤    │
              │  │  Zone Heatmap             │    │  Color-coded grid
              │  │  (intensity 0-100)        │    │  with dwell times
              │  ├───────────────────────────┤    │
              │  │  Active Anomalies         │    │  CRITICAL / WARN / INFO
              │  │  (with suggested actions) │    │  cards
              │  ├───────────────────────────┤    │
              │  │  Dwell Time Chart         │    │  Horizontal bars
              │  │  (per zone)               │    │  per zone
              │  ├───────────────────────────┤    │
              │  │  Health Status Bar        │    │  DB, uptime, event count
              │  └───────────────────────────┘    │
              │                                   │
              │  Updates via:                     │
              │  • SSE stream (/events/stream)    │
              │  • REST polling (3s interval)     │
              └───────────────────────────────────┘
```

---

## Data Flow

```
CCTV MP4 → YOLOv8n → ByteTrack → Zone/Staff Classify → Event Emitter
    → events.jsonl → POST /events/ingest → SQLite → Analytics Queries
    → REST JSON → Dashboard JS → DOM Render
```

### Event Lifecycle

1. **Detection** (5 fps) — YOLOv8n detects person bounding boxes in each frame
2. **Tracking** — ByteTrack assigns persistent IDs across frames (60-frame buffer = 12s)
3. **Classification** — Each detection is:
   - Zone-classified (point-in-polygon on centroid)
   - Staff-classified (clothing HSV + persistence)
4. **State machine** — `VisitorTracker` manages per-track state:
   - New track on entry camera → ENTRY event
   - Track enters a zone → ZONE_ENTER event
   - Track stays 30s → ZONE_DWELL event
   - Track moves to new zone → ZONE_EXIT + ZONE_ENTER
   - Track reaches billing → BILLING_QUEUE_JOIN + queue depth
   - Track lost → EXIT event (+ any pending ZONE_EXIT)
5. **Emission** — Events are written to JSONL and optionally POSTed to the API
6. **Ingestion** — API validates, deduplicates (by event_id), and stores in SQLite
7. **Analytics** — Endpoints aggregate events into metrics, funnels, heatmaps, anomalies
8. **Visualisation** — Dashboard polls API endpoints and renders real-time updates

---

## Camera Layout

```
            ┌────────────────────────────────────────────┐
            │              STORE_BLR_002                  │
            │         Purplle Brigade Road                │
            │                                            │
            │  ┌──────────┐           ┌──────────┐      │
            │  │          │           │          │      │
            │  │ CAM 1    │           │ CAM 2    │      │
            │  │ SKINCARE │           │ MAKEUP   │      │
            │  │          │           │          │      │
            │  └──────────┘           └──────────┘      │
            │                                            │
            │            ┌──────────┐                    │
            │            │ CAM 5    │                    │
            │            │ BILLING  │                    │
            │            └──────────┘                    │
            │                                            │
            │            ┌──────────┐                    │
            │            │ CAM 3    │                    │
            │            │ ENTRY    │                    │
            │            └──────────┘                    │
            │                ▼                           │
            │            ENTRANCE                        │
            └────────────────────────────────────────────┘

  CAM 4 (Storage Room) — EXCLUDED from processing
```

---

## Technology Stack

| Layer | Technology | Version |
|-------|-----------|---------|
| Detection | YOLOv8n (ultralytics) | 8.3.0 |
| Tracking | ByteTrack (built-in) | — |
| API | FastAPI | 0.115.0 |
| Database | SQLite via aiosqlite | 0.20.0 |
| Dashboard | Vanilla HTML/CSS/JS | — |
| Container | Docker + Compose | — |
| Testing | pytest + pytest-asyncio | 8.3.0 / 0.24.0 |
