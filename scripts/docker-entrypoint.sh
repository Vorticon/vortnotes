#!/bin/sh
set -eu

exec gunicorn --no-control-socket -c /app/gunicorn.conf.py app:app
