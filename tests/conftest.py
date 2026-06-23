import os
from pathlib import Path

# Isolate all app data before test modules import the singleton Flask app.
os.environ.setdefault("NOTES_DATA_DIR", str(Path(__file__).resolve().parents[1] / ".pytest-tmp" / "app-data"))
