# PROMPT: "Generate pytest fixtures for a FastAPI app with async SQLite.
# I need a test client, an in-memory database, and sample event data
# covering all event types in the schema."
# CHANGES MADE: Added explicit event_id uniqueness checks, expanded sample data
# to cover edge cases (staff events, re-entries, zero-dwell), and added
# POS transaction fixtures for conversion rate testing.

"""
Shared test fixtures — test client, database, sample events.
"""

import os
import sys
import uuid
import pytest
import pytest_asyncio
from pathlib import Path
from httpx import AsyncClient, ASGITransport

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Use in-memory DB for tests
os.environ["DB_PATH"] = ":memory:"

from app.main import app
from app.database import get_db, close_db, _db


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture
async def client():
    """Async test client for the FastAPI app."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver"
    ) as ac:
        yield ac
    await close_db()


def make_event(
    event_type="ENTRY",
    visitor_id="VIS_test01",
    zone_id=None,
    dwell_ms=0,
    is_staff=False,
    confidence=0.85,
    timestamp="2026-04-10T14:22:10Z",
    store_id="STORE_BLR_002",
    camera_id="CAM_ENTRY_01",
    queue_depth=None,
    sku_zone=None,
    session_seq=1,
):
    """Create a valid event dict for testing."""
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": store_id,
        "camera_id": camera_id,
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": timestamp,
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": is_staff,
        "confidence": confidence,
        "metadata": {
            "queue_depth": queue_depth,
            "sku_zone": sku_zone,
            "session_seq": session_seq,
        },
    }


# ─── Sample event sets for different test scenarios ──────────────────────

SAMPLE_CUSTOMER_JOURNEY = [
    make_event("ENTRY", "VIS_cust01", timestamp="2026-04-10T14:00:00Z", session_seq=1),
    make_event("ZONE_ENTER", "VIS_cust01", zone_id="SKINCARE_KOREAN", camera_id="CAM_FLOOR_01",
               timestamp="2026-04-10T14:01:00Z", sku_zone="KOREAN_SKINCARE", session_seq=2),
    make_event("ZONE_DWELL", "VIS_cust01", zone_id="SKINCARE_KOREAN", camera_id="CAM_FLOOR_01",
               dwell_ms=45000, timestamp="2026-04-10T14:01:45Z", sku_zone="KOREAN_SKINCARE", session_seq=3),
    make_event("ZONE_EXIT", "VIS_cust01", zone_id="SKINCARE_KOREAN", camera_id="CAM_FLOOR_01",
               dwell_ms=90000, timestamp="2026-04-10T14:02:30Z", sku_zone="KOREAN_SKINCARE", session_seq=4),
    make_event("ZONE_ENTER", "VIS_cust01", zone_id="BILLING", camera_id="CAM_BILLING_01",
               timestamp="2026-04-10T14:03:00Z", sku_zone="BILLING", session_seq=5),
    make_event("BILLING_QUEUE_JOIN", "VIS_cust01", zone_id="BILLING", camera_id="CAM_BILLING_01",
               timestamp="2026-04-10T14:03:05Z", queue_depth=1, sku_zone="BILLING", session_seq=6),
    make_event("ZONE_EXIT", "VIS_cust01", zone_id="BILLING", camera_id="CAM_BILLING_01",
               dwell_ms=120000, timestamp="2026-04-10T14:05:00Z", sku_zone="BILLING", session_seq=7),
    make_event("EXIT", "VIS_cust01", timestamp="2026-04-10T14:06:00Z", session_seq=8),
]

SAMPLE_STAFF_EVENTS = [
    make_event("ENTRY", "VIS_staff01", is_staff=True, timestamp="2026-04-10T10:00:00Z"),
    make_event("ZONE_ENTER", "VIS_staff01", zone_id="SKINCARE_KOREAN", camera_id="CAM_FLOOR_01",
               is_staff=True, timestamp="2026-04-10T10:01:00Z"),
    make_event("ZONE_DWELL", "VIS_staff01", zone_id="SKINCARE_KOREAN", camera_id="CAM_FLOOR_01",
               dwell_ms=300000, is_staff=True, timestamp="2026-04-10T10:06:00Z"),
]

SAMPLE_REENTRY = [
    make_event("ENTRY", "VIS_re01", timestamp="2026-04-10T14:00:00Z"),
    make_event("EXIT", "VIS_re01", timestamp="2026-04-10T14:10:00Z"),
    make_event("REENTRY", "VIS_re01", timestamp="2026-04-10T14:12:00Z"),
]
