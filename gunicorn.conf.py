"""Gunicorn settings, including persisted direct-HTTPS configuration."""

import os

from vortnotes.deployment import tls_context_from_config
from vortnotes.settings import CONFIG_PATH

bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"
workers = int(os.getenv("WEB_CONCURRENCY", "1"))
threads = int(os.getenv("THREADS", "2"))
timeout = int(os.getenv("TIMEOUT", "120"))

tls_context = tls_context_from_config(CONFIG_PATH)
if tls_context:
    certfile, keyfile = tls_context
