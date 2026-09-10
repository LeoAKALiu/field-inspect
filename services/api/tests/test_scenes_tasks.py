"""场景、场景元数据与任务端点测试（契约 v2，前缀 /api）。"""

VALID_PROVENANCE_STATUSES = {"simulated", "pending_confirmation"}
VALID_SOURCE_TYPES = {"simulation", "replay", "live_pending"}


def test_list_scenes(client):
    r = client.get("/api/scenes")
    assert r.status_code == 200
    scenes = r.json()
    assert len(scenes) >= 1
    for s in scenes:
        assert s["source_type"] == "simulation"
        assert s["provenance"]["source"] == s["source_type"]
        assert s["provenance"]["status"] in VALID_PROVENANCE_STATUSES


def test_get_scene_detail_with_bindings(client):
    r = client.get("/api/scenes/scene-001")
    assert r.status_code == 200
    scene = r.json()
    assert scene["id"] == "scene-001"
    assert scene["source_type"] == "simulation"
    bindings = scene["spatial_bindings"]
    assert len(bindings) >= 3
    target_types = {b["target_type"] for b in bindings}
    assert "sensor_device" in target_types
    assert "detection_event" in target_types
    for b in bindings:
        assert b["coordinate_system"] == "scene_local_yup"
        assert b["source_type"] in VALID_SOURCE_TYPES
        assert b["provenance"]["status"] in VALID_PROVENANCE_STATUSES


def test_get_scene_404(client):
    r = client.get("/api/scenes/no-such-scene")
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "not_found"
    assert body["error"]["message"]


def test_scene_metadata(client):
    r = client.get("/api/scenes/scene-001/metadata")
    assert r.status_code == 200
    meta = r.json()
    assert meta["scene_id"] == "scene-001"
    assert meta["coordinate_system"] == "scene_local_yup"
    assert meta["units"] == "m"
    assert meta["up_axis"] == "Y"
    assert meta["mesh_url"] == "/tunnel/liris/tunnel_mesh.obj"
    assert meta["pointcloud_url"] == "/tunnel/liris/tunnel_pointcloud.ply"
    # bounds 与 Grok 交付的 viewer Y-up boundingBox 一致
    assert meta["bounds_min"] == {"x": -120.1896, "y": -34.261, "z": -71.9806}
    assert meta["bounds_max"] == {"x": 120.1896, "y": 34.261, "z": 71.9806}
    # route 由 LIRIS 原始 PLY 自动识别主廊道候选并核验逐段连续性后生成。
    route = meta["route"]
    assert len(route) == 20
    assert route[0] == {"x": -92.188, "y": 23.275, "z": 23.557}
    assert route[-1] == {"x": 38.595, "y": 3.605, "z": -50.65}
    for p in route:
        assert set(p) == {"x", "y", "z"}
        assert meta["bounds_min"]["x"] <= p["x"] <= meta["bounds_max"]["x"]
        assert meta["bounds_min"]["y"] <= p["y"] <= meta["bounds_max"]["y"]
        assert meta["bounds_min"]["z"] <= p["z"] <= meta["bounds_max"]["z"]
    # length_m 为路线折线长度（取整）
    import math

    poly = sum(
        math.dist((a["x"], a["y"], a["z"]), (b["x"], b["y"], b["z"]))
        for a, b in zip(route, route[1:], strict=False)
    )
    assert meta["length_m"] == round(poly)
    assert (
        max(
            math.dist((a["x"], a["y"], a["z"]), (b["x"], b["y"], b["z"]))
            for a, b in zip(route, route[1:], strict=False)
        )
        < 10
    )
    route_source = meta["route_provenance"]
    assert route_source["method"] == "pointcloud_auto_corridor_centerline"
    assert route_source["algorithm_version"] == "2.0.0"
    assert route_source["source_point_count"] == 162594
    assert route_source["generated_point_count"] == len(route)
    assert route_source["status"] == "estimated"
    assert route_source["source_sha256"] == (
        "910ddbcd1e9f16f9cc99db195de8c8f277938305d1d83da4f7626c0390a5c6bc"
    )
    assert "非实测路线" in route_source["disclaimer"]
    assert meta["source_type"] == "simulation"
    assert meta["provenance"]["status"] == "simulated"


def test_scene_metadata_404(client):
    r = client.get("/api/scenes/no-such-scene/metadata")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_list_tasks_and_filters(client):
    r = client.get("/api/tasks")
    assert r.status_code == 200
    tasks = r.json()
    assert len(tasks) >= 2
    keys = [t["actual_end"] or t["planned_start"] or "" for t in tasks]
    assert keys == sorted(keys, reverse=True)
    for t in tasks:
        assert t["source_type"] == t["mode"]
        assert t["provenance"]["source"] == t["source_type"]
        assert t["provenance"]["status"] in VALID_PROVENANCE_STATUSES

    replay = client.get("/api/tasks", params={"mode": "replay"}).json()
    assert len(replay) == 1 and replay[0]["mode"] == "replay"
    assert replay[0]["source_type"] == "replay"

    running = client.get("/api/tasks", params={"status": "running"}).json()
    assert len(running) >= 1 and all(t["status"] == "running" for t in running)

    by_scene = client.get("/api/tasks", params={"scene_id": "scene-001"}).json()
    assert len(by_scene) >= 2 and all(t["scene_id"] == "scene-001" for t in by_scene)


def test_list_tasks_invalid_enum_422(client):
    r = client.get("/api/tasks", params={"status": "bogus"})
    assert r.status_code == 422
    r = client.get("/api/tasks", params={"mode": "live"})
    assert r.status_code == 422  # v2 枚举为 live_pending


def test_get_task_and_404(client):
    r = client.get("/api/tasks/task-replay-001")
    assert r.status_code == 200
    assert r.json()["id"] == "task-replay-001"
    assert r.json()["source_type"] == "replay"
    assert r.json()["run_kind"] == "demonstration"
    assert r.json()["acceptance_state"] == "not_applicable"
    assert r.json()["has_pointcloud"] is False
    assert r.json()["acceptance_recorded_at"] is None

    r = client.get("/api/tasks/no-such-task")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_task_trajectory(client):
    r = client.get("/api/tasks/task-replay-001/trajectory")
    assert r.status_code == 200
    points = r.json()
    assert len(points) >= 50
    seqs = [p["seq"] for p in points]
    assert seqs == sorted(seqs)
    # Grok viewer Y-up 包围盒
    bbox_min, bbox_max = (
        {"x": -120.1896, "y": -34.261, "z": -71.9806},
        {"x": 120.1896, "y": 34.261, "z": 71.9806},
    )
    for p in points:
        assert p["task_id"] == "task-replay-001"
        assert set(p["position"]) == {"x", "y", "z"}
        for axis in ("x", "y", "z"):
            assert bbox_min[axis] <= p["position"][axis] <= bbox_max[axis]
        assert 0 <= p["heading_deg"] < 360
        assert 0.8 <= p["speed_mps"] <= 2.0
        assert p["source_type"] == "replay"
        assert p["provenance"]["source"] == "replay"


def test_task_trajectory_pagination(client):
    r = client.get("/api/tasks/task-replay-001/trajectory", params={"from_seq": 10, "limit": 5})
    assert r.status_code == 200
    assert [p["seq"] for p in r.json()] == [10, 11, 12, 13, 14]


def test_task_trajectory_404(client):
    r = client.get("/api/tasks/no-such-task/trajectory")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
