"""Python API for transformation-diff and why-not."""

from __future__ import annotations

import time
from typing import Any

from .deep import build_deep_diff
from .diff import transformation_diff_core, transformation_diff_result
from .endpoints import open_endpoints
from .errors import DeepBufferUnavailable, UsageError
from .lookup import lookup_state
from .models import (
    CandidateInfo,
    DiffSide,
    LookupState,
    LookupStateName,
    Reference,
    SCHEMA_VERSION,
    TransformationDiffResult,
    WhyNotResult,
    to_json,
)
from .references import resolve_reference
from .selection import choose_candidate


def transformation_diff(
    ref_a: Reference | str,
    ref_b: Reference | str,
    *,
    endpoints: list[Any] | None = None,
    config: str | None = None,
    deep: bool = False,
    deep_best_effort: bool = False,
    verbose: bool = False,
) -> TransformationDiffResult:
    start = time.monotonic()
    opened = open_endpoints(endpoints, config=config)
    if not opened and (_is_checksum_reference(ref_a) or _is_checksum_reference(ref_b)):
        raise UsageError("checksum references require at least one endpoint")
    resolved_a = resolve_reference(ref_a, opened)
    resolved_b = resolve_reference(ref_b, opened)
    deep_entries, missing_deep = _deep_entries(
        resolved_a.transformation_dict or {},
        resolved_b.transformation_dict or {},
        opened,
        deep=deep,
    )
    timing = {"wall_ms": int((time.monotonic() - start) * 1000)} if verbose else None
    result = transformation_diff_result(
        resolved_a,
        resolved_b,
        deep_entries=deep_entries,
        timing=timing,
    )
    if missing_deep:
        result.warnings = sorted(set(result.warnings + ["deep_checksum_fallback"]))
        if not deep_best_effort:
            raise DeepBufferUnavailable(
                "deep diff requested but at least one buffer was unavailable",
                output=to_json(result),
                best_effort=False,
            )
    return result


def why_not(
    ref: Reference | str,
    *,
    endpoints: list[Any] | None = None,
    config: str | None = None,
    candidate: Reference | str | None = None,
    deep: bool = False,
    deep_best_effort: bool = False,
    explain_selection: bool = False,
    verbose: bool = False,
) -> WhyNotResult:
    start = time.monotonic()
    opened = open_endpoints(endpoints, config=config)
    if not opened and candidate is None:
        raise UsageError("why-not requires at least one endpoint unless --candidate is supplied")
    resolved_input = resolve_reference(ref, opened)
    state = lookup_state(opened, resolved_input.tf_checksum) if opened else LookupState(
        state=LookupStateName.NOT_PRESENT.value,
        per_endpoint=[],
        details={},
    )
    endpoint_set = [endpoint.spec.raw for endpoint in opened]
    result = WhyNotResult(
        tool="why-not",
        version=SCHEMA_VERSION,
        input={
            "tf_checksum": resolved_input.tf_checksum,
            "reference_form": resolved_input.reference_form,
            "endpoint_set": endpoint_set,
        },
        lookup_state=state,
        warnings=[],
        timing={"wall_ms": int((time.monotonic() - start) * 1000)} if verbose else None,
    )
    if state.state != LookupStateName.NOT_PRESENT.value:
        return result

    candidate_ref = None
    candidate_score = None
    runners_up: list[dict[str, Any]] = []
    selection_warnings: list[str] = []
    if candidate is not None:
        candidate_ref = resolve_reference(candidate, opened)
    else:
        records = [
            record
            for endpoint in opened
            for record in endpoint.iter_transformation_dicts()
            if record.tf_checksum != resolved_input.tf_checksum
        ]
        selected, candidate_score, runners_up, selection_warnings = choose_candidate(
            resolved_input.transformation_dict or {},
            records,
            explain_selection=explain_selection,
        )
        if selected is not None:
            candidate_ref = Reference.from_tf_checksum(selected.tf_checksum)
            candidate_ref.transformation_dict = selected.transformation_dict
            candidate_ref.source_endpoint = selected.endpoint
    result.warnings.extend(selection_warnings)
    if candidate_ref is None:
        return result

    result.candidate = CandidateInfo(
        tf_checksum=candidate_ref.tf_checksum,
        selection_score=candidate_score.selection_score if candidate_score else None,
        explanation=candidate_score.explanation() if candidate_score and explain_selection else None,
        runners_up=runners_up,
    )
    deep_entries, missing_deep = _deep_entries(
        resolved_input.transformation_dict or {},
        candidate_ref.transformation_dict or {},
        opened,
        deep=deep,
    )
    entries, identity_relevant, diff_warnings = transformation_diff_core(
        resolved_input.transformation_dict or {},
        candidate_ref.transformation_dict or {},
        side_a_only=DiffSide.KEY_ONLY_IN_INPUT.value,
        side_b_only=DiffSide.KEY_ONLY_IN_CANDIDATE.value,
        deep_entries=deep_entries,
    )
    if not entries:
        diff_warnings.append("empty_diff_check_implicit_closure")
    result.diff = {"identity_relevant": identity_relevant, "entries": entries}
    result.warnings = sorted(set(result.warnings + diff_warnings))
    if missing_deep:
        result.warnings = sorted(set(result.warnings + ["deep_checksum_fallback"]))
        if not deep_best_effort:
            raise DeepBufferUnavailable(
                "deep diff requested but at least one buffer was unavailable",
                output=to_json(result),
                best_effort=False,
            )
    if verbose:
        result.timing = {"wall_ms": int((time.monotonic() - start) * 1000)}
    return result


def _deep_entries(
    dict_a: dict[str, Any],
    dict_b: dict[str, Any],
    endpoints: list[Any],
    *,
    deep: bool,
) -> tuple[dict[str, Any], bool]:
    if not deep:
        return {}, False
    deep_entries = {}
    missing = False
    for key in sorted(set(dict_a) & set(dict_b)):
        if str(key) != "code":
            continue
        if dict_a[key] == dict_b[key]:
            continue
        deep_diff, unavailable = build_deep_diff(dict_a[key], dict_b[key], endpoints)
        if deep_diff is not None:
            deep_entries[str(key)] = deep_diff
        missing = missing or unavailable
    return deep_entries, missing


def _is_checksum_reference(ref: Reference | str) -> bool:
    if isinstance(ref, Reference):
        return ref.reference_form == "tf_checksum" and ref.transformation_dict is None
    if not isinstance(ref, str):
        return False
    import re

    return re.fullmatch(r"[0-9a-fA-F]{64}", ref) is not None
