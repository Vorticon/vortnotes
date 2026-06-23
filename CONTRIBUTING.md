# Contributing

Use Python 3.11 or newer. Install `requirements.lock` with `--require-hashes` and
`requirements-dev.txt`, then run:

```text
ruff check .
black --check .
pytest
python scripts/check_release_clean.py
```

Do not commit databases, uploads, secrets, certificates, logs, backups, or
generated release archives. Schema changes must be added as an ordered entry in
`vortnotes/migrations.py` and covered by an upgrade test. User-visible changes
belong in `CHANGELOG.md`.
