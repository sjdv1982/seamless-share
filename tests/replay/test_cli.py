import json
import sqlite3
import subprocess
import sys
from pathlib import Path


def _artifact(tmp_path: Path):
    db = tmp_path / "seamless.db"
    bufferdir = tmp_path / "bufferdir"
    bufferdir.mkdir()
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE transformation (checksum CHAR(64) PRIMARY KEY, result CHAR(64))")
    conn.commit()
    conn.close()
    return db, bufferdir


def test_replay_cli_noop_json_report(tmp_path):
    db, bufferdir = _artifact(tmp_path)
    script = tmp_path / "noop.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    report = tmp_path / "report.json"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "seamless_share.cli",
            "replay",
            "--artifact",
            str(db),
            "--bufferdir",
            str(bufferdir),
            "--report",
            str(report),
            str(script),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["tool"] == "replay"
    assert payload["outcome"]["phase"] == "completed"
    assert payload["post_run_assertions"]["seamless_db_unchanged"] is True


def test_replay_cli_malformed_auth_is_setup_error(tmp_path):
    db, bufferdir = _artifact(tmp_path)
    script = tmp_path / "noop.py"
    script.write_text("", encoding="utf-8")
    auth = tmp_path / "auth.json"
    auth.write_text("{", encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "seamless_share.cli",
            "replay",
            "--artifact",
            str(db),
            "--bufferdir",
            str(bufferdir),
            "--authorization",
            str(auth),
            str(script),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 3
    payload = json.loads(proc.stderr)
    assert payload["outcome"]["phase"] == "setup_error"
