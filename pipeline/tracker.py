"""
Visitor tracker — manages track state, entry/exit detection, zone transitions,
dwell timing, re-entry detection, and queue depth monitoring.

Uses ByteTrack IDs from the detection layer and wraps them with business logic.
"""

import hashlib
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple, List, Set
from dataclasses import dataclass, field

from pipeline.config import (
    RE_ENTRY_WINDOW_SEC,
    DWELL_EMIT_INTERVAL_SEC,
    ZONE_EXIT_TIMEOUT_SEC,
)


@dataclass
class VisitorTrack:
    """State for a single tracked visitor."""
    visitor_id: str
    track_id: int
    camera_id: str
    first_seen: datetime
    last_seen: datetime
    is_staff: bool = False
    staff_confidence: float = 0.0
    
    # Entry/exit state
    has_entered: bool = False
    has_exited: bool = False
    entry_time: Optional[datetime] = None
    exit_time: Optional[datetime] = None
    is_reentry: bool = False
    
    # Zone state
    current_zone: Optional[str] = None
    zone_enter_time: Optional[datetime] = None
    last_dwell_emit: Optional[datetime] = None
    zones_visited: Set[str] = field(default_factory=set)
    
    # Tracking
    positions: List[Tuple[int, int]] = field(default_factory=list)
    frame_count: int = 0
    avg_confidence: float = 0.0
    confidence_sum: float = 0.0

    # Staff classification accumulator
    staff_dark_ratios: List[float] = field(default_factory=list)


def generate_visitor_id(track_id: int, camera_id: str, timestamp: str) -> str:
    """
    Generate a stable visitor_id from track parameters.
    Format: VIS_XXXXXX (6 hex chars from hash)
    """
    raw = f"{track_id}_{camera_id}_{timestamp}"
    hash_hex = hashlib.md5(raw.encode()).hexdigest()[:6]
    return f"VIS_{hash_hex}"


class VisitorTracker:
    """
    Manages all active visitor tracks for a camera.
    Handles entry/exit detection, zone transitions, and dwell timing.
    """

    def __init__(self, camera_id: str, camera_type: str, entry_line_config: dict = None):
        self.camera_id = camera_id
        self.camera_type = camera_type
        self.entry_line_config = entry_line_config
        
        self.tracks: Dict[int, VisitorTrack] = {}
        self.exited_visitors: Dict[str, datetime] = {}  # visitor_id → exit time (for re-entry)
        self.active_count: int = 0
        self.billing_zone_occupants: Set[str] = set()  # visitor_ids in billing zone

    def update_track(
        self,
        track_id: int,
        centroid: Tuple[int, int],
        bbox: Tuple[int, int, int, int],
        timestamp: datetime,
        confidence: float,
        zone_id: Optional[str] = None,
        is_staff: bool = False,
        staff_confidence: float = 0.0,
    ) -> List[dict]:
        """
        Update a track with a new detection. Returns a list of event dicts to emit.
        
        This is the core method — it processes each detection and decides what
        events to generate based on state transitions.
        """
        events_to_emit = []

        if track_id not in self.tracks:
            # New track
            visitor_id = generate_visitor_id(
                track_id, self.camera_id, timestamp.isoformat()
            )
            
            # Check for re-entry
            is_reentry = False
            if self.camera_type == "entry":
                is_reentry = self._check_reentry(centroid, timestamp)
            
            track = VisitorTrack(
                visitor_id=visitor_id,
                track_id=track_id,
                camera_id=self.camera_id,
                first_seen=timestamp,
                last_seen=timestamp,
                is_staff=is_staff,
                staff_confidence=staff_confidence,
                is_reentry=is_reentry,
            )
            self.tracks[track_id] = track
            
            # Entry camera logic
            if self.camera_type == "entry":
                if is_reentry:
                    events_to_emit.append({
                        "type": "REENTRY",
                        "visitor_id": visitor_id,
                        "timestamp": timestamp,
                        "confidence": confidence,
                        "is_staff": is_staff,
                    })
                else:
                    events_to_emit.append({
                        "type": "ENTRY",
                        "visitor_id": visitor_id,
                        "timestamp": timestamp,
                        "confidence": confidence,
                        "is_staff": is_staff,
                    })
                track.has_entered = True
                track.entry_time = timestamp
            
            # Floor/billing camera — emit ZONE_ENTER if in a zone
            if zone_id and self.camera_type in ("floor", "billing"):
                events_to_emit.append({
                    "type": "ZONE_ENTER",
                    "visitor_id": visitor_id,
                    "timestamp": timestamp,
                    "zone_id": zone_id,
                    "confidence": confidence,
                    "is_staff": is_staff,
                })
                track.current_zone = zone_id
                track.zone_enter_time = timestamp
                track.last_dwell_emit = timestamp
                track.zones_visited.add(zone_id)
                
                if zone_id in ("BILLING", "CASH_COUNTER"):
                    self.billing_zone_occupants.add(visitor_id)
                    events_to_emit.append({
                        "type": "BILLING_QUEUE_JOIN",
                        "visitor_id": visitor_id,
                        "timestamp": timestamp,
                        "confidence": confidence,
                        "queue_depth": len(self.billing_zone_occupants),
                        "is_staff": is_staff,
                    })

        else:
            # Existing track — update state
            track = self.tracks[track_id]
            track.last_seen = timestamp
            track.frame_count += 1
            track.confidence_sum += confidence
            track.avg_confidence = track.confidence_sum / track.frame_count
            track.positions.append(centroid)
            
            # Update staff classification (running average)
            if is_staff:
                track.is_staff = True
                track.staff_confidence = max(track.staff_confidence, staff_confidence)
            
            # Zone transition detection
            if self.camera_type in ("floor", "billing"):
                if zone_id != track.current_zone:
                    # Zone changed
                    if track.current_zone is not None:
                        # Emit ZONE_EXIT for previous zone
                        dwell_ms = int((timestamp - track.zone_enter_time).total_seconds() * 1000) if track.zone_enter_time else 0
                        events_to_emit.append({
                            "type": "ZONE_EXIT",
                            "visitor_id": track.visitor_id,
                            "timestamp": timestamp,
                            "zone_id": track.current_zone,
                            "dwell_ms": dwell_ms,
                            "confidence": confidence,
                            "is_staff": track.is_staff,
                        })
                        
                        # Check billing zone exit (potential abandon)
                        if track.current_zone in ("BILLING", "CASH_COUNTER"):
                            self.billing_zone_occupants.discard(track.visitor_id)
                    
                    if zone_id is not None:
                        # Emit ZONE_ENTER for new zone
                        events_to_emit.append({
                            "type": "ZONE_ENTER",
                            "visitor_id": track.visitor_id,
                            "timestamp": timestamp,
                            "zone_id": zone_id,
                            "confidence": confidence,
                            "is_staff": track.is_staff,
                        })
                        track.zones_visited.add(zone_id)
                        
                        if zone_id in ("BILLING", "CASH_COUNTER"):
                            self.billing_zone_occupants.add(track.visitor_id)
                            events_to_emit.append({
                                "type": "BILLING_QUEUE_JOIN",
                                "visitor_id": track.visitor_id,
                                "timestamp": timestamp,
                                "confidence": confidence,
                                "queue_depth": len(self.billing_zone_occupants),
                                "is_staff": track.is_staff,
                            })
                    
                    track.current_zone = zone_id
                    track.zone_enter_time = timestamp
                    track.last_dwell_emit = timestamp

                elif zone_id is not None and track.last_dwell_emit is not None:
                    # Same zone — check if we should emit a ZONE_DWELL
                    elapsed = (timestamp - track.last_dwell_emit).total_seconds()
                    if elapsed >= DWELL_EMIT_INTERVAL_SEC:
                        dwell_ms = int((timestamp - track.zone_enter_time).total_seconds() * 1000)
                        events_to_emit.append({
                            "type": "ZONE_DWELL",
                            "visitor_id": track.visitor_id,
                            "timestamp": timestamp,
                            "zone_id": zone_id,
                            "dwell_ms": dwell_ms,
                            "confidence": track.avg_confidence if track.avg_confidence > 0 else confidence,
                            "is_staff": track.is_staff,
                        })
                        track.last_dwell_emit = timestamp

        return events_to_emit

    def finalize_track(self, track_id: int, timestamp: datetime) -> List[dict]:
        """
        Called when a track is lost. Emit EXIT and/or ZONE_EXIT events.
        """
        events_to_emit = []
        track = self.tracks.get(track_id)
        if not track:
            return events_to_emit

        # Emit zone exit if still in a zone
        if track.current_zone is not None:
            dwell_ms = int((timestamp - track.zone_enter_time).total_seconds() * 1000) if track.zone_enter_time else 0
            events_to_emit.append({
                "type": "ZONE_EXIT",
                "visitor_id": track.visitor_id,
                "timestamp": timestamp,
                "zone_id": track.current_zone,
                "dwell_ms": dwell_ms,
                "confidence": track.avg_confidence if track.avg_confidence > 0 else 0.5,
                "is_staff": track.is_staff,
            })
            if track.current_zone in ("BILLING", "CASH_COUNTER"):
                self.billing_zone_occupants.discard(track.visitor_id)

        # Entry camera — emit EXIT
        if self.camera_type == "entry" and track.has_entered and not track.has_exited:
            events_to_emit.append({
                "type": "EXIT",
                "visitor_id": track.visitor_id,
                "timestamp": timestamp,
                "confidence": track.avg_confidence if track.avg_confidence > 0 else 0.5,
                "is_staff": track.is_staff,
            })
            track.has_exited = True
            track.exit_time = timestamp
            self.exited_visitors[track.visitor_id] = timestamp

        return events_to_emit

    def _check_reentry(self, centroid: Tuple[int, int], timestamp: datetime) -> bool:
        """Check if this new detection might be a re-entry (same person returning)."""
        cutoff = timestamp - timedelta(seconds=RE_ENTRY_WINDOW_SEC)
        for vid, exit_time in self.exited_visitors.items():
            if exit_time > cutoff:
                return True
        return False

    def get_queue_depth(self) -> int:
        """Return current number of visitors in the billing zone."""
        return len(self.billing_zone_occupants)

    def get_active_track_ids(self) -> Set[int]:
        """Return set of currently active track IDs."""
        return set(self.tracks.keys())

    # ─── Group Detection ──────────────────────────────────────────────────

    GROUP_TEMPORAL_WINDOW_SEC = 2.0  # Tracks appearing within 2s are grouped
    GROUP_SPATIAL_THRESHOLD = 200    # Tracks within 200px are grouped

    def detect_groups(self) -> Dict[str, list]:
        """
        Detect groups of visitors who entered together.
        Groups are identified by temporal + spatial proximity on the entry camera.

        Returns dict: group_id → [visitor_id, ...]
        """
        if self.camera_type != "entry":
            return {}

        # Find tracks that appeared close together in time and space
        entry_tracks = [
            t for t in self.tracks.values()
            if t.has_entered and not t.is_staff
        ]

        if len(entry_tracks) < 2:
            return {}

        # Sort by entry time
        entry_tracks.sort(key=lambda t: t.first_seen)

        groups: Dict[str, list] = {}
        group_counter = 0
        assigned: set = set()

        for i, t1 in enumerate(entry_tracks):
            if t1.visitor_id in assigned:
                continue

            group_members = [t1.visitor_id]
            assigned.add(t1.visitor_id)

            for t2 in entry_tracks[i + 1:]:
                if t2.visitor_id in assigned:
                    continue

                time_diff = abs((t2.first_seen - t1.first_seen).total_seconds())
                if time_diff > self.GROUP_TEMPORAL_WINDOW_SEC:
                    break  # sorted, so no more within window

                # Check spatial proximity
                if t1.positions and t2.positions:
                    dx = abs(t1.positions[0][0] - t2.positions[0][0])
                    dy = abs(t1.positions[0][1] - t2.positions[0][1])
                    dist = (dx ** 2 + dy ** 2) ** 0.5
                    if dist <= self.GROUP_SPATIAL_THRESHOLD:
                        group_members.append(t2.visitor_id)
                        assigned.add(t2.visitor_id)

            if len(group_members) >= 2:
                group_counter += 1
                gid = f"GRP_{group_counter:04d}"
                groups[gid] = group_members

        return groups

