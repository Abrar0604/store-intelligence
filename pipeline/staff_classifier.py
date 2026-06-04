"""
Staff classification — identifies store staff vs customers.

Purplle staff wear all-black uniforms. We use two heuristics:
1. Clothing color analysis — dark clothing (low HSV Value) in the torso region
2. Persistence heuristic — staff are visible for most of the clip duration

Approach documented in CHOICES.md.
"""

import cv2
import numpy as np
from typing import Tuple
from pipeline.config import (
    STAFF_DARK_THRESHOLD,
    STAFF_DARK_RATIO,
    STAFF_PERSISTENCE_RATIO,
)


def analyze_clothing_color(
    frame: np.ndarray,
    bbox: Tuple[int, int, int, int],
) -> float:
    """
    Analyze the clothing region of a detected person to estimate
    the fraction of dark pixels (potential staff uniform).
    
    Args:
        frame: BGR image frame
        bbox: (x1, y1, x2, y2) bounding box coordinates
    
    Returns:
        Ratio of dark pixels in the clothing region (0.0–1.0)
    """
    x1, y1, x2, y2 = [int(c) for c in bbox]
    h = y2 - y1
    w = x2 - x1
    
    if h <= 0 or w <= 0:
        return 0.0

    # Extract the torso region (middle 40% of height, center 60% of width)
    # This avoids hair/head at top and legs at bottom
    torso_y1 = y1 + int(h * 0.25)
    torso_y2 = y1 + int(h * 0.65)
    torso_x1 = x1 + int(w * 0.2)
    torso_x2 = x2 - int(w * 0.2)

    # Clamp to frame bounds
    fh, fw = frame.shape[:2]
    torso_y1 = max(0, min(torso_y1, fh - 1))
    torso_y2 = max(torso_y1 + 1, min(torso_y2, fh))
    torso_x1 = max(0, min(torso_x1, fw - 1))
    torso_x2 = max(torso_x1 + 1, min(torso_x2, fw))

    torso_crop = frame[torso_y1:torso_y2, torso_x1:torso_x2]

    if torso_crop.size == 0:
        return 0.0

    # Convert to HSV and check Value channel
    hsv = cv2.cvtColor(torso_crop, cv2.COLOR_BGR2HSV)
    value_channel = hsv[:, :, 2]

    # Count pixels with low value (dark)
    dark_pixels = np.sum(value_channel < STAFF_DARK_THRESHOLD)
    total_pixels = value_channel.size

    if total_pixels == 0:
        return 0.0

    return dark_pixels / total_pixels


def is_staff_by_clothing(
    frame: np.ndarray,
    bbox: Tuple[int, int, int, int],
) -> Tuple[bool, float]:
    """
    Classify a person as staff based on clothing color.
    
    Returns:
        (is_staff, confidence)
    """
    dark_ratio = analyze_clothing_color(frame, bbox)
    is_staff = dark_ratio > STAFF_DARK_RATIO
    
    # Confidence scales with how far above/below threshold
    if is_staff:
        confidence = min(0.95, 0.6 + (dark_ratio - STAFF_DARK_RATIO) * 2)
    else:
        confidence = min(0.95, 0.6 + (STAFF_DARK_RATIO - dark_ratio) * 2)
    
    return is_staff, confidence


def is_staff_by_persistence(
    track_frame_count: int,
    total_frames: int,
) -> bool:
    """
    Classify as staff if the track is visible for a large fraction
    of the total clip duration. Staff are continuously present;
    customers visit briefly.
    """
    if total_frames == 0:
        return False
    ratio = track_frame_count / total_frames
    return ratio > STAFF_PERSISTENCE_RATIO


def classify_staff(
    frame: np.ndarray,
    bbox: Tuple[int, int, int, int],
    track_frame_count: int = 0,
    total_frames: int = 0,
) -> Tuple[bool, float]:
    """
    Combined staff classification using clothing + persistence.
    
    Either signal alone can flag as staff.
    Both together give higher confidence.
    """
    clothing_staff, clothing_conf = is_staff_by_clothing(frame, bbox)
    persistence_staff = is_staff_by_persistence(track_frame_count, total_frames)
    
    is_staff = clothing_staff or persistence_staff
    
    if clothing_staff and persistence_staff:
        confidence = min(0.98, clothing_conf + 0.15)
    elif clothing_staff:
        confidence = clothing_conf
    elif persistence_staff:
        confidence = 0.70
    else:
        confidence = clothing_conf
    
    return is_staff, confidence
