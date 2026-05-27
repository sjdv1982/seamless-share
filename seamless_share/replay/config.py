"""Replay config mode description."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .models import ReplayConfigInfo


@dataclass
class ReplayConfig:
    mode: str
    path: str | None = None

    @classmethod
    def synthesized(cls) -> "ReplayConfig":
        return cls("synthesized")

    @classmethod
    def from_file(cls, path: str | Path) -> "ReplayConfig":
        return cls("file", str(path))

    @classmethod
    def inherit(cls) -> "ReplayConfig":
        return cls("inherit")

    def to_info(self, *, artifact: str, bufferdir: str, driver_cache: str) -> ReplayConfigInfo:
        if self.mode == "inherit":
            return ReplayConfigInfo(
                synthesized=False,
                endpoints_resolved={},
                driver_cache=driver_cache,
                inherited=True,
                warnings=["config_inherited"],
            )
        endpoints = {"database": artifact, "bufferdir": bufferdir}
        return ReplayConfigInfo(
            synthesized=self.mode == "synthesized",
            endpoints_resolved=endpoints,
            driver_cache=driver_cache,
            config_path=self.path,
            inherited=False,
        )
