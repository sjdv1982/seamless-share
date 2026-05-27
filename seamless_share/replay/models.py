"""Dataclasses and deterministic serialization for replay reports."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
import hashlib
import json
from typing import Any


SCHEMA_VERSION = "0.1.0"


FINDING_REQUIRED_FIELDS = {
    "unexpected_miss": ("tf_checksum", "script_position", "driver_context", "diff"),
    "unauthorized_materialization": (
        "checksum",
        "requested_by",
        "script_position",
        "available_authorizations",
    ),
    "unauthorized_fingertip": (
        "consumer_tf_checksum",
        "missing_input_checksum",
        "producer_tf_checksum",
        "script_position",
    ),
    "authorized_materialization_unsatisfied_dependency": (
        "authorized_target",
        "unsatisfied_dependency",
        "chain",
        "script_position",
    ),
    "remote_delegation_observed": ("backend", "dispatched_work", "script_position"),
    "unexpected_heavy_compute": (
        "tf_checksum",
        "was_driver",
        "observed_cost_ms",
        "correlated_miss",
    ),
    "irreproducible_only_hit": (
        "tf_checksum",
        "row_count",
        "result_checksums",
        "script_position",
    ),
    "authorization_incoherent": ("authorization", "reason"),
}


class OutcomePhase(str, Enum):
    COMPLETED = "completed"
    SCRIPT_ERROR = "script_error"
    TIMEOUT = "timeout"
    SETUP_ERROR = "setup_error"


@dataclass
class ArtifactInfo:
    seamless_db: str
    seamless_db_checksum: str
    bufferdir: str
    bufferdir_checksum: str


@dataclass
class AuthorizationSummary:
    bufferdir: str
    fingertips: list[str] = field(default_factory=list)
    driver_cache: str | None = None
    findings: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ReplayConfigInfo:
    synthesized: bool
    endpoints_resolved: dict[str, Any] = field(default_factory=dict)
    driver_cache: str = "bypass"
    config_path: str | None = None
    inherited: bool | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class Outcome:
    phase: str
    wall_ms: int
    script_exit_code: int | None = None
    message: str | None = None


@dataclass
class ReplayCounts:
    drivers_executed: int = 0
    drivers_short_circuited: int = 0
    transformations_submitted: int = 0
    cache_hits: int = 0
    buffers_materialized_from_bufferdir: int = 0
    buffers_materialized_via_authorized_fingertip: int = 0
    findings_by_kind: dict[str, int] = field(default_factory=dict)


@dataclass
class PostRunAssertions:
    seamless_db_unchanged: bool
    bufferdir_unchanged: bool


@dataclass
class Finding:
    kind: str
    id: str
    fields: dict[str, Any]
    script_position: str | None = None
    tf_checksum: str | None = None
    context: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        kind: str,
        fields: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
    ) -> "Finding":
        normalized = _normalize_finding_fields(kind, fields)
        return cls(
            kind=kind,
            id=finding_id(kind, normalized),
            fields=normalized,
            script_position=normalized.get("script_position"),
            tf_checksum=normalized.get("tf_checksum") or normalized.get("consumer_tf_checksum"),
            context=context or {},
        )


@dataclass
class ReplayReport:
    tool: str
    version: str
    artifact: ArtifactInfo
    authorization_summary: AuthorizationSummary
    config: ReplayConfigInfo
    outcome: Outcome
    counts: ReplayCounts
    findings: list[Finding] = field(default_factory=list)
    post_run_assertions: PostRunAssertions | None = None
    warnings: list[str] = field(default_factory=list)


def _normalize_finding_fields(kind: str, fields: dict[str, Any]) -> dict[str, Any]:
    result = {str(key): to_plain(value) for key, value in fields.items() if value is not None}
    for key in FINDING_REQUIRED_FIELDS.get(kind, ()):
        result.setdefault(key, None)
    if isinstance(result.get("chain"), list):
        result["chain"] = [to_plain(item) for item in result["chain"]]
    if isinstance(result.get("result_checksums"), list):
        result["result_checksums"] = sorted(str(item) for item in result["result_checksums"])
    return result


def finding_id(kind: str, fields: dict[str, Any]) -> str:
    required = FINDING_REQUIRED_FIELDS.get(kind, tuple(sorted(fields)))
    payload = {"kind": kind, "fields": {key: fields.get(key) for key in required}}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()[:16]


def sort_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(
        findings,
        key=lambda item: (
            item.script_position or "",
            item.kind,
            item.tf_checksum or "",
            item.id,
        ),
    )


def to_plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        result = {}
        for key, item in asdict(value).items():
            if item is not None:
                result[key] = to_plain(item)
        return result
    if isinstance(value, dict):
        return {str(key): to_plain(item) for key, item in value.items() if item is not None}
    if isinstance(value, (list, tuple)):
        return [to_plain(item) for item in value]
    return value


def to_json(value: Any) -> str:
    return json.dumps(to_plain(value), sort_keys=True, separators=(",", ":"))
