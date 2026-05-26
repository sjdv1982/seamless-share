"""Transformation dict diffing primitives."""

from __future__ import annotations

import json
from typing import Any

from .models import Classification, DeepDiff, DiffEntry, DiffSide, TransformationDiffResult, SCHEMA_VERSION

try:
    from seamless_transformer.pretransformation import NON_CHECKSUM_ITEMS as _NON_CHECKSUM_ITEMS
except Exception:  # pragma: no cover - optional workspace dependency
    _NON_CHECKSUM_ITEMS = (
        "__output__",
        "__language__",
        "__meta__",
        "__env__",
        "__format__",
        "__code_text__",
        "__code_checksum__",
        "__compiled__",
        "__compilation__",
        "__record_probe__",
        "__schema__",
        "__header__",
    )


def non_checksum_keys() -> set[str]:
    return set(_NON_CHECKSUM_ITEMS)


def is_dunder_key(key: str) -> bool:
    return (key.startswith("__") and key.endswith("__")) or key in non_checksum_keys()


def classify_key(key: str) -> str:
    return Classification.DUNDER.value if is_dunder_key(key) else Classification.PLAIN.value


def normalize_value(value: Any) -> Any:
    if isinstance(value, tuple):
        return [normalize_value(item) for item in value]
    if isinstance(value, list):
        return [normalize_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): normalize_value(value[key]) for key in sorted(value, key=str)}
    return value


def presentation_value(value: Any) -> Any:
    value = normalize_value(value)
    if (
        isinstance(value, list)
        and len(value) == 3
        and isinstance(value[2], str)
        and len(value[2]) == 64
    ):
        return value[2]
    if isinstance(value, str) and len(value) == 64:
        return value
    return value


def transformation_diff_core(
    dict_a: dict[str, Any],
    dict_b: dict[str, Any],
    *,
    side_a_only: str = DiffSide.KEY_ONLY_IN_A.value,
    side_b_only: str = DiffSide.KEY_ONLY_IN_B.value,
    deep_entries: dict[str, DeepDiff] | None = None,
) -> tuple[list[DiffEntry], bool, list[str]]:
    normalized_a = {str(key): normalize_value(value) for key, value in dict_a.items()}
    normalized_b = {str(key): normalize_value(value) for key, value in dict_b.items()}
    entries: list[DiffEntry] = []
    deep_entries = deep_entries or {}
    for key in sorted(set(normalized_a) | set(normalized_b)):
        has_a = key in normalized_a
        has_b = key in normalized_b
        if has_a and not has_b:
            side = side_a_only
        elif has_b and not has_a:
            side = side_b_only
        elif normalized_a[key] != normalized_b[key]:
            side = DiffSide.VALUE_DIFFERS.value
        else:
            continue
        value_a = presentation_value(normalized_a[key]) if has_a else None
        value_b = presentation_value(normalized_b[key]) if has_b else None
        entry = DiffEntry(
            side=side,
            key=key,
            classification=classify_key(key),
            value_A=value_a if side_a_only == DiffSide.KEY_ONLY_IN_A.value else None,
            value_B=value_b if side_b_only == DiffSide.KEY_ONLY_IN_B.value else None,
            value_input=value_a if side_a_only == DiffSide.KEY_ONLY_IN_INPUT.value else None,
            value_candidate=value_b if side_b_only == DiffSide.KEY_ONLY_IN_CANDIDATE.value else None,
            deep=deep_entries.get(key),
        )
        entries.append(entry)
    identity_relevant = any(entry.classification == Classification.PLAIN.value for entry in entries)
    warnings: list[str] = []
    if entries and not identity_relevant:
        warnings.append("dunder_only_diff")
    return entries, identity_relevant, warnings


def transformation_diff_result(
    ref_a: Any,
    ref_b: Any,
    *,
    deep_entries: dict[str, DeepDiff] | None = None,
    timing: dict[str, Any] | None = None,
) -> TransformationDiffResult:
    entries, identity_relevant, warnings = transformation_diff_core(
        ref_a.transformation_dict or {},
        ref_b.transformation_dict or {},
        deep_entries=deep_entries,
    )
    return TransformationDiffResult(
        tool="transformation-diff",
        version=SCHEMA_VERSION,
        input_A={
            "tf_checksum": ref_a.tf_checksum,
            "reference_form": ref_a.reference_form,
            "source_endpoint": ref_a.source_endpoint,
        },
        input_B={
            "tf_checksum": ref_b.tf_checksum,
            "reference_form": ref_b.reference_form,
            "source_endpoint": ref_b.source_endpoint,
        },
        identity_relevant=identity_relevant,
        entries=entries,
        warnings=warnings,
        timing=timing,
    )


def stable_json_loads(data: bytes) -> Any:
    return json.loads(data.decode("utf-8"))
