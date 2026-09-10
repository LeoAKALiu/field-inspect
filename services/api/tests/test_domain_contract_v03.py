"""共享 v0.3 领域契约：黄金夹具 × 双公共边界验证与契约同步锁。

对应 LeoAKALiu/digital-twin#1：
- 每个合法黄金夹具必须同时通过 JSON Schema（domain.schema.json 的 v0.3 $defs）
  与 Python 边界模型（app.domain_contract）两个公共边界；
- 每个非法夹具（缺字段、空指标、unmatched 携带位姿、success 带原因、
  未知 contract_version）必须被两个边界同时拒绝；
- Python 边界模型与 JSON Schema 的必填字段保持一致（同步锁）；
- marker_proxy 的语义边界（不等于通用图像识别）在契约文件中显式标记。
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from app.domain_contract import V0_3_BOUNDARY_MODELS, InstrumentObservation

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_DIR = REPO_ROOT / "data/contract-fixtures/instrument-v0.3"
SCHEMA_PATH = REPO_ROOT / "packages/contracts/domain.schema.json"
OPENAPI_PATH = REPO_ROOT / "packages/contracts/openapi.yaml"
TS_PATH = REPO_ROOT / "packages/contracts/src/domain.ts"

VALID_FIXTURE_DEFS: dict[str, str] = {
    "observation-marker-proxy-simulation.json": "InstrumentObservation",
    "observation-synthetic-fixture.json": "InstrumentObservation",
    "localization-matched.json": "InstrumentLocalization",
    "localization-unmatched.json": "InstrumentLocalization",
    "localization-needs-review.json": "InstrumentLocalization",
    "asset-match-matched.json": "AssetMatch",
    "asset-match-unmatched.json": "AssetMatch",
    "asset-match-needs-review.json": "AssetMatch",
    "reading-historical-replay.json": "InstrumentReading",
    "reading-expired.json": "InstrumentReading",
    "station-registered.json": "InspectionStation",
    "attempt-failed-then-retried.json": "StationAttempt",
    "attempt-success-after-retry.json": "StationAttempt",
    "attempt-skipped.json": "StationAttempt",
    "run-outcome-completed.json": "InspectionRunOutcome",
    "run-outcome-completed-with-exceptions.json": "InspectionRunOutcome",
    "run-outcome-aborted.json": "InspectionRunOutcome",
}

INVALID_FIXTURE_DEFS: dict[str, str] = {
    "malformed-observation-missing-confidence.json": "InstrumentObservation",
    "malformed-reading-empty-metrics.json": "InstrumentReading",
    "malformed-localization-unmatched-with-position.json": "InstrumentLocalization",
    "malformed-attempt-success-with-reason.json": "StationAttempt",
    "unknown-contract-version.json": "InstrumentObservation",
}

STATION_ATTEMPT_REASON_CODES = {
    "observation_failed",
    "localization_unresolved",
    "asset_unmatched",
    "instrument_unavailable",
    "operator_skipped",
    "route_interrupted",
}


def _def_validator(def_name: str) -> jsonschema.Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(
        {"$ref": f"#/$defs/{def_name}", "$defs": schema["$defs"]},
        format_checker=jsonschema.FormatChecker(),
    )


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("fixture_name", sorted(VALID_FIXTURE_DEFS))
def test_valid_fixtures_pass_both_public_boundaries(fixture_name: str) -> None:
    def_name = VALID_FIXTURE_DEFS[fixture_name]
    payload = _load_fixture(fixture_name)

    schema_errors = list(_def_validator(def_name).iter_errors(payload))
    assert not schema_errors, [e.message for e in schema_errors]

    V0_3_BOUNDARY_MODELS[def_name].model_validate(payload)


@pytest.mark.parametrize("fixture_name", sorted(INVALID_FIXTURE_DEFS))
def test_invalid_fixtures_rejected_by_both_public_boundaries(fixture_name: str) -> None:
    def_name = INVALID_FIXTURE_DEFS[fixture_name]
    payload = _load_fixture(fixture_name)

    schema_errors = list(_def_validator(def_name).iter_errors(payload))
    assert schema_errors, f"schema boundary must reject {fixture_name}"

    with pytest.raises(ValidationError):
        V0_3_BOUNDARY_MODELS[def_name].model_validate(payload)


def test_golden_set_covers_issue_scenarios() -> None:
    """成功 / 未匹配 / 待复核 / 历史读数 / 过期读数 / 缺失读数 / 血缘类别全部可达。"""
    names = set(VALID_FIXTURE_DEFS)
    assert "localization-matched.json" in names
    assert "localization-unmatched.json" in names
    assert "asset-match-needs-review.json" in names
    assert "reading-historical-replay.json" in names
    assert "reading-expired.json" in names
    assert "run-outcome-completed-with-exceptions.json" in names

    observation = _load_fixture("observation-synthetic-fixture.json")
    assert observation["lineage"]["source"] == "synthetic_fixture"
    reading = _load_fixture("reading-historical-replay.json")
    assert reading["lineage"]["source"] == "replay"

    # 缺失读数：stn-002 在本夹具集中没有任何读数（显式缺失，不用演示数据补齐），
    # 其可追溯表达是 attempt-skipped + completed_with_exceptions。
    stn002_attempt = _load_fixture("attempt-skipped.json")
    assert stn002_attempt["station_id"] == "stn-002"
    reading_asset_ids = {
        _load_fixture(name)["asset_id"]
        for name in ("reading-historical-replay.json", "reading-expired.json")
    }
    assert "ast-delam-002" not in reading_asset_ids

    metrics = _load_fixture("reading-historical-replay.json")["metrics"]
    assert {metric["name"] for metric in metrics} == {"deep_base", "shallow_base", "delta"}


@pytest.mark.parametrize("def_name", sorted(V0_3_BOUNDARY_MODELS))
def test_contract_sync_lock_python_schema_required(def_name: str) -> None:
    """Python 边界模型与 JSON Schema 的必填字段一致（同步锁）。"""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    model = V0_3_BOUNDARY_MODELS[def_name]

    pydantic_required = set(model.model_json_schema()["required"])
    schema_required = set(schema["$defs"][def_name]["required"])
    assert pydantic_required == schema_required, def_name


def test_contract_sync_lock_enums() -> None:
    """枚举与常量在 Schema 层锁定，且与 Python/TS 侧语义一致。"""
    defs = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))["$defs"]

    assert set(defs["ResolutionStatus"]["enum"]) == {"matched", "unmatched", "needs_review"}
    assert set(defs["InstrumentMetricName"]["enum"]) == {"deep_base", "shallow_base", "delta"}
    assert set(defs["StationAttemptResult"]["enum"]) == {"success", "failed", "skipped"}
    assert set(defs["StationAttemptReasonCode"]["enum"]) == STATION_ATTEMPT_REASON_CODES
    assert set(defs["InspectionRunFinalStatus"]["enum"]) == {
        "completed",
        "completed_with_exceptions",
        "aborted",
    }
    assert set(defs["DataSourceKind"]["enum"]) == {
        "synthetic_fixture",
        "simulation",
        "replay",
        "live_pending",
    }
    assert defs["InstrumentDetectorKind"]["const"] == "marker_proxy"
    assert defs["InstrumentObservation"]["properties"]["contract_version"]["const"] == "0.3"


def test_marker_proxy_boundary_is_explicit() -> None:
    """契约与升级说明必须显式标记 marker_proxy ≠ 通用图像识别、不确认设备 ID。"""
    schema_text = SCHEMA_PATH.read_text(encoding="utf-8")
    assert "不等于通用图像识别" in schema_text
    assert "不构成已确认的客户设备 ID" in schema_text

    openapi_text = OPENAPI_PATH.read_text(encoding="utf-8")
    assert "./domain.schema.json#/$defs/InstrumentObservation" in openapi_text
    assert "./domain.schema.json#/$defs/InspectionRunOutcome" in openapi_text
    assert "不等于通用图像识别" in openapi_text

    ts_text = TS_PATH.read_text(encoding="utf-8")
    assert "不等于通用图像识别" in ts_text
    assert "contract_version: '0.3'" in ts_text


def test_unknown_contract_version_constant_rejected() -> None:
    payload = _load_fixture("unknown-contract-version.json")
    assert payload["contract_version"] == "0.2"
    with pytest.raises(ValidationError):
        InstrumentObservation.model_validate(payload)
