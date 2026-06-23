from vortnotes.routes.db_manage import _safe_zip_member, _unique_db_import_name, _unique_upload_key


def test_safe_zip_member_rejects_path_traversal():
    assert _safe_zip_member("database/notes.db")
    assert _safe_zip_member("uploads/key/file.png")
    assert not _safe_zip_member("../notes.db")
    assert not _safe_zip_member("uploads/../notes.db")
    assert not _safe_zip_member("/absolute/notes.db")


def test_unique_db_import_name_sanitizes_and_avoids_collisions():
    name = _unique_db_import_name("../My Notes.db", ["My_Notes.db"], "20260614_120000")
    assert name == "My_Notes_import_20260614_120000.db"


def test_unique_upload_key_avoids_existing_folder(tmp_path):
    (tmp_path / "notes_key").mkdir()
    assert _unique_upload_key("notes_key", tmp_path) == "notes_key_1"
