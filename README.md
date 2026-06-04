# Store Intelligence System

> Real-time retail analytics from CCTV footage — from raw video to live store metrics.

An end-to-end pipeline that processes CCTV camera feeds using computer vision (YOLOv8 + ByteTrack), generates structured behavioral events, serves real-time analytics through a REST API, and visualises insights on a live dashboard.

**Live Dashboard URL:** `http://localhost:8000/dashboard`

---

## Quick Start

### 1. Start the API (Docker)

```bash
git clone <repo-url> && cd store-intelligence
docker compose up -d
```

The API starts at `http://localhost:8000`. The dashboard is accessible at **http://localhost:8000/dashboard**.

### 2. Run the Detection Pipeline

The detection pipeline processes CCTV clips and emits structured events:

```bash
# Process Store 1 clips (default)
./pipeline/run.sh

# Or run directly with Python:
python -m pipeline.detect --store store1 --ingest

# Process Store 2 clips:
python -m pipeline.detect --store store2 --ingest
```

**What happens:**
1. YOLOv8n processes each camera's video at 5 fps
2. ByteTrack assigns persistent person IDs across frames
3. Zone classification maps each person to store zones
4. Staff are identified by dark clothing + persistence
5. Structured events are written to `data/events.jsonl`
6. If `--ingest` is passed, events are POSTed to `POST /events/ingest`

**Output:** `data/events.jsonl` — one JSON event per line, following the required schema.

### 3. Verify

```bash
# Health check
curl http://localhost:8000/health

# Store metrics
curl http://localhost:8000/stores/STORE_BLR_002/metrics

# Conversion funnel
curl http://localhost:8000/stores/STORE_BLR_002/funnel

# Zone heatmap
curl http://localhost:8000/stores/STORE_BLR_002/heatmap

# Active anomalies
curl http://localhost:8000/stores/STORE_BLR_002/anomalies
```

---

## Local Development (Without Docker)

```bash
pip install -r requirements.txt

# Start the API server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# In another terminal — run detection
python -m pipeline.detect --store store1 --ingest
```

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/events/ingest` | Batch event ingestion (up to 500, idempotent) |
| GET | `/stores/{id}/metrics` | Real-time store metrics |
| GET | `/stores/{id}/funnel` | Conversion funnel (Entry → Zone → Billing → Purchase) |
| GET | `/stores/{id}/heatmap` | Zone visit heatmap (intensity 0–100) |
| GET | `/stores/{id}/anomalies` | Active operational anomalies |
| GET | `/health` | Service health check |
| GET | `/events/stream` | SSE stream for real-time updates |
| GET | `/dashboard` | Live web dashboard |

### Event Ingestion — Dual Format Support

The API accepts events in **two formats**:

**Problem-statement format:**
```json
{
  "event_id": "uuid-v4",
  "store_id": "STORE_BLR_002",
  "camera_id": "CAM_ENTRY_01",
  "visitor_id": "VIS_c8a2f1",
  "event_type": "ENTRY",
  "timestamp": "2026-04-10T14:22:10Z",
  "zone_id": null,
  "dwell_ms": 0,
  "is_staff": false,
  "confidence": 0.91,
  "metadata": {"queue_depth": null, "sku_zone": null, "session_seq": 1}
}
```

**Official sample format:**
```json
{
  "event_type": "entry",
  "id_token": "ID_60001",
  "store_code": "store_1076",
  "camera_id": "cam1",
  "event_timestamp": "2026-03-08T18:10:05.120000",
  "is_staff": false,
  "gender_pred": "F",
  "age_pred": 28,
  "age_bucket": "25-34"
}
```

Both are normalised to canonical format on ingestion.

---

## Camera Mapping

### Store 1
| Camera | File | Type | Covers |
|--------|------|------|--------|
| CAM_ENTRY_01 | CAM 3 - entry.mp4 | Entry | Store entrance |
| CAM_FLOOR_01 | CAM 1 - zone.mp4 | Floor | Skincare zones |
| CAM_FLOOR_02 | CAM 2 - zone.mp4 | Floor | Makeup zones |
| CAM_BILLING_01 | CAM 5 - billing.mp4 | Billing | Cash counter |

### Store 2
| Camera | File | Type | Covers |
|--------|------|------|--------|
| S2_CAM_ENTRY_01 | entry 1.mp4 | Entry | Main entrance |
| S2_CAM_ENTRY_02 | entry 2.mp4 | Entry | Side entrance |
| S2_CAM_FLOOR_01 | zone.mp4 | Floor | Wall units + gondolas |
| S2_CAM_BILLING_01 | billing_area.mp4 | Billing | Cash counter |

---

## Project Structure

```
store-intelligence/
├── pipeline/                     # Detection Pipeline (Part A)
│   ├── detect.py                 # Main: YOLO → Track → Classify → Emit
│   ├── tracker.py                # ByteTrack state management, entry/exit
│   ├── zones.py                  # Point-in-polygon zone classification
│   ├── staff_classifier.py       # Clothing color + persistence heuristics
│   ├── emit.py                   # Event construction + JSONL output
│   ├── config.py                 # Camera configs, zone polygons, thresholds
│   └── run.sh                    # One-command pipeline runner
├── app/                          # Intelligence API (Part B)
│   ├── main.py                   # FastAPI entrypoint + SSE
│   ├── models.py                 # Pydantic schemas (dual-format)
│   ├── database.py               # SQLite + async, POS loader
│   ├── ingestion.py              # Ingest + normalise + dedup
│   ├── metrics.py                # /metrics computation
│   ├── funnel.py                 # /funnel with session logic
│   ├── heatmap.py                # /heatmap zone intensity
│   ├── anomalies.py              # 4 anomaly detectors
│   ├── health.py                 # /health check
│   └── logging_config.py         # Structured JSON logging
├── dashboard/                    # Live Dashboard (Part E)
│   ├── index.html
│   ├── style.css
│   └── app.js
├── tests/                        # Test suite (Part C)
│   ├── conftest.py               # Shared fixtures
│   ├── test_ingestion.py         # 12 tests: batch, idempotency, sample format
│   ├── test_metrics.py           # 5 tests: empty store, staff exclusion
│   ├── test_funnel.py            # 5 tests: stages, monotonic, drop-offs
│   ├── test_anomalies.py         # 6 tests: all anomaly types
│   └── test_pipeline.py          # 10 tests: zones, staff, emitter, numpy
├── docs/                         # Documentation (Part D)
│   ├── DESIGN.md                 # Architecture + AI-Assisted Decisions
│   └── CHOICES.md                # 12 engineering decisions with trade-offs
├── data/
│   ├── pos_transactions.csv      # POS transaction data
│   └── store_layout.json         # Zone definitions
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── README.md
```

---

## Testing

```bash
python -m pytest tests/ -v --cov=app --cov=pipeline --cov-report=term-missing
```

---

## Documentation

- **[DESIGN.md](docs/DESIGN.md)** — System architecture, AI-assisted decisions, production readiness
- **[CHOICES.md](docs/CHOICES.md)** — Engineering decisions: model selection, schema design, API decisions
