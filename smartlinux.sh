#!/usr/bin/env bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

# Check if venv exists
if [ -d "$DIR/.venv" ]; then
    PYTHON_EXEC="$DIR/.venv/bin/python3"
else
    PYTHON_EXEC="python3"
fi

exec "$PYTHON_EXEC" "$DIR/smartlinux/main.py" "$@"
