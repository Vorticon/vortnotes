import io
from pathlib import Path

from werkzeug.datastructures import FileStorage

from vortnotes.storage import save_with_size_limit, unique_store_name


def test_unique_store_name_avoids_overwrite(tmp_path: Path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    name = unique_store_name(tmp_path, "a.txt")
    assert name != "a.txt"
    assert name.startswith("a_")
    assert name.endswith(".txt")


def test_save_with_size_limit_allows_small_files(tmp_path: Path):
    data = b"hello" * 10
    fs = FileStorage(stream=io.BytesIO(data), filename="x.bin", content_type="application/octet-stream")
    dest = tmp_path / "x.bin"
    ok, err = save_with_size_limit(fs, dest, max_bytes=len(data) + 1)
    assert ok is True
    assert err == ""
    assert dest.exists()
    assert dest.read_bytes() == data


def test_save_with_size_limit_rejects_large_files_and_cleans_up(tmp_path: Path):
    data = b"a" * 1024
    fs = FileStorage(stream=io.BytesIO(data), filename="big.bin", content_type="application/octet-stream")
    dest = tmp_path / "big.bin"
    ok, err = save_with_size_limit(fs, dest, max_bytes=100)
    assert ok is False
    assert err
    assert not dest.exists()
