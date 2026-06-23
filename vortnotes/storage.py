"""Storage helpers.

This module contains filesystem-level primitives for saving uploads.

Why this exists:
- :mod:`vortnotes.webapp` historically contained *everything* (routes + helpers).
- Moving storage helpers into a dedicated module makes future refactors and
  testing much easier without changing app behavior.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Tuple

from werkzeug.utils import secure_filename


def unique_store_name(target_dir: Path, original_filename: str) -> str:
    """Return a stored filename that prefers the original name but avoids overwrites."""
    safe = secure_filename(original_filename) or "file"
    candidate = safe
    if not (target_dir / candidate).exists():
        return candidate
    stem = Path(safe).stem
    ext = Path(safe).suffix
    # Avoid clobbering: append short uuid
    return f"{stem}_{uuid.uuid4().hex[:8]}{ext}"


def save_with_size_limit(file_storage, dest_path: Path, max_bytes: int) -> Tuple[bool, str]:
    """Save an uploaded file enforcing a max size.

    Returns ``(ok, error_message)``. If not ok, the file is not kept.

    Notes:
    - This is intentionally defensive because different WSGI servers expose
      different per-part content length behaviors.
    - We save first, then validate size on disk as the authoritative truth.
    """

    try:
        # If Content-Length is known for this part, pre-check.
        part_len = getattr(file_storage, "content_length", None)
        if part_len is not None:
            try:
                if int(part_len) > int(max_bytes):
                    return (False, "File is too large")
            except Exception:
                pass

        file_storage.save(dest_path)

        try:
            size = dest_path.stat().st_size
        except Exception:
            size = None

        if size is not None and size > int(max_bytes):
            try:
                dest_path.unlink(missing_ok=True)
            except Exception:
                pass
            return (False, "File is too large")
        return (True, "")
    except Exception:
        try:
            dest_path.unlink(missing_ok=True)
        except Exception:
            pass
        return (False, "Upload failed")
