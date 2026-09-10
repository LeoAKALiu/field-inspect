"""事件端点测试（契约 v2）：过滤、状态机、GET/POST 导出。"""

VALID_PROVENANCE_STATUSES = {"simulated", "pending_confirmation"}


def test_list_events_and_filters(client):
    r = client.get("/api/events")
    assert r.status_code == 200
    events = r.json()
    assert 3 <= len(events) <= 5
    detected = [e["detected_at"] for e in events]
    assert detected == sorted(detected, reverse=True)
    for e in events:
        assert e["source_type"] in {"simulation", "replay"}
        assert e["provenance"]["source"] == e["source_type"]
        assert e["provenance"]["status"] in VALID_PROVENANCE_STATUSES

    open_events = client.get("/api/events", params={"status": "open"}).json()
    assert len(open_events) >= 1
    assert all(e["status"] == "open" for e in open_events)

    cracks = client.get("/api/events", params={"type": "crack"}).json()
    assert len(cracks) >= 1
    assert all(e["type"] == "crack" for e in cracks)

    critical = client.get("/api/events", params={"severity": "critical"}).json()
    assert all(e["severity"] == "critical" for e in critical)

    by_task = client.get("/api/events", params={"task_id": "task-replay-001"}).json()
    assert len(by_task) == 4
    assert all(e["task_id"] == "task-replay-001" for e in by_task)
    assert all(e["source_type"] == "replay" for e in by_task)


def test_seed_demonstrates_review_workflow(client):
    """种子事件演示完整复核过程：acknowledged 与 resolved/false_positive 带处置信息。"""
    events = client.get("/api/events").json()
    by_status = {}
    for e in events:
        by_status.setdefault(e["status"], []).append(e)
    assert "open" in by_status
    assert "acknowledged" in by_status
    closed = by_status.get("resolved", []) + by_status.get("false_positive", [])
    assert len(closed) >= 1
    for e in by_status["acknowledged"] + closed:
        assert e["handled_by"]
        assert e["handled_at"]
        assert e["handle_comment"]


def test_list_events_pagination(client):
    page1 = client.get("/api/events", params={"limit": 3, "offset": 0}).json()
    page2 = client.get("/api/events", params={"limit": 3, "offset": 3}).json()
    assert len(page1) == 3 and len(page2) >= 1
    assert {e["id"] for e in page1}.isdisjoint({e["id"] for e in page2})


def test_list_events_invalid_enum_422(client):
    assert client.get("/api/events", params={"status": "bogus"}).status_code == 422
    assert client.get("/api/events", params={"severity": "bogus"}).status_code == 422
    assert client.get("/api/events", params={"type": "bogus"}).status_code == 422


def test_get_event_and_404(client):
    r = client.get("/api/events/evt-0001")
    assert r.status_code == 200
    event = r.json()
    assert event["id"] == "evt-0001"
    assert event["source_type"] == "replay"
    assert event["provenance"]["status"] in VALID_PROVENANCE_STATUSES

    r = client.get("/api/events/no-such-event")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_event_status_machine_happy_path(client):
    r = client.patch(
        "/api/events/evt-0001",
        json={"status": "acknowledged", "comment": "已收到，安排复核", "handled_by": "operator-li"},
    )
    assert r.status_code == 200
    event = r.json()
    assert event["status"] == "acknowledged"
    assert event["handled_by"] == "operator-li"
    assert event["handle_comment"] == "已收到，安排复核"
    assert event["handled_at"] and event["handled_at"].endswith("Z")

    r = client.patch("/api/events/evt-0001", json={"status": "resolved"})
    assert r.status_code == 200
    assert r.json()["status"] == "resolved"


def test_event_false_positive_path(client):
    r = client.patch("/api/events/evt-0002", json={"status": "false_positive"})
    # evt-0002 种子状态为 acknowledged，可迁移到 false_positive
    assert r.status_code == 200
    assert r.json()["status"] == "false_positive"


def test_event_illegal_transitions_409(client):
    # open 不能直接 resolved（必须经 acknowledged）
    r = client.patch("/api/events/evt-0001", json={"status": "resolved"})
    assert r.status_code == 409
    body = r.json()
    assert body["error"]["code"] == "invalid_state_transition"
    assert body["error"]["details"]["current_status"] == "open"

    # resolved 为终态
    r = client.patch("/api/events/evt-0003", json={"status": "acknowledged"})
    assert r.status_code == 409
    assert r.json()["error"]["details"]["current_status"] == "resolved"


def test_event_patch_404_and_422(client):
    r = client.patch("/api/events/no-such-event", json={"status": "acknowledged"})
    assert r.status_code == 404
    r = client.patch("/api/events/evt-0001", json={"status": "open"})
    assert r.status_code == 422


def test_export_get_csv(client):
    r = client.get("/api/events/export", params={"format": "csv"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("id,scene_id,task_id,type,severity,status")
    assert len(lines) == 1 + len(client.get("/api/events", params={"limit": 1000}).json())


def test_export_post_csv(client):
    r = client.post("/api/events/export", json={"format": "csv", "status": "open"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("id,scene_id,task_id")
    open_count = len(client.get("/api/events", params={"status": "open"}).json())
    assert len(lines) == 1 + open_count


def test_export_post_json_matches_list(client):
    body = {"format": "json", "status": "open", "type": "crack"}
    exported = client.post("/api/events/export", json=body).json()
    listed = client.get(
        "/api/events", params={"status": "open", "type": "crack", "limit": 1000}
    ).json()
    assert isinstance(exported, list)
    assert exported == listed


def test_export_post_invalid_format_422(client):
    r = client.post("/api/events/export", json={"format": "xml"})
    assert r.status_code == 422
