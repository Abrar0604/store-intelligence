"""
Zone classification — maps bounding box centroids to named store zones.
Uses pixel-region polygons defined per camera in config.py.
"""

from typing import Optional, Tuple
from pipeline.config import CAMERA_CONFIGS


def point_in_polygon(point: Tuple[int, int], polygon: list) -> bool:
    """Ray-casting algorithm for point-in-polygon test."""
    x, y = point
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def classify_zone(
    camera_id: str,
    centroid_x: int,
    centroid_y: int
) -> Optional[str]:
    """
    Determine which zone a detected person is in based on their bounding box centroid.
    
    Args:
        camera_id: Camera that produced the detection
        centroid_x: X coordinate of bounding box center
        centroid_y: Y coordinate of bounding box center
    
    Returns:
        Zone ID string or None if person is not in any defined zone
    """
    cam_config = CAMERA_CONFIGS.get(camera_id)
    if not cam_config:
        return None

    zones = cam_config.get("zones", {})
    
    # Check each zone polygon — return first match
    # We check smaller/more-specific zones first if they overlap
    for zone_id, zone_info in zones.items():
        polygon = zone_info["polygon"]
        if point_in_polygon((centroid_x, centroid_y), polygon):
            return zone_id

    return None


def get_sku_zone(zone_id: str) -> Optional[str]:
    """
    Map zone_id to a product-category label for the metadata.sku_zone field.
    This provides business-meaningful zone names.
    """
    SKU_ZONE_MAP = {
        "SKINCARE_KOREAN": "KOREAN_SKINCARE",
        "SKINCARE_NATURAL": "NATURAL_SKINCARE",
        "SKINCARE_CLINICAL": "CLINICAL_SKINCARE",
        "MAKEUP_FACE": "FACE_MAKEUP",
        "MAKEUP_LIPS_EYES": "LIPS_EYES",
        "FRAGRANCE": "FRAGRANCE",
        "ACCESSORIES": "ACCESSORIES",
        "FOH": "FRONT_DISPLAY",
        "BILLING": "BILLING",
        "CASH_COUNTER": "BILLING",
        "ENTRY_EXIT": None,
    }
    return SKU_ZONE_MAP.get(zone_id)


def get_all_zones_for_camera(camera_id: str) -> list:
    """Return list of zone IDs covered by a camera."""
    cam_config = CAMERA_CONFIGS.get(camera_id, {})
    return list(cam_config.get("zones", {}).keys())
