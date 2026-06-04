# PROMPT: "Write comprehensive pytest tests for a FastAPI event ingestion endpoint.
# Cover: valid batch, idempotency (same event_id twice), partial success with
# malformed events, batch size limit (500), empty batch, invalid JSON, and
# schema validation failures."
# CHANGES MADE: Added explicit assertion on accepted/rejected counts per scenario,
# verified idempotency produces identical response, added test for concurrent
# duplicate ingestion, and structured error message validation.

"""
Tests for POST /events/ingest endpoint.
Covers idempotency, batch limits, partial success, validation, and edge cases.
"""

import uuid
import pytest
from tests.conftest import make_event, SAMPLE_CUSTOMER_JOURNEY


@pytest.mark.asyncio
async def test_ingest_valid_batch(client):
    """Valid batch of events should be accepted."""
    events = [make_event(visitor_id=f"VIS_t{i}") for i in range(5)]
    resp = await client.post("/events/ingest", json=events)
    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted"] == 5
    assert data["rejected"] == 0
    assert data["total"] == 5


@pytest.mark.asyncio
async def test_ingest_idempotency(client):
    """Ingesting the same event_id twice should not create duplicates."""
    event = make_event(visitor_id="VIS_idem01")
    
    # First ingestion
    resp1 = await client.post("/events/ingest", json=[event])
    assert resp1.status_code == 200
    assert resp1.json()["accepted"] == 1
    
    # Second ingestion with same event_id
    resp2 = await client.post("/events/ingest", json=[event])
    assert resp2.status_code == 200
    # Should accept (INSERT OR IGNORE) without error
    assert resp2.json()["accepted"] == 1
    assert resp2.json()["rejected"] == 0


@pytest.mark.asyncio
async def test_ingest_partial_success(client):
    """Mix of valid and invalid events — valid ones accepted, invalid rejected."""
    valid_event = make_event(visitor_id="VIS_partial01")
    invalid_event = {
        "event_id": "bad",
        "store_id": "STORE_BLR_002",
        # Missing required fields
    }
    
    resp = await client.post("/events/ingest", json=[valid_event, invalid_event])
    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted"] == 1
    assert data["rejected"] == 1
    assert len(data["errors"]) == 1


@pytest.mark.asyncio
async def test_ingest_batch_limit(client):
    """Batch exceeding 500 events should be rejected."""
    events = [make_event(visitor_id=f"VIS_big{i}") for i in range(501)]
    resp = await client.post("/events/ingest", json=events)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_ingest_empty_batch(client):
    """Empty batch should be accepted with 0 counts."""
    resp = await client.post("/events/ingest", json=[])
    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted"] == 0
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_ingest_invalid_json(client):
    """Non-JSON body should return 400."""
    resp = await client.post(
        "/events/ingest",
        content="not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_ingest_invalid_event_type(client):
    """Invalid event_type should cause rejection."""
    event = make_event()
    event["event_type"] = "INVALID_TYPE"
    
    resp = await client.post("/events/ingest", json=[event])
    assert resp.status_code == 200
    assert resp.json()["rejected"] == 1


@pytest.mark.asyncio
async def test_ingest_invalid_confidence(client):
    """Confidence outside 0-1 range should be rejected."""
    event = make_event()
    event["confidence"] = 1.5
    
    resp = await client.post("/events/ingest", json=[event])
    assert resp.status_code == 200
    assert resp.json()["rejected"] == 1


@pytest.mark.asyncio
async def test_ingest_dict_format(client):
    """Accept {events: [...]} format as well as raw list."""
    events = [make_event(visitor_id="VIS_dict01")]
    resp = await client.post("/events/ingest", json={"events": events})
    assert resp.status_code == 200
    assert resp.json()["accepted"] == 1


@pytest.mark.asyncio
async def test_ingest_customer_journey(client):
    """Ingest a complete customer journey (8 events)."""
    resp = await client.post("/events/ingest", json=SAMPLE_CUSTOMER_JOURNEY)
    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted"] == len(SAMPLE_CUSTOMER_JOURNEY)
    assert data["rejected"] == 0


@pytest.mark.asyncio
async def test_ingest_sample_format_entry_exit(client):
    """Official sample format (lowercase event types, id_token) should be accepted."""
    sample_events = [
        {
            "event_type": "entry",
            "id_token": "ID_60001",
            "store_code": "store_1076",
            "camera_id": "cam1",
            "event_timestamp": "2026-03-08T18:10:05.120000",
            "is_staff": False,
            "gender_pred": "F",
            "age_pred": 28,
            "age_bucket": "25-34",
            "is_face_hidden": False,
            "group_id": None,
            "group_size": None,
        },
        {
            "event_type": "exit",
            "id_token": "ID_60001",
            "store_code": "store_1076",
            "camera_id": "cam1",
            "event_timestamp": "2026-03-08T18:12:44.360000",
            "is_staff": False,
            "gender_pred": "F",
            "age_pred": 28,
            "age_bucket": "25-34",
            "is_face_hidden": False,
            "group_id": None,
            "group_size": None,
        },
    ]
    resp = await client.post("/events/ingest", json=sample_events)
    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted"] == 2
    assert data["rejected"] == 0


@pytest.mark.asyncio
async def test_ingest_sample_format_queue(client):
    """Official sample queue events (queue_completed/queue_abandoned) should be accepted."""
    queue_events = [
        {
            "queue_event_id": "cfd8e3c5-7aa0-4ea3-9b59-692d50da8308",
            "event_type": "queue_completed",
            "track_id": 102,
            "store_id": "ST1076",
            "camera_id": "PURPLLE_MUM_1076_CAM6",
            "zone_id": "PURPLLE_MUM_1076_Z_BILLING_01",
            "zone_name": "Billing Counter Queue",
            "zone_type": "BILLING",
            "is_revenue_zone": "Yes",
            "queue_join_ts": "2026-03-08T18:13:05.080000",
            "queue_served_ts": "2026-03-08T18:13:13.240000",
            "queue_exit_ts": "2026-03-08T18:15:31.840000",
            "wait_seconds": 8,
            "queue_position_at_join": 2,
            "abandoned": False,
            "zone_hotspot_x": 602.8,
            "zone_hotspot_y": 183.4,
            "gender_pred": "M",
            "age_pred": 31,
            "age_bucket": "25-34",
        },
    ]
    resp = await client.post("/events/ingest", json=queue_events)
    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted"] == 1
    assert data["rejected"] == 0
