"""WebSocket 测试（C1–C4）：车辆推送与回放控制。

conftest 将 TWIN_SIM_TICK_MS 设为 50ms，保证测试快速。
"""

import time


def test_vehicle_stream(client):
    with client.websocket_connect("/ws/vehicle?task_id=task-sim-001") as ws:
        ack = ws.receive_json()
        assert ack["type"] == "connection.ack"
        assert ack["payload"]["channel"] == "vehicle"
        assert ack["payload"]["task_id"] == "task-sim-001"
        assert ack["payload"]["server_time"].endswith("Z")

        # 至少收到一条 vehicle.state（跳过可能的事件消息）
        for _ in range(20):
            msg = ws.receive_json()
            if msg["type"] == "vehicle.state":
                break
        else:
            raise AssertionError("未收到 vehicle.state")
        p = msg["payload"]
        assert p["task_id"] == "task-sim-001"
        assert p["timestamp"].endswith("Z")
        assert set(p["position"]) == {"x", "y", "z"}
        assert 0 <= p["heading_deg"] < 360
        assert p["speed_mps"] >= 0
        assert p["source_type"] == "simulation"
        assert p["provenance"]["source"] == "simulation"
        assert p["provenance"]["status"] in {"simulated", "pending_confirmation"}


def test_vehicle_stream_unknown_task(client):
    with client.websocket_connect("/ws/vehicle?task_id=task-replay-001") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert msg["payload"]["code"] == "not_found"


def test_replay_full_flow(client):
    with client.websocket_connect("/ws/replay/task-replay-001") as ws:
        ack = ws.receive_json()
        assert ack["type"] == "connection.ack"
        assert ack["payload"]["channel"] == "replay"

        started = ws.receive_json()
        assert started["type"] == "replay.started"
        total = started["payload"]["total_points"]
        assert total >= 50
        assert started["payload"]["speed"] == 1.0

        ws.send_json({"type": "replay.control", "payload": {"action": "speed", "speed": 100}})
        seqs = []
        progresses = []
        while True:
            msg = ws.receive_json()
            if msg["type"] == "replay.point":
                seqs.append(msg["payload"]["point"]["seq"])
                progresses.append(msg["payload"]["progress"])
            elif msg["type"] == "replay.finished":
                assert msg["payload"]["task_id"] == "task-replay-001"
                assert msg["payload"]["total_points"] == total
                break
        assert seqs == list(range(total))
        assert progresses == sorted(progresses)
        assert progresses[-1] == 1.0


def test_replay_pause_and_seek(client):
    with client.websocket_connect("/ws/replay/task-replay-001") as ws:
        assert ws.receive_json()["type"] == "connection.ack"
        assert ws.receive_json()["type"] == "replay.started"

        first = ws.receive_json()
        assert first["type"] == "replay.point"
        assert first["payload"]["point"]["seq"] == 0

        # 默认 1x 倍速下，下一点 1s 后才发送；在此期间 pause 应被及时处理
        ws.send_json({"type": "replay.control", "payload": {"action": "pause"}})
        time.sleep(0.2)
        ws.send_json({"type": "replay.control", "payload": {"action": "seek", "seq": 60}})
        ws.send_json({"type": "replay.control", "payload": {"action": "resume"}})

        nxt = ws.receive_json()
        assert nxt["type"] == "replay.point"
        # pause 生效则 resume 后从 seek 的 seq=60 继续，而非积压的 seq=1
        assert nxt["payload"]["point"]["seq"] == 60


def test_replay_unknown_task_error_then_close(client):
    with client.websocket_connect("/ws/replay/no-such-task") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert msg["payload"]["code"] == "not_found"
        close = ws.receive()
        assert close["type"] == "websocket.close"
