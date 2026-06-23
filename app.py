"""WSGI entrypoint.

Kept as a tiny shim so existing deployments that run `gunicorn app:app` keep working.

The actual application code lives in `vortnotes/webapp.py`.
"""

from vortnotes import create_app

app = create_app()  # noqa: F401
