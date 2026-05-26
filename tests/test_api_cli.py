import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from seamless_share.why_not import transformation_diff, why_not
from seamless_share.why_not.models import Reference, to_json
from seamless_share.why_not.references import transformation_checksum


def write_json(path: Path, value: dict):
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def write_buffer(root: Path, checksum: str, payload: bytes):
    path = root / checksum
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def build_endpoint(tmp_path: Path, rows: list[tuple[str, str, dict]], result_buffers=()):
    db_path = tmp_path / "seamless.db"
    bufferdir = tmp_path / "bufferdir"
    bufferdir.mkdir()
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE transformation (checksum CHAR(64) PRIMARY KEY, result CHAR(64))")
    conn.execute("CREATE TABLE irreproducible_transformation (result CHAR(64), checksum CHAR(64), metadata TEXT)")
    for tf_checksum, result_checksum, tf_dict in rows:
        conn.execute(
            "INSERT INTO transformation (checksum, result) VALUES (?, ?)",
            (tf_checksum, result_checksum),
        )
        write_buffer(bufferdir, tf_checksum, json.dumps(tf_dict, sort_keys=True).encode())
    conn.commit()
    conn.close()
    for checksum, payload in result_buffers:
        write_buffer(bufferdir, checksum, payload)
    return db_path


def test_transformation_diff_json_is_deterministic(tmp_path):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    write_json(a, {"code": ["plain", None, "a" * 64]})
    write_json(b, {"code": ["plain", None, "b" * 64]})
    first = to_json(transformation_diff(str(a), str(b)))
    second = to_json(transformation_diff(str(a), str(b)))
    assert first == second
    payload = json.loads(first)
    assert payload["identity_relevant"] is True
    assert payload["entries"][0]["key"] == "code"


def test_why_not_explicit_candidate_without_endpoint(tmp_path):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    write_json(a, {"code": "a"})
    write_json(b, {"code": "b"})
    result = why_not(str(a), candidate=str(b))
    assert result.lookup_state.state == "NOT_PRESENT"
    assert result.candidate.tf_checksum == transformation_checksum({"code": "b"})
    assert result.diff["identity_relevant"] is True


def test_why_not_local_endpoint_lookup_hit(tmp_path):
    tf_dict = {"code": "a"}
    tf_checksum = transformation_checksum(tf_dict)
    result_checksum = "d" * 64
    db_path = build_endpoint(tmp_path, [(tf_checksum, result_checksum, tf_dict)], [(result_checksum, b"ok")])
    ref = Reference.from_tf_checksum(tf_checksum)
    ref.transformation_dict = tf_dict
    result = why_not(ref, endpoints=[str(db_path)])
    assert result.lookup_state.state == "PRESENT_AS_HIT"
    assert result.diff is None


def test_why_not_selects_candidate_from_transformation_dict_buffer(tmp_path):
    input_path = tmp_path / "input.json"
    input_dict = {"code": "new", "x": 1}
    candidate_dict = {"code": "old", "x": 1}
    write_json(input_path, input_dict)
    candidate_checksum = transformation_checksum(candidate_dict)
    db_path = build_endpoint(tmp_path, [(candidate_checksum, "e" * 64, candidate_dict)])
    result = why_not(str(input_path), endpoints=[str(db_path)])
    assert result.lookup_state.state == "NOT_PRESENT"
    assert result.candidate.tf_checksum == candidate_checksum
    assert result.diff["identity_relevant"] is True


def test_cli_usage_error_for_checksum_without_endpoint():
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "seamless_share.cli",
            "transformation-diff",
            "a" * 64,
            "b" * 64,
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "checksum references require" in proc.stderr
