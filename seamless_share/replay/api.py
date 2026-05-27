"""Python API for replay-mode verification."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
from typing import Sequence

from .auth import AuthorizationError, AuthorizationSpec
from .config import ReplayConfig
from .models import (
    ArtifactInfo,
    AuthorizationSummary,
    Finding,
    Outcome,
    OutcomePhase,
    PostRunAssertions,
    ReplayCounts,
    ReplayReport,
    SCHEMA_VERSION,
    sort_findings,
    to_json,
)
from .report import bufferdir_manifest_sha256, file_sha256, parse_event_file


class ReplayUsageError(ValueError):
    """Usage error that maps to CLI exit code 2."""


class ReplaySetupError(RuntimeError):
    """Setup error that maps to CLI exit code 3."""

    def __init__(self, message: str, report: ReplayReport | None = None):
        super().__init__(message)
        self.report = report


def replay(
    *,
    script: str,
    script_args: Sequence[str] | None = None,
    artifact: str,
    bufferdir: str,
    authorization: AuthorizationSpec | str | None = None,
    driver_cache: str = "bypass",
    config: ReplayConfig | None = None,
    timeout: float | None = None,
    allow_remote: bool = False,
) -> ReplayReport:
    start = time.monotonic()
    script_args = list(script_args or [])
    config = config or ReplayConfig.synthesized()
    script_path = Path(script)
    artifact_path = Path(artifact)
    bufferdir_path = Path(bufferdir)
    _validate_usage(script_path, artifact_path, bufferdir_path, driver_cache)
    auth = _coerce_authorization(authorization)
    artifact_info, pre_db, pre_buffer = _artifact_info(artifact_path, bufferdir_path)
    config_info = config.to_info(
        artifact=str(artifact_path.resolve()),
        bufferdir=str(bufferdir_path.resolve()),
        driver_cache=driver_cache,
    )
    auth_summary = AuthorizationSummary(
        bufferdir=str(bufferdir_path.resolve()),
        fingertips=auth.fingertips,
        driver_cache=auth.driver_cache,
    )
    startup_findings = [
        Finding.build("authorization_incoherent", item)
        for item in auth.incoherence_findings(artifact_path, driver_cache)
    ]
    with tempfile.TemporaryDirectory(prefix="seamless-share-replay-") as tmpdir:
        tmp = Path(tmpdir)
        event_path = tmp / "events.jsonl"
        runtime_auth = tmp / "authorization.json"
        runtime_auth.write_text(
            json.dumps(
                {"fingertips": auth.fingertips, "driver_cache": auth.driver_cache},
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        env = _child_env(
            event_path=event_path,
            authorization_path=runtime_auth,
            artifact=artifact_path,
            bufferdir=bufferdir_path,
            driver_cache=driver_cache,
            config=config,
            allow_remote=allow_remote,
        )
        try:
            proc = subprocess.run(
                [sys.executable, str(script_path), *script_args],
                text=True,
                cwd=os.getcwd(),
                env=env,
                timeout=timeout,
                check=False,
            )
            phase = OutcomePhase.COMPLETED.value if proc.returncode == 0 else OutcomePhase.SCRIPT_ERROR.value
            script_exit = proc.returncode
            message = None
        except subprocess.TimeoutExpired:
            phase = OutcomePhase.TIMEOUT.value
            script_exit = None
            message = "script timed out"
        counts, event_findings = parse_event_file(event_path)
    findings = sort_findings(startup_findings + event_findings)
    counts.findings_by_kind = _finding_counts(findings)
    post_db = file_sha256(artifact_path)
    post_buffer = bufferdir_manifest_sha256(bufferdir_path)
    return ReplayReport(
        tool="replay",
        version=SCHEMA_VERSION,
        artifact=artifact_info,
        authorization_summary=replace(auth_summary, findings=[item.fields for item in startup_findings]),
        config=config_info,
        outcome=Outcome(
            phase=phase,
            wall_ms=int((time.monotonic() - start) * 1000),
            script_exit_code=script_exit,
            message=message,
        ),
        counts=counts,
        findings=findings,
        post_run_assertions=PostRunAssertions(
            seamless_db_unchanged=pre_db == post_db,
            bufferdir_unchanged=pre_buffer == post_buffer,
        ),
        warnings=config_info.warnings,
    )


def setup_error_report(message: str, *, artifact: str = "", bufferdir: str = "") -> ReplayReport:
    return ReplayReport(
        tool="replay",
        version=SCHEMA_VERSION,
        artifact=ArtifactInfo(
            seamless_db=artifact,
            seamless_db_checksum="",
            bufferdir=bufferdir,
            bufferdir_checksum="",
        ),
        authorization_summary=AuthorizationSummary(bufferdir=bufferdir),
        config=ReplayConfig.synthesized().to_info(artifact=artifact, bufferdir=bufferdir, driver_cache="bypass"),
        outcome=Outcome(phase=OutcomePhase.SETUP_ERROR.value, wall_ms=0, message=message),
        counts=ReplayCounts(),
        findings=[],
        post_run_assertions=PostRunAssertions(False, False),
    )


def write_report(report: ReplayReport, path: str | Path, report_format: str) -> None:
    output = to_json(report) if report_format == "json" else _text(report)
    Path(path).write_text(output + "\n", encoding="utf-8")


def _text(report: ReplayReport) -> str:
    from .render import render_replay_text

    return render_replay_text(report)


def _validate_usage(script: Path, artifact: Path, bufferdir: Path, driver_cache: str) -> None:
    if driver_cache not in {"bypass", "enabled"}:
        raise ReplayUsageError("driver_cache must be bypass or enabled")
    if not script.is_file():
        raise ReplayUsageError(f"script is not readable: {script}")
    if not os.access(script, os.R_OK):
        raise ReplayUsageError(f"script is not readable: {script}")
    if not artifact.is_file():
        raise ReplaySetupError(f"artifact is not readable: {artifact}", setup_error_report(str(artifact), artifact=str(artifact), bufferdir=str(bufferdir)))
    if not bufferdir.is_dir():
        raise ReplaySetupError(f"bufferdir is not a directory: {bufferdir}", setup_error_report(str(bufferdir), artifact=str(artifact), bufferdir=str(bufferdir)))
    try:
        conn = sqlite3.connect(f"file:{artifact.resolve()}?mode=ro", uri=True)
        conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
        conn.close()
    except sqlite3.Error as exc:
        report = setup_error_report(f"artifact is not a readable SQLite database: {artifact}", artifact=str(artifact), bufferdir=str(bufferdir))
        raise ReplaySetupError(report.outcome.message or "artifact setup failed", report) from exc


def _coerce_authorization(value: AuthorizationSpec | str | None) -> AuthorizationSpec:
    try:
        if value is None:
            return AuthorizationSpec.empty()
        if isinstance(value, AuthorizationSpec):
            return value
        return AuthorizationSpec.from_file(value)
    except AuthorizationError as exc:
        raise ReplaySetupError(str(exc), setup_error_report(str(exc))) from exc


def _artifact_info(artifact: Path, bufferdir: Path) -> tuple[ArtifactInfo, str, str]:
    db_digest = file_sha256(artifact)
    buffer_digest = bufferdir_manifest_sha256(bufferdir)
    return (
        ArtifactInfo(
            seamless_db=str(artifact.resolve()),
            seamless_db_checksum=db_digest,
            bufferdir=str(bufferdir.resolve()),
            bufferdir_checksum=buffer_digest,
        ),
        db_digest,
        buffer_digest,
    )


def _child_env(
    *,
    event_path: Path,
    authorization_path: Path,
    artifact: Path,
    bufferdir: Path,
    driver_cache: str,
    config: ReplayConfig,
    allow_remote: bool,
) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "SEAMLESS_REPLAY_MODE": "1",
            "SEAMLESS_REPLAY_ARTIFACT": str(artifact.resolve()),
            "SEAMLESS_REPLAY_BUFFERDIR": str(bufferdir.resolve()),
            "SEAMLESS_REPLAY_AUTH": str(authorization_path),
            "SEAMLESS_REPLAY_DRIVER_CACHE": driver_cache,
            "SEAMLESS_REPLAY_ALLOW_REMOTE": "1" if allow_remote else "0",
            "SEAMLESS_REPLAY_REPORT_EVENTS": str(event_path),
            "SEAMLESS_REPLAY_CONFIG_MODE": config.mode,
        }
    )
    if config.path is not None:
        env["SEAMLESS_REPLAY_CONFIG"] = config.path
    return env


def _finding_counts(findings: list[Finding]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.kind] = counts.get(finding.kind, 0) + 1
    return dict(sorted(counts.items()))
