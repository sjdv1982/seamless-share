"""Replay authorization parsing and decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import sqlite3
from typing import Any


class AuthorizationError(ValueError):
    """Raised when an authorization file cannot be parsed."""


@dataclass
class AuthorizationDecision:
    allowed: bool
    source: str | None
    reason: str
    considered: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AuthorizationSpec:
    fingertips: list[str] = field(default_factory=list)
    driver_cache: str | None = None
    source_path: str | None = None

    @classmethod
    def empty(cls) -> "AuthorizationSpec":
        return cls()

    @classmethod
    def from_file(cls, path: str | Path) -> "AuthorizationSpec":
        source = Path(path)
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except OSError as exc:
            raise AuthorizationError(f"cannot read authorization file: {source}") from exc
        except json.JSONDecodeError as exc:
            raise AuthorizationError(f"malformed authorization JSON: {source}") from exc
        if not isinstance(payload, dict):
            raise AuthorizationError("authorization JSON must be an object")
        fingertips = payload.get("fingertips", [])
        if not isinstance(fingertips, list) or not all(isinstance(item, str) for item in fingertips):
            raise AuthorizationError("authorization.fingertips must be a list of checksum strings")
        driver_cache = payload.get("driver_cache")
        if driver_cache is not None and driver_cache not in {"bypass", "enabled"}:
            raise AuthorizationError("authorization.driver_cache must be bypass or enabled")
        return cls(fingertips=sorted(set(fingertips)), driver_cache=driver_cache, source_path=str(source))

    def decide_materialization(self, checksum: str, bufferdir: str | Path) -> AuthorizationDecision:
        path = Path(bufferdir) / checksum
        considered = [{"source": "bufferdir", "path": str(path), "present": path.is_file()}]
        if path.is_file():
            return AuthorizationDecision(True, "bufferdir", "present_in_bufferdir", considered)
        fingertip_allowed = checksum in set(self.fingertips)
        considered.append({"source": "fingertip", "checksum": checksum, "authorized": fingertip_allowed})
        if fingertip_allowed:
            return AuthorizationDecision(True, "fingertip", "explicit_fingertip", considered)
        return AuthorizationDecision(False, None, "not_authorized", considered)

    def incoherence_findings(self, artifact: str | Path, driver_cache: str) -> list[dict[str, Any]]:
        findings = []
        if self.driver_cache is not None and self.driver_cache != driver_cache:
            findings.append(
                {
                    "authorization": self.source_path or "inline",
                    "reason": "conflicting_driver_cache",
                    "authorization_driver_cache": self.driver_cache,
                    "runtime_driver_cache": driver_cache,
                }
            )
        if not self.fingertips:
            return findings
        try:
            conn = sqlite3.connect(f"file:{Path(artifact).resolve()}?mode=ro", uri=True)
            rows = {
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT result FROM transformation WHERE result IS NOT NULL"
                )
            }
            conn.close()
        except sqlite3.Error:
            return findings
        for checksum in self.fingertips:
            if checksum not in rows:
                findings.append(
                    {
                        "authorization": checksum,
                        "reason": "fingertip_producer_absent",
                    }
                )
        return findings
