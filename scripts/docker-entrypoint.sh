#!/bin/sh
set -eu

exec gunicorn -c /app/gunicorn.conf.py app:app
