"""
Main detection pipeline — processes CCTV clips using YOLOv8 + ByteTrack.

Pipeline flow:
  1. Load video clip for each camera
  2. Run YOLOv8 person detection at PROCESS_FPS
  3. Track detections with ByteTrack
  4. Classify zones and staff
  5. Generate structured behavioral events
  6. Write events to JSONL

Usage:
  python -m pipeline.detect                         # process all Store 1 clips
  python -m pipeline.detect --store store2           # process Store 2 clips
  python -m pipeline.detect --camera CAM_ENTRY_01    # process one camera
  python -m pipeline.detect --realtime               # simulated real-time mode
  python -m pipeline.detect --store store1 --ingest  # process + send to API
"""

import sys
import os
import json
import time
import argparse
import logging
from pathlib import Path
from datetime import datetime, timedelta, timezone

import cv2
import numpy as np

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.config import (
    STORE_ID,
    CLIPS_DIR,
    EVENTS_OUTPUT,
    CAMERA_CONFIGS,
    ACTIVE_CAMERAS,
    YOLO_MODEL,
    DETECTION_CONFIDENCE,
    PERSON_CLASS_ID,
    PROCESS_FPS,
    IOU_THRESHOLD,
    TRACK_BUFFER,
    MATCH_THRESHOLD,
    STORE_CONFIGS,
)
from pipeline.zones import classify_zone, get_sku_zone
from pipeline.staff_classifier import classify_staff
from pipeline.tracker import VisitorTracker
from pipeline.emit import EventEmitter, _NumpySafeEncoder

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def process_camera(
    camera_id: str,
    emitter: EventEmitter,
    realtime: bool = False,
    api_url: str = None,
) -> int:
    """
    Process a single camera clip through the detection pipeline.
    
    Returns the number of events generated.
    """
    cam_config = CAMERA_CONFIGS.get(camera_id)
    if not cam_config:
        logger.error(f"Unknown camera: {camera_id}")
        return 0

    clip_path = CLIPS_DIR / cam_config["file"]
    if not clip_path.exists():
        logger.error(f"Clip not found: {clip_path}")
        return 0

    camera_type = cam_config["type"]
    entry_line = cam_config.get("entry_line")
    clip_start_str = cam_config.get("clip_start_time", "2026-04-10T20:09:00+05:30")

    # Parse clip start time
    try:
        if "+" in clip_start_str or clip_start_str.endswith("Z"):
            clip_start = datetime.fromisoformat(clip_start_str)
        else:
            clip_start = datetime.fromisoformat(clip_start_str)
    except ValueError:
        clip_start = datetime(2026, 4, 10, 20, 9, 0)

    logger.info(f"Processing {camera_id} ({camera_type}): {clip_path}")

    # ── Load YOLO model ─────────────────────────────────────────────────
    try:
        from ultralytics import YOLO
        model = YOLO(YOLO_MODEL)
        logger.info(f"Loaded YOLO model: {YOLO_MODEL}")
    except ImportError:
        logger.error("ultralytics not installed. Install with: pip install ultralytics")
        return 0

    # ── Open video ───────────────────────────────────────────────────────
    cap = cv2.VideoCapture(str(clip_path))
    if not cap.isOpened():
        logger.error(f"Cannot open video: {clip_path}")
        return 0

    native_fps = cap.get(cv2.CAP_PROP_FPS) or cam_config.get("fps_native", 30)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_interval = max(1, int(native_fps / PROCESS_FPS))
    total_process_frames = total_frames // frame_interval

    logger.info(
        f"  Native FPS: {native_fps}, Processing at {PROCESS_FPS} fps "
        f"(every {frame_interval} frames), Total: {total_frames} frames"
    )

    # ── Initialize tracker ───────────────────────────────────────────────
    tracker = VisitorTracker(
        camera_id=camera_id,
        camera_type=camera_type,
        entry_line_config=entry_line,
    )

    events_before = len(emitter.events)
    frame_idx = 0
    processed = 0
    active_track_ids = set()

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        frame_idx += 1

        # Skip frames to match PROCESS_FPS
        if frame_idx % frame_interval != 0:
            continue

        processed += 1
        elapsed_sec = frame_idx / native_fps
        current_time = clip_start + timedelta(seconds=elapsed_sec)

        # ── Run YOLO detection ───────────────────────────────────────
        results = model.track(
            frame,
            persist=True,
            conf=DETECTION_CONFIDENCE,
            iou=IOU_THRESHOLD,
            classes=[PERSON_CLASS_ID],
            tracker="bytetrack.yaml",
            verbose=False,
        )

        current_track_ids = set()

        if results and results[0].boxes is not None and len(results[0].boxes) > 0:
            boxes = results[0].boxes

            for i in range(len(boxes)):
                # Get bounding box
                xyxy = boxes.xyxy[i].cpu().numpy()
                x1, y1, x2, y2 = xyxy.astype(int)
                conf = float(boxes.conf[i].cpu().numpy())
                
                # Get track ID (ByteTrack assigns these)
                if boxes.id is not None:
                    track_id = int(boxes.id[i].cpu().numpy())
                else:
                    track_id = i  # fallback

                current_track_ids.add(track_id)
                centroid = ((x1 + x2) // 2, (y1 + y2) // 2)

                # ── Zone classification ──────────────────────────────
                zone_id = classify_zone(camera_id, centroid[0], centroid[1])

                # ── Staff classification ─────────────────────────────
                is_staff, staff_conf = classify_staff(
                    frame, (x1, y1, x2, y2),
                    track_frame_count=tracker.tracks[track_id].frame_count if track_id in tracker.tracks else 0,
                    total_frames=total_process_frames,
                )

                # ── Update tracker → get events ──────────────────────
                events = tracker.update_track(
                    track_id=track_id,
                    centroid=centroid,
                    bbox=(x1, y1, x2, y2),
                    timestamp=current_time,
                    confidence=conf,
                    zone_id=zone_id,
                    is_staff=is_staff,
                    staff_confidence=staff_conf,
                )

                # ── Emit events ──────────────────────────────────────
                for evt in events:
                    sku_zone = get_sku_zone(evt.get("zone_id")) if evt.get("zone_id") else None
                    
                    if evt["type"] == "ENTRY":
                        emitter.emit_entry(camera_id, evt["visitor_id"], evt["timestamp"], evt["confidence"], evt.get("is_staff", False))
                    elif evt["type"] == "EXIT":
                        emitter.emit_exit(camera_id, evt["visitor_id"], evt["timestamp"], evt["confidence"], evt.get("is_staff", False))
                    elif evt["type"] == "REENTRY":
                        emitter.emit_reentry(camera_id, evt["visitor_id"], evt["timestamp"], evt["confidence"], evt.get("is_staff", False))
                    elif evt["type"] == "ZONE_ENTER":
                        emitter.emit_zone_enter(camera_id, evt["visitor_id"], evt["timestamp"], evt["zone_id"], evt["confidence"], sku_zone, evt.get("is_staff", False))
                    elif evt["type"] == "ZONE_EXIT":
                        emitter.emit_zone_exit(camera_id, evt["visitor_id"], evt["timestamp"], evt["zone_id"], evt.get("dwell_ms", 0), evt["confidence"], sku_zone, evt.get("is_staff", False))
                    elif evt["type"] == "ZONE_DWELL":
                        emitter.emit_zone_dwell(camera_id, evt["visitor_id"], evt["timestamp"], evt["zone_id"], evt.get("dwell_ms", 0), evt["confidence"], sku_zone, evt.get("is_staff", False))
                    elif evt["type"] == "BILLING_QUEUE_JOIN":
                        emitter.emit_billing_queue_join(camera_id, evt["visitor_id"], evt["timestamp"], evt["confidence"], evt.get("queue_depth", 0), evt.get("is_staff", False))
                    elif evt["type"] == "BILLING_QUEUE_ABANDON":
                        emitter.emit_billing_queue_abandon(camera_id, evt["visitor_id"], evt["timestamp"], evt["confidence"], evt.get("dwell_ms", 0), evt.get("is_staff", False))

        # ── Finalize lost tracks ─────────────────────────────────────
        lost_ids = active_track_ids - current_track_ids
        for lost_id in lost_ids:
            events = tracker.finalize_track(lost_id, current_time)
            for evt in events:
                sku_zone = get_sku_zone(evt.get("zone_id")) if evt.get("zone_id") else None
                if evt["type"] == "EXIT":
                    emitter.emit_exit(camera_id, evt["visitor_id"], evt["timestamp"], evt["confidence"], evt.get("is_staff", False))
                elif evt["type"] == "ZONE_EXIT":
                    emitter.emit_zone_exit(camera_id, evt["visitor_id"], evt["timestamp"], evt["zone_id"], evt.get("dwell_ms", 0), evt["confidence"], sku_zone, evt.get("is_staff", False))

        active_track_ids = current_track_ids

        # Realtime mode: sleep to match real-time playback
        if realtime:
            time.sleep(1.0 / PROCESS_FPS)

        # Progress logging
        if processed % 50 == 0:
            logger.info(f"  [{camera_id}] Processed {processed}/{total_process_frames} frames, {len(emitter.events) - events_before} events")

    cap.release()

    # Finalize remaining tracks
    final_time = clip_start + timedelta(seconds=total_frames / native_fps)
    for tid in list(tracker.tracks.keys()):
        events = tracker.finalize_track(tid, final_time)
        for evt in events:
            sku_zone = get_sku_zone(evt.get("zone_id")) if evt.get("zone_id") else None
            if evt["type"] == "EXIT":
                emitter.emit_exit(camera_id, evt["visitor_id"], evt["timestamp"], evt["confidence"], evt.get("is_staff", False))
            elif evt["type"] == "ZONE_EXIT":
                emitter.emit_zone_exit(camera_id, evt["visitor_id"], evt["timestamp"], evt["zone_id"], evt.get("dwell_ms", 0), evt["confidence"], sku_zone, evt.get("is_staff", False))

    events_count = len(emitter.events) - events_before
    logger.info(f"  [{camera_id}] Done. {events_count} events from {processed} frames.")
    return events_count


def ingest_events_to_api(events: list, api_url: str, batch_size: int = 100):
    """POST events to the Intelligence API in batches."""
    import urllib.request
    import urllib.error

    total = len(events)
    ingested = 0

    for i in range(0, total, batch_size):
        batch = events[i : i + batch_size]
        payload = json.dumps(batch, cls=_NumpySafeEncoder).encode("utf-8")

        req = urllib.request.Request(
            f"{api_url}/events/ingest",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode())
                ingested += result.get("accepted", len(batch))
                logger.info(f"  Ingested batch {i//batch_size + 1}: {result}")
        except urllib.error.URLError as e:
            logger.error(f"  Failed to ingest batch: {e}")

    logger.info(f"Total ingested: {ingested}/{total}")


def main():
    parser = argparse.ArgumentParser(description="Store Intelligence Detection Pipeline")
    parser.add_argument("--store", type=str, default="store1",
                        choices=list(STORE_CONFIGS.keys()),
                        help="Which store to process (default: store1)")
    parser.add_argument("--camera", type=str, help="Process specific camera (e.g., CAM_ENTRY_01)")
    parser.add_argument("--realtime", action="store_true", help="Run in simulated real-time")
    parser.add_argument("--api-url", type=str, default="http://localhost:8000", help="API base URL for event ingestion")
    parser.add_argument("--ingest", action="store_true", help="POST events to API after processing")
    parser.add_argument("--output", type=str, default=None, help="Output JSONL file path")
    args = parser.parse_args()

    # Resolve store configuration
    store_config = STORE_CONFIGS[args.store]
    store_id = store_config["store_id"]
    active_cameras = store_config["active_cameras"]
    output_path = Path(args.output) if args.output else EVENTS_OUTPUT

    logger.info(f"Processing store: {args.store} (ID: {store_id})")

    emitter = EventEmitter(
        store_id=store_id,
        output_path=output_path,
    )

    cameras = [args.camera] if args.camera else active_cameras
    total_events = 0

    for camera_id in cameras:
        count = process_camera(
            camera_id=camera_id,
            emitter=emitter,
            realtime=args.realtime,
            api_url=args.api_url if args.ingest else None,
        )
        total_events += count

    # Write all events to file
    emitter.flush_to_file()
    logger.info(f"\nPipeline complete. Total events: {total_events}")

    # Optionally ingest into API
    if args.ingest:
        logger.info(f"Ingesting events into API at {args.api_url}...")
        ingest_events_to_api(emitter.get_events(), args.api_url)


if __name__ == "__main__":
    main()
