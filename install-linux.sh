#!/bin/sh
# Linux/SteamOS source launcher. Quote paths and keep caller-relative arguments.
set -eu
launcher_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
if command -v python3 >/dev/null 2>&1; then
    patch_python=python3
elif command -v python >/dev/null 2>&1; then
    patch_python=python
else
    printf '%s\n' 'Python 3.10 이상이 필요합니다. python3 --version으로 확인하세요.' >&2
    exit 1
fi
if ! "$patch_python" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
    printf '%s\n' 'Python 3.10 이상이 필요합니다.' >&2
    exit 1
fi
exec "$patch_python" -B -X utf8 "$launcher_dir/patch.py" "$@"
