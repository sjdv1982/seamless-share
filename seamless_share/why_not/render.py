"""Human-readable rendering."""

from __future__ import annotations

from .models import TransformationDiffResult, WhyNotResult


def render_transformation_diff_text(
    result: TransformationDiffResult,
    *,
    quiet: bool = False,
    verbose: bool = False,
) -> str:
    lines = [
        f"A: {result.input_A.get('tf_checksum')}",
        f"B: {result.input_B.get('tf_checksum')}",
        f"identity_relevant: {'yes' if result.identity_relevant else 'no'}",
    ]
    if result.warnings:
        lines.append(f"warnings: {', '.join(result.warnings)}")
    if "dunder_only_diff" in result.warnings:
        lines.append("candidate does not explain the miss: diff entries are dunder-only")
    lines.append("diff:")
    for entry in result.entries:
        lines.append(
            f"  [{entry.classification}] {entry.side} {entry.key} "
            f"{entry.value_A if entry.value_A is not None else entry.value_input} "
            f"{entry.value_B if entry.value_B is not None else entry.value_candidate}"
        )
    if verbose and result.timing:
        lines.append(f"timing: {result.timing}")
    return "\n".join(lines)


def render_why_not_text(
    result: WhyNotResult,
    *,
    quiet: bool = False,
    verbose: bool = False,
) -> str:
    lines = [
        f"tf_checksum: {result.input.get('tf_checksum')}",
        f"lookup state: {result.lookup_state.state}",
        f"endpoints: {len(result.input.get('endpoint_set', []))}",
    ]
    if result.lookup_state.state == "PRESENT_AS_HIT":
        lines.append("this is a cache hit on the queried endpoint set - your miss is elsewhere")
    elif result.lookup_state.state == "PRESENT_RESULT_UNAVAILABLE":
        lines.append(f"result_checksum: {result.lookup_state.details.get('result_checksum')}")
        lines.append(f"reason: {result.lookup_state.details.get('reason')}")
        lines.append("this is not an identity miss - searching for a code change will not explain it")
    elif result.lookup_state.state == "IRREPRODUCIBLE":
        lines.append(f"irreproducible rows: {result.lookup_state.details.get('row_count', 0)}")
        lines.append(
            "result checksums: "
            + ", ".join(result.lookup_state.details.get("result_checksums", []))
        )
        lines.append("candidate diff is not run by default")
    elif result.candidate is None:
        lines.append("no near-enough candidate was found")
    else:
        if result.diff and "dunder_only_diff" in result.warnings:
            lines.append("candidate does not explain the miss: diff entries are dunder-only")
        lines.append(f"candidate: {result.candidate.tf_checksum}")
        if result.candidate.selection_score is not None:
            lines.append(f"selection score: {result.candidate.selection_score}")
        if result.diff:
            lines.append(
                f"identity-relevant: {'yes' if result.diff.get('identity_relevant') else 'no'}"
            )
            lines.append("diff:")
            for entry in result.diff.get("entries", []):
                lines.append(
                    f"  [{entry.classification}] {entry.side} {entry.key} "
                    f"{entry.value_input} {entry.value_candidate}"
                )
    if result.warnings and not quiet:
        lines.append(f"warnings: {', '.join(result.warnings)}")
    if verbose:
        for item in result.lookup_state.per_endpoint:
            lines.append(f"endpoint {item.endpoint}: {item.state} {item.details}")
        if result.timing:
            lines.append(f"timing: {result.timing}")
    return "\n".join(lines)
