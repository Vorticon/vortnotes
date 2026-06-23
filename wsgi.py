"""Alternative WSGI entrypoint.

Some process managers prefer `wsgi:app`.
"""

from vortnotes import create_app

app = create_app()  # noqa: F401
