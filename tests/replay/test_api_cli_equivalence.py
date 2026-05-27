import json
import sqlite3
import subprocess
import sys

from seamless_share.replay import replay
from seamless_share.replay.models import to_plain


def test_api_cli_equivalence_modulo_timing(tmp_path):
    db = tmp_path / "seamless.db"
    bufferdir = tmp_path / "bufferdir"
    bufferdir.mkdir()
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE transformation (checksum CHAR(64) PRIMARY KEY, result CHAR(64))")
    conn.commit()
    conn.close()
    script = tmp_path / "noop.py"
    script.write_text("", encoding="utf-8")
    api_payload = to_plain(replay(script=str(script), artifact=str(db), bufferdir=str(bufferdir)))
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
            "--report-format",
            "json",
            str(script),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0
    cli_payload = json.loads(proc.stdout)
    api_payload["outcome"]["wall_ms"] = 0
    cli_payload["outcome"]["wall_ms"] = 0
    assert api_payload == cli_payload
