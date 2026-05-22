#!/bin/bash
# lint.sh
set -e

echo "Running Ruff Format (check-only)..."
# --check makes ruff exit non-zero on unformatted code without writing.
# Without --check, lint.sh silently mutates files and `set -e` never trips,
# so formatting violations sail through. Use `ruff format .` (no --check) to
# fix locally before re-running this script.
uv run ruff format --check .

echo "Running Ruff..."
uv run ruff check .

echo "Running Pyright..."
uv run pyright .