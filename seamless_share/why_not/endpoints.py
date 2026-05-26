"""Read-only endpoint adapters."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .errors import EndpointError, UsageError
from .models import EndpointKind, EndpointSpec


@dataclass
class TransformationRecord:
    tf_checksum: str
    result_checksum: str | None
    transformation_dict: dict[str, Any] | None = None
    endpoint: str | None = None


class DatabaseEndpoint:
    spec: EndpointSpec

    def get_transformation_result(self, tf_checksum: str) -> str | None:
        raise NotImplementedError

    def get_transformation_dict(self, tf_checksum: str) -> dict[str, Any] | None:
        raise NotImplementedError

    def iter_transformation_dicts(self) -> Iterable[TransformationRecord]:
        raise NotImplementedError

    def get_irreproducible_records(self, tf_checksum: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def buffer_available(self, checksum: str) -> bool:
        raise NotImplementedError

    def get_buffer(self, checksum: str) -> bytes | None:
        raise NotImplementedError


class LocalSQLiteEndpoint(DatabaseEndpoint):
    def __init__(self, spec: EndpointSpec):
        self.spec = spec
        if spec.path is None:
            raise UsageError(f"local endpoint has no path: {spec.raw}")
        self.path = Path(spec.path)
        if not self.path.exists():
            raise EndpointError(f"endpoint file does not exist: {self.path}")
        uri = f"file:{self.path.resolve()}?mode=ro"
        try:
            self.connection = sqlite3.connect(uri, uri=True)
            self.connection.row_factory = sqlite3.Row
        except sqlite3.Error as exc:
            raise EndpointError(f"cannot open endpoint read-only: {self.path}") from exc
        self.bufferdirs = [
            self.path.parent / "bufferdir",
            self.path.parent / "buffers",
            self.path.parent / "hashserver",
        ]

    def _execute(self, query: str, parameters: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        try:
            return list(self.connection.execute(query, parameters))
        except sqlite3.Error as exc:
            raise EndpointError(f"malformed or unreadable database {self.path}: {exc}") from exc

    def _table_exists(self, name: str) -> bool:
        rows = self._execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
        return bool(rows)

    def get_transformation_result(self, tf_checksum: str) -> str | None:
        if not self._table_exists("transformation"):
            return None
        rows = self._execute("SELECT result FROM transformation WHERE checksum=?", (tf_checksum,))
        return str(rows[0]["result"]) if rows else None

    def get_irreproducible_records(self, tf_checksum: str) -> list[dict[str, Any]]:
        if not self._table_exists("irreproducible_transformation"):
            return []
        rows = self._execute(
            "SELECT result, metadata FROM irreproducible_transformation WHERE checksum=?",
            (tf_checksum,),
        )
        result = []
        for row in rows:
            metadata = row["metadata"]
            try:
                metadata = json.loads(metadata) if isinstance(metadata, str) else metadata
            except Exception:
                metadata = {"raw": metadata}
            result.append({"result": row["result"], "metadata": metadata})
        return result

    def iter_transformation_dicts(self) -> Iterable[TransformationRecord]:
        if not self._table_exists("transformation"):
            return []
        rows = self._execute("SELECT checksum, result FROM transformation ORDER BY checksum")
        records = []
        for row in rows:
            tf_checksum = str(row["checksum"])
            records.append(
                TransformationRecord(
                    tf_checksum=tf_checksum,
                    result_checksum=str(row["result"]),
                    transformation_dict=self.get_transformation_dict(tf_checksum),
                    endpoint=self.spec.raw,
                )
            )
        return records

    def _buffer_paths(self, checksum: str) -> list[Path]:
        rels = [
            Path(checksum),
            Path(checksum[:2]) / checksum[2:],
            Path(checksum[:2]) / checksum[2:4] / checksum[4:],
        ]
        paths = []
        for root in self.bufferdirs:
            for rel in rels:
                paths.append(root / rel)
        return paths

    def buffer_available(self, checksum: str) -> bool:
        return any(path.is_file() for path in self._buffer_paths(checksum))

    def get_buffer(self, checksum: str) -> bytes | None:
        for path in self._buffer_paths(checksum):
            if path.is_file():
                return path.read_bytes()
        return None

    def get_transformation_dict(self, tf_checksum: str) -> dict[str, Any] | None:
        data = self.get_buffer(tf_checksum)
        if data is None:
            return None
        try:
            value = json.loads(data.decode("utf-8"))
        except Exception as exc:
            raise EndpointError(
                f"transformation dict buffer {tf_checksum} is not JSON at endpoint {self.spec.raw}"
            ) from exc
        if not isinstance(value, dict):
            raise EndpointError(
                f"transformation dict buffer {tf_checksum} is not an object at endpoint {self.spec.raw}"
            )
        return value


def resolve_endpoint_specs(
    endpoints: list[EndpointSpec | str] | None,
    *,
    config: str | None = None,
) -> list[EndpointSpec]:
    if config is not None:
        raise UsageError("--config named endpoint resolution is not implemented in v1")
    return [item if isinstance(item, EndpointSpec) else EndpointSpec.from_str(item) for item in endpoints or []]


def open_endpoints(
    endpoints: list[EndpointSpec | str] | None,
    *,
    config: str | None = None,
) -> list[DatabaseEndpoint]:
    opened: list[DatabaseEndpoint] = []
    for spec in resolve_endpoint_specs(endpoints, config=config):
        if spec.endpoint_kind == EndpointKind.LOCAL_SQLITE.value:
            opened.append(LocalSQLiteEndpoint(spec))
        elif spec.endpoint_kind == EndpointKind.REMOTE_URL.value:
            raise UsageError("remote endpoint transformation-dict reads are not implemented in v1")
        else:
            raise UsageError(f"unknown endpoint spec: {spec.raw}")
    return opened
