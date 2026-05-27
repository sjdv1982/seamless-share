"""Replay report helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .models import Finding, ReplayCounts


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bufferdir_manifest_sha256(path: str | Path) -> str:
    root = Path(path)
    digest = hashlib.sha256()
    for child in sorted(item for item in root.rglob("*") if item.is_file()):
        stat = child.stat()
        rel = child.relative_to(root).as_posix()
        digest.update(rel.encode())
        digest.update(b"\0")
        digest.update(str(stat.st_mode & 0o777).encode())
        digest.update(b"\0")
        digest.update(str(stat.st_size).encode())
        digest.update(b"\0")
        digest.update(file_sha256(child).encode())
        digest.update(b"\0")
    return digest.hexdigest()


def parse_event_file(path: str | Path) -> tuple[ReplayCounts, list[Finding]]:
    counts = ReplayCounts()
    findings: list[Finding] = []
    event_path = Path(path)
    if not event_path.exists():
        return counts, findings
    for line in event_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        _apply_event(event, counts, findings)
    counts.findings_by_kind = dict(sorted(counts.findings_by_kind.items()))
    return counts, findings


def _add_finding(findings: list[Finding], counts: ReplayCounts, kind: str, fields: dict[str, Any], context=None):
    findings.append(Finding.build(kind, fields, context=context))
    counts.findings_by_kind[kind] = counts.findings_by_kind.get(kind, 0) + 1


def _apply_event(event: dict[str, Any], counts: ReplayCounts, findings: list[Finding]) -> None:
    kind = event.get("event") or event.get("kind")
    if kind in {"transformation_submitted", "transformation_started"}:
        counts.transformations_submitted += 1
        if event.get("is_driver"):
            counts.drivers_executed += 1
    elif kind == "driver_short_circuited":
        counts.drivers_short_circuited += 1
    elif kind == "cache_hit":
        counts.cache_hits += 1
    elif kind == "materialized":
        source = event.get("source")
        if source == "bufferdir":
            counts.buffers_materialized_from_bufferdir += 1
        elif source == "fingertip":
            counts.buffers_materialized_via_authorized_fingertip += 1
    elif kind in {
        "unexpected_miss",
        "unauthorized_materialization",
        "unauthorized_fingertip",
        "authorized_materialization_unsatisfied_dependency",
        "remote_delegation_observed",
        "unexpected_heavy_compute",
        "irreproducible_only_hit",
        "authorization_incoherent",
    }:
        fields = dict(event.get("fields") or {})
        for key, value in event.items():
            if key not in {"event", "kind", "fields", "context"}:
                fields.setdefault(key, value)
        _add_finding(findings, counts, kind, fields, event.get("context") or {})
