"""App logging setup.

Goals:
- Always log to stdout (container-friendly)
- Optionally log to a rotating file under DATA_DIR/logs
- Include a lightweight request id when inside a request context
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

from flask import has_request_context, request

from .settings import LOG_DIR


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        # Attach request_id if we are inside a request context.
        if has_request_context():
            rid = request.headers.get("X-Request-ID") or request.environ.get("REQUEST_ID")
            record.request_id = rid or "-"
        else:
            record.request_id = "-"
        return True


def configure_logging(app_name: str = "vortnotes") -> None:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    root = logging.getLogger()
    if root.handlers:
        # Avoid double-configuring if imported multiple times.
        return

    root.setLevel(level)

    fmt = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    filt = RequestIdFilter()

    # Stdout handler
    sh = logging.StreamHandler()
    sh.setLevel(level)
    sh.setFormatter(fmt)
    sh.addFilter(filt)
    root.addHandler(sh)

    # Rotating file handler (best-effort)
    try:
        log_path = LOG_DIR / f"{app_name}.log"
        fh = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3)
        fh.setLevel(level)
        fh.setFormatter(fmt)
        fh.addFilter(filt)
        root.addHandler(fh)
    except Exception:
        # If filesystem is read-only or permissions forbid it, stdout-only is fine.
        pass
