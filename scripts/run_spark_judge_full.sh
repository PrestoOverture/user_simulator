#!/bin/bash
set -eu
cd "$(dirname "$0")/.."
set -a
source .env
set +a
exec .venv/bin/python -u scripts/validate_spark_judge.py --supervise-full --concurrency "${SPARK_JUDGE_CONCURRENCY:-8}"
