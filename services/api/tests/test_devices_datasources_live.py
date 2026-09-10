"""设备、模拟读数、数据源、live 预留接口测试（契约 v2）。"""

VALID_PROVENANCE_STATUSES = {"simulated", "pending_confirmation"}


def test_list_devices(client):
    r = client.get("/api/devices")
    assert r.status_code == 200
    devices = r.json()
    assert len(devices) >= 8
    types = [d["type"] for d in devices]
    # 至少 2 台离层仪、2 台位移计
    assert types.count("delamination") >= 2
    assert types.count("displacement") >= 2
    # 至少 1 台 offline
    assert any(d["status"] == "offline" for d in devices)
    for d in devices:
        assert d["source_type"] == "simulation"
        assert d["provenance"]["source"] == "simulation"
        assert d["provenance"]["status"] in VALID_PROVENANCE_STATUSES

    delams = client.get("/api/devices", params={"type": "delamination"}).json()
    assert len(delams) >= 2 and all(d["type"] == "delamination" for d in delams)

    assert client.get("/api/devices", params={"type": "strain"}).status_code == 422


def test_device_readings_deterministic(client):
    params = {
        "from": "2026-08-01T00:00:00Z",
        "to": "2026-08-01T01:00:00Z",
        "interval_sec": 60,
    }
    r1 = client.get("/api/devices/dev-temp-001/readings", params=params)
    r2 = client.get("/api/devices/dev-temp-001/readings", params=params)
    assert r1.status_code == 200
    # 相同参数两次调用逐字节一致
    assert r1.content == r2.content

    readings = r1.json()
    assert len(readings) == 61
    timestamps = [p["timestamp"] for p in readings]
    assert timestamps == sorted(timestamps)
    for p in readings:
        assert p["device_id"] == "dev-temp-001"
        assert isinstance(p["value"], (int, float))
        assert p["quality"] in {"good", "uncertain", "bad"}
        assert p["source_type"] == "simulation"
        assert p["provenance"]["status"] == "simulated"


def test_device_readings_differ_between_devices(client):
    params = {"from": "2026-08-01T00:00:00Z", "to": "2026-08-01T00:10:00Z"}
    a = client.get("/api/devices/dev-delam-001/readings", params=params).json()
    b = client.get("/api/devices/dev-delam-002/readings", params=params).json()
    assert [p["value"] for p in a] != [p["value"] for p in b]


def test_device_readings_404(client):
    r = client.get("/api/devices/no-such-device/readings")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_data_sources(client):
    r = client.get("/api/data-sources")
    assert r.status_code == 200
    sources = {s["mode"]: s for s in r.json()}
    assert set(sources) == {"simulation", "replay", "live_pending"}
    assert sources["simulation"]["status"] in {"active", "standby"}
    assert sources["replay"]["status"] in {"active", "standby"}
    live = sources["live_pending"]
    assert live["status"] == "reserved"
    assert live["last_update"] is None
    assert "甲方协议" in live["message"]
    for mode, s in sources.items():
        assert s["source_type"] == mode
        assert s["provenance"]["source"] == mode
        assert s["provenance"]["status"] in VALID_PROVENANCE_STATUSES


def test_live_ingest_always_501(client):
    r = client.post(
        "/api/live/ingest",
        json={"source_id": "party-a-gateway", "timestamp": "2026-08-01T00:00:00Z", "payload": {}},
    )
    assert r.status_code == 501
    body = r.json()
    assert body["error"]["code"] == "not_implemented"
    assert "待甲方协议确认" in body["error"]["message"]
