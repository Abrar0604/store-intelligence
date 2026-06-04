"""
Pipeline configuration — camera settings, zone boundaries, detection thresholds.
All pixel coordinates are relative to 1920x1080 frames.
"""

import os
from pathlib import Path

# ─── Paths ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
CLIPS_DIR = Path(os.environ.get("CLIPS_DIR", str(PROJECT_ROOT.parent / "CCTV Footage")))
EVENTS_OUTPUT = DATA_DIR / "events.jsonl"
POS_FILE = DATA_DIR / "pos_transactions.csv"
STORE_LAYOUT = DATA_DIR / "store_layout.json"

# ─── Store ──────────────────────────────────────────────────────────────────
STORE_ID = "STORE_BLR_002"

# ─── Detection ──────────────────────────────────────────────────────────────
YOLO_MODEL = os.environ.get("YOLO_MODEL", "yolov8n.pt")
DETECTION_CONFIDENCE = 0.35        # minimum confidence for person detection
PERSON_CLASS_ID = 0                # COCO class 0 = person
PROCESS_FPS = 5                    # process at 5 fps to save compute
IOU_THRESHOLD = 0.45               # NMS IoU threshold

# ─── Tracking ───────────────────────────────────────────────────────────────
TRACK_BUFFER = 60                  # frames to keep lost tracks (at 5fps = 12s)
MATCH_THRESHOLD = 0.7              # ByteTrack matching threshold
RE_ENTRY_WINDOW_SEC = 300          # 5 minutes — re-entry detection window

# ─── Staff Detection ───────────────────────────────────────────────────────
STAFF_DARK_THRESHOLD = 60          # HSV Value below this = dark clothing
STAFF_DARK_RATIO = 0.55            # >55% dark pixels in clothing region = staff
STAFF_PERSISTENCE_RATIO = 0.75     # if track visible for >75% of clip = likely staff

# ─── Zone Dwell ────────────────────────────────────────────────────────────
DWELL_EMIT_INTERVAL_SEC = 30       # emit ZONE_DWELL every 30s of continuous stay
ZONE_EXIT_TIMEOUT_SEC = 5          # leave zone for 5s → ZONE_EXIT

# ─── POS Correlation ──────────────────────────────────────────────────────
POS_CORRELATION_WINDOW_SEC = 300   # 5-minute window for visitor-transaction match

# ─── Camera Configurations ────────────────────────────────────────────────
# Each camera has: file mapping, clip start time, type, and zone pixel regions

CAMERA_CONFIGS = {
    "CAM_ENTRY_01": {
        "file": "CAM 3.mp4",
        "type": "entry",
        "clip_start_time": "2026-04-10T20:09:00+05:30",
        "fps_native": 30,
        # Entry/exit threshold: horizontal line across the entrance
        # People crossing this line inward (right→left in frame) = ENTRY
        # People crossing outward (left→right) = EXIT
        "entry_line": {
            "y": 400,               # y-coordinate of the threshold line
            "x_start": 500,
            "x_end": 1200,
            "inbound_direction": "right_to_left",  # entering store
        },
        "zones": {}
    },
    "CAM_FLOOR_01": {
        "file": "CAM 1.mp4",
        "type": "floor",
        "clip_start_time": "2026-04-10T20:10:00+05:30",
        "fps_native": 30,
        "zones": {
            "SKINCARE_KOREAN": {
                "polygon": [(0, 0), (400, 0), (400, 600), (0, 600)],
                "description": "Left side — Korean skincare (FarmStay, EB Korean)"
            },
            "SKINCARE_NATURAL": {
                "polygon": [(400, 0), (900, 0), (900, 600), (400, 600)],
                "description": "Center-left — Natural skincare (Good Vibes, DermDoc)"
            },
            "SKINCARE_CLINICAL": {
                "polygon": [(900, 0), (1500, 0), (1500, 600), (900, 600)],
                "description": "Center-right — Clinical (Minimalist, Aqualogica)"
            },
            "FOH": {
                "polygon": [(300, 500), (1200, 500), (1200, 1080), (300, 1080)],
                "description": "Front of house — central display tables"
            }
        }
    },
    "CAM_FLOOR_02": {
        "file": "CAM 2.mp4",
        "type": "floor",
        "clip_start_time": "2026-04-10T20:10:00+05:30",
        "fps_native": 30,
        "zones": {
            "MAKEUP_LIPS_EYES": {
                "polygon": [(800, 0), (1920, 0), (1920, 600), (800, 600)],
                "description": "Right side — Maybelline, Faces Canada, Lakme"
            },
            "MAKEUP_FACE": {
                "polygon": [(400, 0), (800, 0), (800, 600), (400, 600)],
                "description": "Center — Swiss Beauty, Mars"
            },
            "ACCESSORIES": {
                "polygon": [(0, 0), (400, 0), (400, 500), (0, 500)],
                "description": "Left side — Accessories, Alps, LED panel"
            },
            "FOH": {
                "polygon": [(300, 500), (1200, 500), (1200, 1080), (300, 1080)],
                "description": "Front of house — promo display"
            }
        }
    },
    "CAM_BILLING_01": {
        "file": "CAM 5.mp4",
        "type": "billing",
        "clip_start_time": "2026-04-10T20:09:00+05:30",
        "fps_native": 25,
        "zones": {
            "BILLING": {
                "polygon": [(0, 0), (800, 0), (800, 1080), (0, 1080)],
                "description": "Cash counter area — POS system"
            },
            "CASH_COUNTER": {
                "polygon": [(0, 0), (600, 0), (600, 700), (0, 700)],
                "description": "Behind counter — staff area"
            }
        }
    }
}

# Cameras to process (exclude storage room CAM 4)
ACTIVE_CAMERAS = ["CAM_ENTRY_01", "CAM_FLOOR_01", "CAM_FLOOR_02", "CAM_BILLING_01"]


# ─── Store 2 Camera Configurations ─────────────────────────────────────────
# Store 2 has a different layout: 2 entry cameras, 1 zone camera, 1 billing camera

STORE2_CAMERA_CONFIGS = {
    "S2_CAM_ENTRY_01": {
        "file": "entry 1.mp4",
        "type": "entry",
        "clip_start_time": "2026-04-10T20:09:00+05:30",
        "fps_native": 30,
        "entry_line": {
            "y": 500,
            "x_start": 300,
            "x_end": 1400,
            "inbound_direction": "top_to_bottom",
        },
        "zones": {}
    },
    "S2_CAM_ENTRY_02": {
        "file": "entry 2.mp4",
        "type": "entry",
        "clip_start_time": "2026-04-10T20:09:00+05:30",
        "fps_native": 30,
        "entry_line": {
            "y": 500,
            "x_start": 300,
            "x_end": 1400,
            "inbound_direction": "top_to_bottom",
        },
        "zones": {}
    },
    "S2_CAM_FLOOR_01": {
        "file": "zone.mp4",
        "type": "floor",
        "clip_start_time": "2026-04-10T20:10:00+05:30",
        "fps_native": 30,
        "zones": {
            "WALL_LEFT": {
                "polygon": [(0, 400), (300, 400), (300, 1080), (0, 1080)],
                "description": "Left wall units (1-5) — skincare, haircare"
            },
            "WALL_TOP": {
                "polygon": [(300, 200), (1400, 200), (1400, 500), (300, 500)],
                "description": "Top wall units (7-10) — cosmetics"
            },
            "WALL_RIGHT": {
                "polygon": [(1500, 200), (1920, 200), (1920, 1080), (1500, 1080)],
                "description": "Right wall units (15-19) — fragrances, premium"
            },
            "GONDOLA": {
                "polygon": [(400, 600), (900, 600), (900, 900), (400, 900)],
                "description": "Center gondolas + MK displays"
            },
            "FOH": {
                "polygon": [(400, 500), (1200, 500), (1200, 800), (400, 800)],
                "description": "Front of house — central display area"
            },
            "MAKEUP_ISLAND": {
                "polygon": [(900, 600), (1400, 600), (1400, 900), (900, 900)],
                "description": "Makeup unit island"
            },
        }
    },
    "S2_CAM_BILLING_01": {
        "file": "billing_area.mp4",
        "type": "billing",
        "clip_start_time": "2026-04-10T20:09:00+05:30",
        "fps_native": 25,
        "zones": {
            "BILLING": {
                "polygon": [(400, 100), (1200, 100), (1200, 500), (400, 500)],
                "description": "Cash counter area"
            },
            "CASH_COUNTER": {
                "polygon": [(500, 150), (900, 150), (900, 400), (500, 400)],
                "description": "Behind counter — staff area"
            }
        }
    }
}

STORE2_ACTIVE_CAMERAS = ["S2_CAM_ENTRY_01", "S2_CAM_ENTRY_02", "S2_CAM_FLOOR_01", "S2_CAM_BILLING_01"]


# ─── Multi-Store Configuration ─────────────────────────────────────────────

STORE_CONFIGS = {
    "store1": {
        "store_id": STORE_ID,
        "clips_dir_name": "Store 1",
        "cameras": CAMERA_CONFIGS,
        "active_cameras": ACTIVE_CAMERAS,
    },
    "store2": {
        "store_id": "STORE_2",
        "clips_dir_name": "Store 2",
        "cameras": STORE2_CAMERA_CONFIGS,
        "active_cameras": STORE2_ACTIVE_CAMERAS,
    },
}

