#!/usr/bin/env bash
set -euo pipefail

# remote-ops one-line installer
# Detects installed TUI harnesses and installs the skill to all of them.
# Usage: curl -sSL https://raw.githubusercontent.com/timeuser4/remote-ops/v0.1/install.sh | bash

REPO_URL="https://github.com/timeuser4/remote-ops.git"
TMP_DIR=$(mktemp -d)
HARNESS_DIRS=()

# Detect harnesses
if [ -d "$HOME/.claude" ]; then
    HARNESS_DIRS+=("$HOME/.claude/skills/remote-ops")
fi
if [ -d "$HOME/.codex" ]; then
    HARNESS_DIRS+=("$HOME/.codex/skills/remote-ops")
fi
if [ -d "$HOME/.opencode" ]; then
    HARNESS_DIRS+=("$HOME/.opencode/skills/remote-ops")
fi

if [ ${#HARNESS_DIRS[@]} -eq 0 ]; then
    echo "No supported harness detected (~/.claude, ~/.codex, ~/.opencode)."
    echo "Install a harness first, or use manual installation."
    exit 1
fi

echo "==> Cloning remote-ops..."
git clone --depth 1 "$REPO_URL" "$TMP_DIR"

for dir in "${HARNESS_DIRS[@]}"; do
    echo "==> Installing to $dir"
    mkdir -p "$dir"
    cp "$TMP_DIR/SKILL.md" "$dir/"
    cp -r "$TMP_DIR/scripts/" "$TMP_DIR/references/" "$TMP_DIR/agents/" "$dir/"
done

echo "==> Running setup..."
python3 "$TMP_DIR/scripts/setup.py"

rm -rf "$TMP_DIR"
echo "==> Done. remote-ops installed to ${#HARNESS_DIRS[@]} harness(es)."
