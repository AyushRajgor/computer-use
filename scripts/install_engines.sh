#!/bin/sh
# Installs the optional browser-use engine into the current Python environment.
#
# browser-use pins google-genai==1.65.0 / google-auth==2.48.0, but this repo
# needs google-genai>=2.34 (Interactions API). uv's --override keeps the newer
# versions; browser-use works with them.
set -eu

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-$(command -v python3 || command -v python)}"
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || {
  echo "browser-use needs Python >= 3.11" >&2
  exit 1
}

if ! command -v uv >/dev/null 2>&1; then
  "$PYTHON" -m pip install --quiet uv
fi

OVERRIDES="$(mktemp)"
trap 'rm -f "$OVERRIDES"' EXIT
cat > "$OVERRIDES" <<'EOF'
google-genai>=2.34.0,<3
google-auth>=2.56
EOF

TARGET="--python $PYTHON"
if [ -z "${VIRTUAL_ENV:-}" ] && ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.prefix != sys.base_prefix else 1)'; then
  TARGET="--system"
fi

# shellcheck disable=SC2086
uv pip install $TARGET -r requirements.txt -r requirements-engines.txt --override "$OVERRIDES"
echo "browser-use engine installed. Try: python main.py --engine browser-use --query \"...\""
