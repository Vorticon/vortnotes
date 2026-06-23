"""Container health check supporting HTTP and direct TLS."""

import json
import os
import ssl
import urllib.request

from vortnotes.deployment import tls_context_from_config
from vortnotes.settings import CONFIG_PATH

scheme = "https" if tls_context_from_config(CONFIG_PATH) else "http"
context = ssl._create_unverified_context() if scheme == "https" else None
url = f"{scheme}://127.0.0.1:{os.getenv('PORT', '8000')}/healthz"
with urllib.request.urlopen(url, timeout=3, context=context) as response:
    assert json.load(response)["ok"] is True
