"""Dataclasses and deterministic serialization for why-not results."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "0.1.0"


class ReferenceForm(str, Enum):
    TF_CHECKSUM = "tf_checksum"
    DICT_PATH = "dict_path"
    DEFINITION_PATH = "definition_path"


class EndpointKind(str, Enum):
    LOCAL_SQLITE = "local_sqlite"
    REMOTE_URL = "remote_url"
    NAMED = "named"


class LookupStateName(str, Enum):
    NOT_PRESENT = "NOT_PRESENT"
    IRREPRODUCIBLE = "IRREPRODUCIBLE"
    PRESENT_RESULT_UNAVAILABLE = "PRESENT_RESULT_UNAVAILABLE"
    PRESENT_AS_HIT = "PRESENT_AS_HIT"


class DiffSide(str, Enum):
    KEY_ONLY_IN_A = "key_only_in_A"
    KEY_ONLY_IN_B = "key_only_in_B"
    KEY_ONLY_IN_INPUT = "key_only_in_input"
    KEY_ONLY_IN_CANDIDATE = "key_only_in_candidate"
    VALUE_DIFFERS = "value_differs"


class Classification(str, Enum):
    PLAIN = "plain"
    LOAD_BEARING_DUNDER = "load_bearing_dunder"
    ORTHOGONAL_DUNDER = "orthogonal_dunder"
    DERIVED_DUNDER = "derived_dunder"


class DeepKind(str, Enum):
    TEXT_DIFF = "text_diff"
    JSON_DIFF = "json_diff"
    CHECKSUM_FALLBACK = "checksum_fallback"


@dataclass
class Reference:
    original: str
    reference_form: str
    tf_checksum: str | None = None
    transformation_dict: dict[str, Any] | None = None
    source_endpoint: str | None = None

    @classmethod
    def from_tf_checksum(cls, checksum: str) -> "Reference":
        return cls(original=checksum, reference_form=ReferenceForm.TF_CHECKSUM.value, tf_checksum=checksum)

    @classmethod
    def from_dict_path(cls, path: str | Path) -> "Reference":
        return cls(original=str(path), reference_form=ReferenceForm.DICT_PATH.value)

    @classmethod
    def from_definition_path(cls, path: str | Path) -> "Reference":
        return cls(original=str(path), reference_form=ReferenceForm.DEFINITION_PATH.value)

    @classmethod
    def from_str(cls, value: str) -> "Reference":
        import re

        if re.fullmatch(r"[0-9a-fA-F]{64}", value):
            return cls.from_tf_checksum(value.lower())
        path = Path(value)
        if path.suffix.lower() == ".json":
            return cls.from_dict_path(path)
        return cls.from_definition_path(path)


@dataclass
class EndpointSpec:
    raw: str
    endpoint_kind: str
    path: str | None = None
    url: str | None = None
    display_name: str | None = None

    @classmethod
    def from_path(cls, path: str | Path) -> "EndpointSpec":
        path_str = str(path)
        return cls(
            raw=path_str,
            endpoint_kind=EndpointKind.LOCAL_SQLITE.value,
            path=path_str,
            display_name=Path(path_str).name,
        )

    @classmethod
    def from_str(cls, spec: str) -> "EndpointSpec":
        if spec.startswith(("http://", "https://")):
            return cls(raw=spec, endpoint_kind=EndpointKind.REMOTE_URL.value, url=spec, display_name=spec)
        if "://" in spec:
            return cls(raw=spec, endpoint_kind=EndpointKind.NAMED.value, display_name=spec)
        return cls.from_path(spec)


@dataclass
class DeepDiff:
    kind: str
    body: Any = None
    fallback_reason: str | None = None


@dataclass
class DiffEntry:
    side: str
    key: str
    classification: str
    value_A: Any = None
    value_B: Any = None
    value_input: Any = None
    value_candidate: Any = None
    deep: DeepDiff | None = None


@dataclass
class TransformationDiffResult:
    tool: str
    version: str
    input_A: dict[str, Any]
    input_B: dict[str, Any]
    identity_relevant: bool
    entries: list[DiffEntry]
    warnings: list[str] = field(default_factory=list)
    timing: dict[str, Any] | None = None


@dataclass
class EndpointLookupState:
    endpoint: str
    state: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class LookupState:
    state: str
    per_endpoint: list[EndpointLookupState] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class CandidateInfo:
    tf_checksum: str
    selection_score: float | None = None
    explanation: dict[str, Any] | None = None
    runners_up: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class WhyNotResult:
    tool: str
    version: str
    input: dict[str, Any]
    lookup_state: LookupState
    candidate: CandidateInfo | None = None
    diff: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    timing: dict[str, Any] | None = None


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
