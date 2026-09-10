"""共享 v0.3 领域契约的 Python 边界模型（数字孪生侧）。

与 packages/contracts/domain.schema.json 的 v0.3 $defs、
packages/contracts/src/domain.ts 第 11–14 节表达完全相同的字段、枚举与必填规则；
条件规则（matched/unmatched/needs_review 的字段约束、success 不带原因、
completed_with_exceptions 必须可追溯等）在本模块以校验器表达，
与 Schema 的 if/then 一一对应。

黄金夹具：data/contract-fixtures/instrument-v0.3/。
v0.3 对象载荷携带 contract_version: "0.3"；其他契约版本一律拒绝。
marker_proxy 检测器不等于通用图像识别，unmatched/needs_review 不产生
已确认的客户设备 ID；契约不包含连续原始视频展示或上传。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

DOMAIN_CONTRACT_VERSION = Literal["0.3"]

SourceType = Literal["simulation", "replay", "live_pending"]
ProvenanceStatus = Literal["simulated", "pending_confirmation", "confirmed"]


class StrictModel(BaseModel):
    """Forbid undeclared fields and non-finite numeric values."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Provenance(StrictModel):
    source: SourceType
    status: ProvenanceStatus


DataSourceKind = Literal["synthetic_fixture", "simulation", "replay", "live_pending"]


class DataLineage(StrictModel):
    """逐对象数据血缘：区分 synthetic_fixture / simulation / replay / live_pending。"""

    source: DataSourceKind
    producer: str | None
    producer_version: str | None
    derived_from: str | None


class Position3D(StrictModel):
    x: float
    y: float
    z: float


NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]


# ---------------------------------------------------------------------------
# 仪器观测
# ---------------------------------------------------------------------------

InstrumentDetectorKind = Literal["marker_proxy"]


class ImageRegion(StrictModel):
    """归一化图像区域；0 ≤ x_min < x_max ≤ 1，0 ≤ y_min < y_max ≤ 1。"""

    x_min: float = Field(ge=0.0, le=1.0)
    y_min: float = Field(ge=0.0, le=1.0)
    x_max: float = Field(ge=0.0, le=1.0)
    y_max: float = Field(ge=0.0, le=1.0)
    image_width: int = Field(ge=1)
    image_height: int = Field(ge=1)

    @model_validator(mode="after")
    def _region_ordered(self) -> ImageRegion:
        if not (self.x_min < self.x_max and self.y_min < self.y_max):
            raise ValueError("image region must satisfy x_min < x_max and y_min < y_max")
        return self


class InstrumentDetector(StrictModel):
    kind: InstrumentDetectorKind
    version: NonEmptyStr


class InstrumentObservation(StrictModel):
    """仪器观测：稳定 ID、运行 ID、采集时间、图像区域、检测器、置信度、标定引用。"""

    contract_version: DOMAIN_CONTRACT_VERSION
    observation_id: NonEmptyStr
    run_id: NonEmptyStr
    captured_at: NonEmptyStr
    image_region: ImageRegion
    detector: InstrumentDetector
    confidence: float = Field(ge=0.0, le=1.0)
    calibration_ref: NonEmptyStr
    source_type: SourceType
    provenance: Provenance
    lineage: DataLineage


# ---------------------------------------------------------------------------
# 仪器定位与资产匹配（相互独立）
# ---------------------------------------------------------------------------

ResolutionStatus = Literal["matched", "unmatched", "needs_review"]


class InstrumentLocalization(StrictModel):
    """仪器定位：观测 → scene_local_yup 位姿；与资产匹配相互独立。"""

    contract_version: DOMAIN_CONTRACT_VERSION
    localization_id: NonEmptyStr
    run_id: NonEmptyStr
    observation_id: NonEmptyStr
    status: ResolutionStatus
    position: Position3D | None
    heading_deg: float | None
    method: NonEmptyStr
    residual_m: float | None
    source_type: SourceType
    provenance: Provenance
    lineage: DataLineage

    @model_validator(mode="after")
    def _status_consistent(self) -> InstrumentLocalization:
        if self.status == "matched":
            if self.position is None or self.heading_deg is None:
                raise ValueError("matched localization requires position and heading_deg")
        if self.status == "unmatched":
            if self.position is not None or self.heading_deg is not None:
                raise ValueError("unmatched localization must not carry position or heading_deg")
        return self


class AssetMatch(StrictModel):
    """资产匹配：定位 → 登记资产 ID；unmatched/needs_review 不确认客户设备 ID。"""

    contract_version: DOMAIN_CONTRACT_VERSION
    match_id: NonEmptyStr
    run_id: NonEmptyStr
    localization_id: NonEmptyStr
    status: ResolutionStatus
    asset_id: NonEmptyStr | None
    candidate_asset_ids: list[Annotated[str, StringConstraints(min_length=1)]]
    rule_version: NonEmptyStr
    source_type: SourceType
    provenance: Provenance
    lineage: DataLineage

    @model_validator(mode="after")
    def _status_consistent(self) -> AssetMatch:
        if self.status == "matched":
            if self.asset_id is None:
                raise ValueError("matched asset match requires asset_id")
            if self.candidate_asset_ids:
                raise ValueError("matched asset match must not carry candidate_asset_ids")
        if self.status == "unmatched":
            if self.asset_id is not None or self.candidate_asset_ids:
                raise ValueError("unmatched asset match must not carry asset_id or candidates")
        if self.status == "needs_review":
            if self.asset_id is not None:
                raise ValueError("needs_review asset match must not carry a confirmed asset_id")
            if not self.candidate_asset_ids:
                raise ValueError("needs_review asset match requires at least one candidate")
        return self


# ---------------------------------------------------------------------------
# 命名指标读数
# ---------------------------------------------------------------------------

InstrumentMetricName = Literal["deep_base", "shallow_base", "delta"]


class NamedMetric(StrictModel):
    """命名指标：深基点 / 浅基点 / 差值，不复用单一匿名 value 语义。"""

    name: InstrumentMetricName
    value: float
    unit: NonEmptyStr


ReadingQuality = Literal["good", "uncertain", "bad"]


class InstrumentReading(StrictModel):
    """仪器读数：一组命名指标；valid_until 过期后不得作为当前状态展示。"""

    contract_version: DOMAIN_CONTRACT_VERSION
    reading_id: NonEmptyStr
    run_id: NonEmptyStr
    asset_id: NonEmptyStr
    captured_at: NonEmptyStr
    metrics: list[NamedMetric] = Field(min_length=1)
    quality: ReadingQuality
    valid_until: NonEmptyStr | None
    source_type: SourceType
    provenance: Provenance
    lineage: DataLineage

    @field_validator("metrics")
    @classmethod
    def _metric_names_unique(cls, value: list[NamedMetric]) -> list[NamedMetric]:
        names = [metric.name for metric in value]
        if len(names) != len(set(names)):
            raise ValueError("metrics must not repeat a metric name")
        return value


# ---------------------------------------------------------------------------
# 巡检站 / 站点尝试 / 运行结果溯源
# ---------------------------------------------------------------------------


class InspectionStation(StrictModel):
    """巡检站：独立稳定 ID + 目标位姿 + 注册仪器；不等同于基准 Tag。"""

    contract_version: DOMAIN_CONTRACT_VERSION
    station_id: NonEmptyStr
    scene_id: NonEmptyStr
    name: NonEmptyStr
    target_pose: Position3D
    target_heading_deg: float = Field(ge=0.0, le=360.0)
    registered_instruments: list[Annotated[str, StringConstraints(min_length=1)]]
    description: str | None = None
    source_type: SourceType
    provenance: Provenance
    lineage: DataLineage


StationAttemptResult = Literal["success", "failed", "skipped"]

StationAttemptReasonCode = Literal[
    "observation_failed",
    "localization_unresolved",
    "asset_unmatched",
    "instrument_unavailable",
    "operator_skipped",
    "route_interrupted",
]

_REASON_CODES = {
    "observation_failed",
    "localization_unresolved",
    "asset_unmatched",
    "instrument_unavailable",
    "operator_skipped",
    "route_interrupted",
}


class StationAttempt(StrictModel):
    """一次站点尝试；attempt_seq > 1 即发生过 retry，可追溯。"""

    contract_version: DOMAIN_CONTRACT_VERSION
    attempt_id: NonEmptyStr
    run_id: NonEmptyStr
    station_id: NonEmptyStr
    attempt_seq: int = Field(ge=1)
    started_at: NonEmptyStr
    ended_at: NonEmptyStr
    result: StationAttemptResult
    reason_code: str | None
    reason_detail: str | None
    source_type: SourceType
    provenance: Provenance
    lineage: DataLineage

    @model_validator(mode="after")
    def _reason_consistent(self) -> StationAttempt:
        if self.result == "success":
            if self.reason_code is not None:
                raise ValueError("successful attempt must not carry a reason_code")
        elif self.reason_code is None:
            raise ValueError("failed/skipped attempt requires an explicit reason_code")
        elif self.reason_code not in _REASON_CODES:
            raise ValueError(f"unknown reason_code: {self.reason_code!r}")
        return self


InspectionRunFinalStatus = Literal["completed", "completed_with_exceptions", "aborted"]

RunAbortReasonCode = Literal["operator_abort", "safety_stop", "system_fault"]


class RunAbort(StrictModel):
    reason_code: RunAbortReasonCode
    detail: str | None


class InspectionRunOutcome(StrictModel):
    """运行结果溯源：终态 + 站点级 skip/retry 汇总，与包契约的带例外完成一致。"""

    contract_version: DOMAIN_CONTRACT_VERSION
    run_id: NonEmptyStr
    final_status: InspectionRunFinalStatus
    skipped_station_ids: list[Annotated[str, StringConstraints(min_length=1)]]
    retried_station_ids: list[Annotated[str, StringConstraints(min_length=1)]]
    abort: RunAbort | None
    source_type: SourceType
    provenance: Provenance
    lineage: DataLineage

    @model_validator(mode="after")
    def _outcome_consistent(self) -> InspectionRunOutcome:
        if self.final_status == "completed":
            if self.skipped_station_ids or self.retried_station_ids or self.abort is not None:
                raise ValueError("completed run must not carry skips, retries or abort")
        if self.final_status == "completed_with_exceptions":
            if not self.skipped_station_ids and not self.retried_station_ids:
                raise ValueError(
                    "completed_with_exceptions requires at least one skip or retry"
                )
            if self.abort is not None:
                raise ValueError("completed_with_exceptions run must not carry abort")
        if self.final_status == "aborted" and self.abort is None:
            raise ValueError("aborted run requires an abort reason")
        return self


# 公共边界清单：夹具验证与契约同步锁测试使用。
V0_3_BOUNDARY_MODELS: dict[str, type[StrictModel]] = {
    "InstrumentObservation": InstrumentObservation,
    "InstrumentLocalization": InstrumentLocalization,
    "AssetMatch": AssetMatch,
    "InstrumentReading": InstrumentReading,
    "InspectionStation": InspectionStation,
    "StationAttempt": StationAttempt,
    "InspectionRunOutcome": InspectionRunOutcome,
}
