#!/usr/bin/env sh
set -eu

script_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_root=$(dirname -- "$script_directory")
cd "$project_root"

if command -v uv >/dev/null 2>&1; then
    exec uv run python -m infrastructure "$@"
fi

exec python3 -m infrastructure "$@"
