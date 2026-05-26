"""Typed errors translated by the CLI."""

from __future__ import annotations


class WhyNotError(Exception):
    """Base class for expected diagnostic errors."""


class UsageError(WhyNotError):
    """Bad user input or unsupported reference form."""


class EndpointError(WhyNotError):
    """Endpoint cannot be queried read-only."""


class DeepBufferUnavailable(WhyNotError):
    """Deep output was produced with unavailable buffers."""

    def __init__(self, message: str, *, output: str | None = None, best_effort: bool = False):
        super().__init__(message)
        self.output = output
        self.best_effort = best_effort
