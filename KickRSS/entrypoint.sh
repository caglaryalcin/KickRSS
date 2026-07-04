#!/bin/sh
set -e

exec gunicorn -b "${HOST:-0.0.0.0}:${PORT:-8000}" -k gthread --threads "${THREADS:-3}" kickrss:app
