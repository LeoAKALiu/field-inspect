"""Station evidence derived from recorded public route diagnostics."""

import json

import pytest

from inspection_pipeline.station_attempts import StationEvidenceError, build_station_attempts


def sample(stamp, state, outcomes=(), *, execution="exec-1", started=110, station="s1"):
    return (stamp, {
        "route_id": "route-1", "route_sha256": "a" * 64,
        "execution_id": execution, "execution_started_ns": str(started),
        "route_state": state, "station_id": station,
        "station_outcomes": json.dumps([
            {"station_id": sid, "outcome": result} for sid, result in outcomes
        ]),
    })


def build(samples, *, status="completed_with_exceptions", stations=("s1", "s2")):
    return build_station_attempts(
        samples, run_id="run-1", route_id="route-1", route_sha256="a" * 64,
        station_ids=stations, bag_start_ns=100, bag_end_ns=500, run_status=status,
    )


def test_completed_and_skipped_stations_keep_recorded_windows_and_stable_ids():
    records = [
        sample(120, "arming"),
        sample(200, "at_station"),
        sample(220, "arming", (("s1", "completed"),), station="s2"),
        sample(250, "arming", (("s1", "completed"),), station="s2"),
        sample(400, "completed_with_exceptions",
               (("s1", "completed"), ("s2", "skipped")), station=""),
    ]
    attempts = build(records)
    assert [(x["station_id"], x["result"], x["reason_code"]) for x in attempts] == [
        ("s1", "success", None), ("s2", "skipped", "operator_skipped"),
    ]
    assert attempts[0]["mcap_range"] == {
        "bag_path": "bag/run-1", "start_ns": 110, "end_ns": 220,
        "clock": "rosbag_receive_time", "bounds_kind": "execution_to_result_status",
    }
    assert attempts == build(records)
    assert len({x["attempt_id"] for x in attempts}) == 2


def test_cancellation_and_retry_preserve_failure_and_ignore_old_execution():
    records = [
        sample(120, "arming"), sample(200, "canceled"),
        sample(300, "arming", execution="exec-2", started=290),
        sample(310, "canceled"),
        sample(400, "completed", (("s1", "completed"),),
               execution="exec-2", started=290, station=""),
    ]
    attempts = build(records, stations=("s1",))
    assert [(x["result"], x["attempt_seq"], x["reason_code"]) for x in attempts] == [
        ("failed", 1, "route_interrupted"), ("success", 2, None),
    ]
    assert attempts[0]["mcap_range"]["end_ns"] == 200
    assert attempts[1]["mcap_range"]["start_ns"] == 290


def test_recording_ends_mid_station_retains_explicit_interruption():
    attempts = build([sample(120, "at_station")], status="incomplete", stations=("s1",))
    assert attempts[0]["result"] == "failed"
    assert attempts[0]["reason_code"] == "route_interrupted"
    assert attempts[0]["mcap_range"]["end_ns"] == 500
    assert attempts[0]["mcap_range"]["bounds_kind"] == "recording_end_without_result"


@pytest.mark.parametrize("patch", [
    {"station_outcomes": "not-json"},
    {"station_outcomes": '{}'},
    {"station_outcomes": '[{"station_id":"s1","outcome":"invented"}]'},
    {"station_outcomes": '[{"station_id":"foreign","outcome":"completed"}]'},
    {"station_outcomes": '[{"station_id":"s1","outcome":"completed","asset_id":"x"}]'},
    {"station_id": "foreign"},
    {"route_state": "invented"},
    {"execution_started_ns": "NaN"},
    {"execution_started_ns": "600"},
])
def test_malformed_station_evidence_is_rejected(patch):
    stamp, values = sample(120, "at_station")
    with pytest.raises(StationEvidenceError):
        build([(stamp, {**values, **patch})], status="incomplete")


@pytest.mark.parametrize("records,status", [
    ([], "completed"),
    ([sample(120, "at_station")], "completed"),
    ([sample(200, "completed", (("s1", "skipped"),), station="")], "completed"),
    ([sample(200, "completed", (("s1", "completed"),), station="")],
     "completed_with_exceptions"),
    ([sample(200, "arming"), sample(199, "at_station")], "incomplete"),
    ([sample(200, "arming"), sample(210, "arming", started=120)], "incomplete"),
    ([sample(200, "arming"), sample(210, "arming", execution="other")], "incomplete"),
    ([sample(200, "arming", (("s1", "completed"),), station="s2"),
      sample(300, "arming", (("s1", "skipped"),), station="s2")], "incomplete"),
])
def test_missing_conflicting_or_falsely_completed_evidence_is_rejected(records, status):
    with pytest.raises(StationEvidenceError):
        build(records, status=status)


def test_heartbeats_can_recover_cumulative_results_without_inventing_arrival_times():
    attempts = build([
        sample(400, "completed", (("s1", "completed"), ("s2", "completed")), station=""),
    ], status="completed")
    assert [x["mcap_range"]["start_ns"] for x in attempts] == [110, 110]
    assert [x["mcap_range"]["end_ns"] for x in attempts] == [400, 400]
    assert all("arrived_at" not in x for x in attempts)
