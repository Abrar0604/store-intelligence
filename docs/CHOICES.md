# Engineering Choices & Trade-Offs

This document records every significant technical decision made during the Store Intelligence Challenge, including the alternatives considered, rationale, and known limitations.

---

## 1. Person Detection Model

| Option | Pros | Cons |
|--------|------|------|
| **YOLOv8n (chosen)** | 6.3 MB, ~160 fps on CPU, COCO-pretrained class 0 = person | Lower accuracy on small/occluded persons vs larger models |
| YOLOv8s | Better accuracy | 22 MB, 2× slower inference |
| YOLOv8m/l | Best accuracy | 50–100 MB, impractical for edge deployment or real-time on CPU |
| DETR / RT-DETR | Transformer-based, fewer false positives | Much heavier, slower startup, overkill for person-only detection |

**Decision:** YOLOv8n provides the best latency-to-accuracy ratio for single-class (person) detection at 5 fps processing rate. The 2.5-minute training clips have reasonable resolution (1920×1080) and the store environment is well-lit with minimal occlusion, making nano sufficient. We process at **5 fps** (every 6th frame from the 30 fps source) to cut compute 6× while retaining enough temporal resolution for zone transitions.

**Known limitation:** Nano may miss partially-occluded persons behind display shelves. Mitigation: ByteTrack's 12-second track buffer bridges brief occlusions.

---

## 2. Multi-Object Tracking

| Option | Pros | Cons |
|--------|------|------|
| **ByteTrack (chosen)** | Integrated in ultralytics, handles low-confidence detections, linear complexity | Cross-camera Re-ID not supported |
| DeepSORT | Deep appearance features, better Re-ID | 5× slower, requires separate ReID model |
| BoT-SORT | Slightly better than ByteTrack on MOT benchmarks | More complex, marginal gains for single-camera use |
| StrongSORT | State-of-the-art accuracy | Heavy inference cost, not needed for in-store analytics |

**Decision:** ByteTrack is the default tracker in `ultralytics` and is the best fit for our use case: single-camera tracking with stable lighting. Its "second association" step recovers low-confidence detections (common when customers partially face away), reducing ID switches. We do **not** need cross-camera Re-ID since each camera covers a distinct zone, and we use `visitor_id` hashing per camera rather than global identity.

**Track buffer:** Set to 60 frames (12 seconds at 5 fps). This handles brief occlusions (e.g., person walks behind a display) without generating false EXIT/ENTRY pairs.

---

## 3. Staff Classification

| Option | Pros | Cons |
|--------|------|------|
| **Dual heuristic: clothing + persistence (chosen)** | No training data needed, interpretable, robust | May misclassify customers in dark clothing |
| Fine-tuned classifier | High accuracy with training data | Requires labeled staff images, fragile to uniform changes |
| Pose/behavior model | Activity-based distinction | Complex, slow, overkill for binary classification |

**Decision:** Purplle staff wear **all-black uniforms**, which is a strong visual signal. We use two complementary heuristics:

1. **Clothing color analysis** — Extract the torso region (middle 40% height, center 60% width) of each bounding box, convert to HSV, and compute the ratio of pixels with Value < 60. If >55% of pixels are dark → staff candidate.

2. **Track persistence** — Staff are present throughout the clip; customers visit briefly. If a track is visible for >75% of total clip frames → staff candidate.

Either heuristic alone can flag staff. Both together increase confidence to 0.98.

**Thresholds:**
- `STAFF_DARK_THRESHOLD = 60` (HSV Value)
- `STAFF_DARK_RATIO = 0.55` (55% dark pixels)
- `STAFF_PERSISTENCE_RATIO = 0.75` (75% of clip duration)

**Known limitation:** A customer wearing a solid black outfit may be misclassified as staff. Mitigation: Persistence provides a strong second signal — a customer rarely appears in >75% of frames.

---

## 4. Zone Classification

| Option | Pros | Cons |
|--------|------|------|
| **Point-in-polygon with static regions (chosen)** | Zero additional compute, deterministic, easily adjustable | Requires manual polygon definition per camera |
| Semantic segmentation | Pixel-perfect zone boundaries | Heavy model, unnecessary for well-defined retail layouts |
| Grid-based cells | Simple | Poor fit for irregular store zones |

**Decision:** The store layout has clearly defined product zones visible in the CCTV frames. We define polygon regions per camera in `config.py` and use the standard **ray-casting** algorithm to test whether a bounding box centroid falls inside each zone. This runs in O(n) where n is the number of polygon vertices — negligible cost.

**Centroid choice:** We use the bottom-center of the bounding box (foot position) would be more accurate for ground-plane mapping, but centroid is more robust when bounding boxes are clipped at frame edges.

---

## 5. Entry/Exit Detection

| Option | Pros | Cons |
|--------|------|------|
| **Track lifecycle on entry camera (chosen)** | Simple, reliable for single-door stores | Cannot distinguish entry vs. exit direction |
| Virtual line crossing | Direction-aware, standard in retail analytics | Requires careful line placement, sensitive to camera angle |
| Door sensor fusion | Ground truth | Requires additional hardware not available |

**Decision:** CAM 3 covers the store entrance. When a new track appears on CAM_ENTRY_01, we emit ENTRY; when the track is lost (not seen for 12 seconds), we emit EXIT. This is simpler than virtual line crossing and works well because:
- The store has a single entrance/exit
- The entry camera's field of view is narrow enough that appearance ≈ entry
- Re-entry detection (5-minute window) handles the edge case of customers stepping outside briefly

**Known limitation:** Cannot distinguish a person who pauses at the doorway (without entering) from one who enters. Mitigation: DETECTION_CONFIDENCE threshold (0.35) filters out distant/partial detections.

---

## 6. Re-Entry Detection

**Window:** 300 seconds (5 minutes). If a visitor exits and a new track appears within 5 minutes, it is tagged as REENTRY rather than ENTRY.

**Rationale:** Beauty retail shoppers sometimes step outside to take a phone call or check a vehicle, then return. A 5-minute window captures >95% of such cases based on typical retail studies while avoiding false positives from truly new visitors.

**Limitation:** Without cross-camera Re-ID or facial features, we rely on temporal proximity. Two different people arriving close together could be incorrectly linked. Given Purplle's moderate foot traffic (~15–25 visitors per hour), this is acceptable.

---

## 7. Event Schema Design

**Session key:** `visitor_id` — a 6-character hex hash of `(track_id, camera_id, timestamp)`. This is deterministic and collision-resistant for the expected traffic volume.

**Dwell emission:** Every 30 seconds of continuous presence in a zone, a ZONE_DWELL event is emitted. This provides granular dwell data without flooding the event stream (at 5 fps, a person generates ~150 frames per 30 seconds — only 1 event is emitted).

**Idempotency:** Events use UUID v4 as event_id. The ingestion endpoint uses `INSERT OR IGNORE` on event_id, making replays safe.

---

## 8. Database

| Option | Pros | Cons |
|--------|------|------|
| **SQLite with aiosqlite (chosen)** | Zero infrastructure, single-file, async, fast for read-heavy analytics | Write concurrency limited, no horizontal scaling |
| PostgreSQL | Production-grade, concurrent writes, rich query features | Requires separate server, overkill for single-store demo |
| Redis + TimescaleDB | Best for time-series analytics | Significant infrastructure overhead |

**Decision:** SQLite is ideal for this challenge: single-store, single-writer (the pipeline), many-readers (the API). aiosqlite provides non-blocking access from FastAPI's async event loop. The database is a single file (`data/store_intelligence.db`) that can be backed up by copying.

**Indices:** We create indices on `(store_id)`, `(visitor_id)`, `(event_type)`, `(timestamp)`, `(zone_id)`, and `(store_id, event_type)` to ensure sub-10ms query latency for all analytics endpoints.

---

## 9. Conversion Rate Calculation

**Method:** A visitor is counted as "converted" if:
1. They have a ZONE_ENTER, ZONE_DWELL, or BILLING_QUEUE_JOIN event in the BILLING zone, AND
2. A POS transaction exists within a **5-minute window** before the visitor's billing zone event.

**Rationale:** This temporal correlation approach is the best available without receipt-level visitor linking. The 5-minute window is generous enough to capture the billing flow (scan → pay → receipt) while narrow enough to avoid false correlations during busy periods.

**Limitation:** If two customers are in the billing zone simultaneously and one makes a purchase, both may be counted as converted. At Purplle Brigade Road's typical traffic, simultaneous billing zone occupancy is rare.

---

## 10. Anomaly Detection Thresholds

| Anomaly | Threshold | Rationale |
|---------|-----------|-----------|
| BILLING_QUEUE_SPIKE | Current depth > 2× rolling average | Standard operations research: 2× mean queue length signals congestion |
| CONVERSION_DROP | Today's rate < 70% of baseline (35%) | Beauty retail averages 30-40% conversion; 70% of that = noticeable decline |
| DEAD_ZONE | 0 visits in 30 minutes | Any product zone with zero traffic during store hours indicates a problem |
| STALE_FEED | No events from camera for 10 minutes | Indicates pipeline failure or camera disconnection |

---

## 11. Dashboard Architecture

| Option | Pros | Cons |
|--------|------|------|
| **Vanilla HTML/CSS/JS + SSE (chosen)** | Zero build step, served directly by FastAPI, instant deployment | No component framework |
| React/Next.js | Component model, rich ecosystem | Requires npm build pipeline, overkill for single-page dashboard |
| Grafana | Pre-built dashboards, time-series native | Requires separate deployment, less customisable |

**Decision:** The dashboard is served by FastAPI as static files, eliminating any separate frontend build. SSE provides push-based updates (2-second interval) for real-time feel, with REST polling as fallback for detailed visualisations. The dark-mode glassmorphism design is implemented with pure CSS custom properties for easy theming.

---

## 12. Processing Rate: 5 FPS

**Rationale:** Beauty retail customers move slowly — browsing, reading labels, testing products. 5 fps captures all meaningful state transitions (zone entry/exit, dwell) while reducing compute by 6× versus native 30 fps. At 5 fps:
- A zone transition taking 1 second is captured in 5 frames (robust detection)
- A 2.5-minute clip produces ~750 frames instead of ~4500
- CPU inference with YOLOv8n at 5 fps runs comfortably in real-time
