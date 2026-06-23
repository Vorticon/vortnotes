"""Run VortNotes with `python -m vortnotes`.

This is a convenience entrypoint for local development.
"""

from __future__ import annotations

import os

from . import create_app
from .deployment import tls_context_from_config
from .settings import CONFIG_PATH


def main() -> None:
    app = create_app()
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
    app.run(host=host, port=port, debug=debug, ssl_context=tls_context_from_config(CONFIG_PATH))


if __name__ == "__main__":
    main()
