"""Display Trajectory API and Recorded Run missing-data seams."""

from __future__ import annotations

from app.display_trajectory import ALGORITHM, DISCLAIMER, build_display_trajectory

from .formal_helpers import COMPLETED, EXCEPTIONS, import_named, inbox_copy, register_golden_scene

GOLDEN_ID = "golden-run-v2-completed"
EXCEPTIONS_ID = "golden-run-v2-completed-with-exceptions"


def _point(seq: int, x: float, y: float, z: float) -> dict:
    return {
        "seq": seq,
        "position": {"x": x, "y": y, "z": z},
        "timestamp": f"2026-08-24T02:00:{seq:02d}Z",
        "task_id": "run",
        "heading_deg": 0,
        "speed_mps": 0,
        "source_type": "replay",
        "provenance": {"source": "replay", "status": "pending_confirmation"},
    }


def test_display_trajectory_keeps_endpoints_and_landmarks() -> None:
    points = [_point(i, float(i), 1.0, 0.0) for i in range(800)]
    landmarks = [
        {"landmark_id": "begin", "x": 0.0, "y": 1.0, "z": 0.0},
        {"landmark_id": "mid", "x": 400.0, "y": 1.0, "z": 0.0},
        {"landmark_id": "end", "x": 799.0, "y": 1.0, "z": 0.0},
    ]
    result = build_display_trajectory(points, landmarks, max_points=500)
    assert result["algorithm"] == ALGORITHM
    assert result["source_point_count"] == 800
    assert result["display_point_count"] <= 500
    assert result["kept_seq"][0] == 0
    assert result["kept_seq"][-1] == 799
    assert result["landmark_seqs"]["begin"] == 0
    assert result["landmark_seqs"]["end"] == 799
    assert result["landmark_seqs"]["mid"] in result["kept_seq"]
    assert DISCLAIMER == result["disclaimer"]
    assert "1,000,000" in result["disclaimer"]
    assert "not original evidence" in result["disclaimer"]


def test_recorded_and_demonstration_are_distinct_api_entries(client) -> None:
    register_golden_scene(client)
    inbox_copy(client, COMPLETED, "v2-completed")
    import_named(client, "v2-completed")
    recorded = client.get("/api/tasks", params={"run_kind": "recorded"}).json()
    demo = client.get("/api/tasks", params={"run_kind": "demonstration"}).json()
    assert recorded[0]["id"] == GOLDEN_ID
    assert recorded[0]["run_kind"] == "recorded"
    assert recorded[0]["acceptance_state"] == "pending_acceptance"
    assert recorded[0]["has_pointcloud"] is True
    assert recorded[0]["scene_version_id"] == "scene-001-v1"
    assert recorded[0]["alignment_id"] == "alignment-scene-001-v1"
    assert all(item["run_kind"] == "demonstration" for item in demo)
    assert all(item["id"] != GOLDEN_ID for item in demo)
    listing = client.get("/api/tasks").json()
    assert listing[0]["id"] == GOLDEN_ID


def test_exceptions_status_stays_independent(client) -> None:
    register_golden_scene(client)
    inbox_copy(client, EXCEPTIONS, "v2-exceptions")
    import_named(client, "v2-exceptions")
    task = client.get(f"/api/tasks/{EXCEPTIONS_ID}").json()
    assert task["status"] == "completed_with_exceptions"
    assert task["package_status"] == "completed_with_exceptions"
    assert task["has_pointcloud"] is False
    assert task["acceptance_state"] == "pending_acceptance"


def test_display_trajectory_endpoint_is_not_archive_evidence(client) -> None:
    register_golden_scene(client)
    inbox_copy(client, COMPLETED, "v2-completed")
    import_named(client, "v2-completed")
    full = client.get(f"/api/tasks/{GOLDEN_ID}/trajectory").json()
    display = client.get(f"/api/tasks/{GOLDEN_ID}/display-trajectory").json()
    assert display["algorithm"] == ALGORITHM
    assert display["source_point_count"] == len(full)
    assert display["kept_seq"][0] == full[0]["seq"]
    assert display["kept_seq"][-1] == full[-1]["seq"]
    for landmark_id in ("aruco-begin-01", "aruco-mid-02", "aruco-end-03", "aruco-aux-10"):
        assert landmark_id in display["landmark_seqs"]
        assert display["landmark_seqs"][landmark_id] in display["kept_seq"]
    assert "archive_path" not in display
    assert "first_frame" not in display
    task = client.get(f"/api/tasks/{GOLDEN_ID}").json()
    assert "first_frame" not in task
    assert "archive_path" not in task
    assert client.get("/local/evidence-reports/" + GOLDEN_ID).status_code == 404
    display_again = client.get(f"/api/tasks/{GOLDEN_ID}/display-trajectory").json()
    assert display_again["disclaimer"] == DISCLAIMER


def test_long_replay_trajectory_is_reduced_for_display(client) -> None:
    register_golden_scene(client)
    inbox_copy(client, COMPLETED, "v2-completed")
    import_named(client, "v2-completed")
    db = client.app.state.db
    extra = [
        (
            GOLDEN_ID,
            seq,
            f"2026-08-24T02:01:{seq % 60:02d}Z",
            -52.0 + seq * 0.001,
            1.0,
            -18.0,
            12.0,
            0.2,
        )
        for seq in range(3, 803)
    ]
    with db.transaction() as conn:
        conn.executemany(
            "INSERT INTO trajectory_points "
            "(task_id, seq, timestamp, x, y, z, heading_deg, speed_mps) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            extra,
        )
    display = client.get(f"/api/tasks/{GOLDEN_ID}/display-trajectory").json()
    assert display["source_point_count"] == 803
    assert display["display_point_count"] <= 500
    assert display["kept_seq"][0] == 0
    assert display["kept_seq"][-1] == 802
    assert display["landmark_seqs"]["aruco-begin-01"] in display["kept_seq"]
    full = client.get(f"/api/tasks/{GOLDEN_ID}/trajectory", params={"limit": 10000}).json()
    assert len(full) == 803


def test_replay_trajectory_pages_past_ten_thousand(client) -> None:
    register_golden_scene(client)
    inbox_copy(client, COMPLETED, "v2-completed")
    import_named(client, "v2-completed")
    extra = [
        (
            GOLDEN_ID,
            seq,
            f"2026-08-24T03:00:{seq % 60:02d}Z",
            -52.0,
            1.0,
            -18.0,
            12.0,
            0.2,
        )
        for seq in range(3, 12003)
    ]
    with client.app.state.db.transaction() as conn:
        conn.executemany(
            "INSERT INTO trajectory_points "
            "(task_id, seq, timestamp, x, y, z, heading_deg, speed_mps) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            extra,
        )
    too_big = client.get(f"/api/tasks/{GOLDEN_ID}/trajectory", params={"limit": 10001})
    assert too_big.status_code == 200
    assert len(too_big.json()) == 10001
    first = client.get(
        f"/api/tasks/{GOLDEN_ID}/trajectory", params={"from_seq": 0, "limit": 10000}
    ).json()
    rest = client.get(
        f"/api/tasks/{GOLDEN_ID}/trajectory",
        params={"from_seq": first[-1]["seq"] + 1, "limit": 10000},
    ).json()
    assert len(first) == 10000
    assert len(first) + len(rest) == 12003
    rejected = client.get(f"/api/tasks/{GOLDEN_ID}/trajectory", params={"limit": 1000001})
    assert rejected.status_code == 422
