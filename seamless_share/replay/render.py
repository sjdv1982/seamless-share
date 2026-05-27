"""Render replay reports."""

from __future__ import annotations

from .models import ReplayReport, to_json


def render_replay_text(report: ReplayReport, quiet: bool = False, verbose: bool = False) -> str:
    lines = [
        "seamless-share replay",
        f"artifact: {report.artifact.seamless_db}",
        f"bufferdir: {report.artifact.bufferdir}",
        f"outcome: {report.outcome.phase}",
    ]
    if report.outcome.script_exit_code is not None:
        lines.append(f"script_exit_code: {report.outcome.script_exit_code}")
    lines.append(f"findings: {len(report.findings)}")
    for key, value in sorted(report.counts.findings_by_kind.items()):
        lines.append(f"  {key}: {value}")
    if report.post_run_assertions is not None:
        lines.append(
            "read_only: "
            f"db={report.post_run_assertions.seamless_db_unchanged} "
            f"bufferdir={report.post_run_assertions.bufferdir_unchanged}"
        )
    if report.config.warnings:
        lines.append("warnings: " + ", ".join(report.config.warnings))
    if not quiet:
        for finding in report.findings:
            detail = finding.tf_checksum or finding.fields.get("checksum") or ""
            lines.append(f"- {finding.kind} {finding.id} {detail}".rstrip())
            if verbose:
                lines.append(f"  fields: {finding.fields}")
    return "\n".join(lines)
