#!/usr/bin/env sh
# Installs the zero-dependency git hooks (gitleaks via binary or Docker).
set -e
cd "$(git rev-parse --show-toplevel)"
git config core.hooksPath .githooks
chmod +x .githooks/* 2>/dev/null || true
echo "Hooks installed (core.hooksPath=.githooks)."
