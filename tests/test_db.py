from pathlib import Path

from vortnotes.db import connect


def test_connect_sets_row_factory(tmp_path: Path):
    db = tmp_path / "t.db"
    conn = connect(db)
    try:
        conn.execute("create table t(x int)")
        conn.execute("insert into t(x) values (1)")
        row = conn.execute("select x from t").fetchone()
        assert row["x"] == 1
    finally:
        conn.close()
