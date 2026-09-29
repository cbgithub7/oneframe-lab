#!/bin/bash
# Prepare a Claude Code on the web container for this repo: the Node LTS in .node-version, the uv
# the engine requires, the engine's locked Python environment, and the app's npm packages, so
# `npm run check`, `npm run engine:check` and `npm run versions` work from the first prompt.
#
# Versions come from the repo's own files, never from this script, so bumping them (see
# docs/versions.md) needs no edit here. Downloads are checked against their published SHA-256.
# Safe to run again: anything already in place is left alone.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
TOOLS="${HOME}/.local/share/oneframe-tools"
mkdir -p "$TOOLS"

NODE_VERSION="$(tr -d '[:space:]' < "$ROOT/.node-version")"
UV_VERSION="$(sed -nE 's/^required-version *= *">=([0-9.]+)".*/\1/p' "$ROOT/engine/pyproject.toml")"
if [ -z "$NODE_VERSION" ] || [ -z "$UV_VERSION" ]; then
  echo "session-start: could not read the Node or uv version from the repo" >&2
  exit 1
fi

case "$(uname -m)" in
  x86_64) NODE_ARCH=x64; UV_TRIPLE=x86_64-unknown-linux-gnu ;;
  aarch64 | arm64) NODE_ARCH=arm64; UV_TRIPLE=aarch64-unknown-linux-gnu ;;
  *) echo "session-start: unsupported architecture $(uname -m)" >&2; exit 1 ;;
esac

# Node (the LTS in .node-version), from nodejs.org.
NODE_DIR="$TOOLS/node-v$NODE_VERSION"
if [ ! -x "$NODE_DIR/bin/node" ]; then
  NODE_TAR="node-v$NODE_VERSION-linux-$NODE_ARCH.tar.xz"
  WORK="$(mktemp -d)"
  curl -sSfL -o "$WORK/$NODE_TAR" "https://nodejs.org/dist/v$NODE_VERSION/$NODE_TAR"
  curl -sSfL "https://nodejs.org/dist/v$NODE_VERSION/SHASUMS256.txt" | grep " $NODE_TAR\$" > "$WORK/sum"
  (cd "$WORK" && sha256sum -c sum >/dev/null)
  mkdir -p "$NODE_DIR"
  tar -xJf "$WORK/$NODE_TAR" -C "$NODE_DIR" --strip-components=1
  rm -rf "$WORK"
fi

# uv (the version engine/pyproject.toml requires), from its GitHub release.
UV_DIR="$TOOLS/uv-$UV_VERSION"
if [ ! -x "$UV_DIR/uv" ]; then
  UV_TAR="uv-$UV_TRIPLE.tar.gz"
  BASE="https://github.com/astral-sh/uv/releases/download/$UV_VERSION"
  WORK="$(mktemp -d)"
  curl -sSfL -o "$WORK/$UV_TAR" "$BASE/$UV_TAR"
  curl -sSfL -o "$WORK/$UV_TAR.sha256" "$BASE/$UV_TAR.sha256"
  (cd "$WORK" && sha256sum -c "$UV_TAR.sha256" >/dev/null)
  mkdir -p "$UV_DIR"
  tar -xzf "$WORK/$UV_TAR" -C "$UV_DIR" --strip-components=1
  rm -rf "$WORK"
fi

export PATH="$NODE_DIR/bin:$UV_DIR:$PATH"
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "export PATH=\"$NODE_DIR/bin:$UV_DIR:\$PATH\"" >> "$CLAUDE_ENV_FILE"
fi

cd "$ROOT"
# The engine: exactly what engine/uv.lock says. uv fetches the Python itself.
uv sync --project engine --frozen --quiet
# The app's packages. `install` rather than `ci` so the container cache is reused between
# sessions; with exact pins and the lock file it installs the same versions.
npm install --no-audit --no-fund --loglevel=error

echo "session-start: node $(node --version), $(uv --version), engine $(engine/.venv/bin/python --version)" >&2
