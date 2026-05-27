import json
import sqlite3
from pathlib import Path

from jsonschema import Draft202012Validator

from seamless_share.replay import replay
from seamless_share.replay.models import to_plain


def test_replay_report_validates_schema(tmp_path):
    schema_path = Path(__file__).parents[2] / "seamless_share" / "replay" / "schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["version"] == "0.1.0"
    db = tmp_path / "seamless.db"
    bufferdir = tmp_path / "bufferdir"
    bufferdir.mkdir()
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE transformation (checksum CHAR(64) PRIMARY KEY, result CHAR(64))")
    conn.commit()
    conn.close()
    script = tmp_path / "noop.py"
    script.write_text("", encoding="utf-8")
    report = replay(script=str(script), artifact=str(db), bufferdir=str(bufferdir))
    Draft202012Validator(schema).validate(to_plain(report))
