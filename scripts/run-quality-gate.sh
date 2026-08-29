#!/usr/bin/env bash
# One deterministic local/CI gate shared by dev-package and release-package.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

step() {
  printf '\n[%s] %s\n' "quality-gate" "$1"
}

step "public package version surfaces"
python3 scripts/manage-version.py check
python3 scripts/test-manage-version.py

step "package builder regression"
python3 scripts/test-build-package.py
python3 scripts/test-package-channel.py
python3 scripts/test-verify-package.py
python3 scripts/test-release-contract-bumps.py

step "skill structure and current contracts"
bash scripts/static-check.sh
python3 scripts/test-static-check.py
python3 scripts/skill-numbering.py check
bash scripts/test-skill-numbering.sh
bash scripts/check-current-skill-contracts.sh
python3 scripts/test-current-skill-contracts.py
python3 scripts/check-chinese-prose-contract.py

step "shared assets and writing detectors"
bash scripts/check-hook-regex-sync.sh
bash scripts/check-shared-files.sh
python3 scripts/test-shared-assets.py
node scripts/test-normalize-punctuation.js
node scripts/test-scan-runtime.js
bash scripts/test-ai-patterns.sh
bash scripts/test-degeneration.sh
bash scripts/test-language-gate.sh
bash scripts/test-style-hygiene.sh
python3 scripts/test-deslop-guard.py
bash scripts/test-dialogue-drift-gate.sh
python3 scripts/test-prose-metrics.py
bash scripts/test-outline-copy.sh
bash scripts/test-prose-backstop-hook.sh
bash scripts/test-prose-net-parity.sh
bash scripts/test-story-continuity.sh

step "data analysis workflow contracts"
python3 -m unittest discover -s skills/story-data-analyze/tests -p 'test_*.py' -v

step "tracking and deployment contracts"
python3 scripts/test-tracking-workflow-contracts.py
python3 scripts/test-tracking-commit.py
python3 scripts/test-outline-forecast.py
python3 scripts/test-chapter-candidate.py
python3 scripts/test-voice-profile.py
python3 scripts/test-cold-read-ledger.py
python3 scripts/test-copy-path-safety.py
bash scripts/check-story-setup-deployment.sh

step "runtime adapters"
python3 scripts/test-story-publish.py
bash scripts/check-claude-adapter.sh
bash scripts/check-codex-adapter.sh
bash scripts/check-opencode-adapter.sh
bash scripts/check-openclaw-skills.sh
bash scripts/check-zcode-adapter.sh
bash scripts/check-reasonix-adapter.sh
bash scripts/test-codex-hooks.sh
bash scripts/test-zcode-hooks.sh

step "portable invocation and encoding"
bash scripts/check-python-invocation.sh
bash scripts/check-hook-locale-safety.sh
bash scripts/test-hook-encoding-portable.sh
bash scripts/test-charcount-portable.sh
bash scripts/test-charcount-portable.sh --stub

step "collection script syntax"
while IFS= read -r -d '' file; do
  node --check "$file"
done < <(
  find skills -type f \( \
    -name '*-scraper.js' -o \
    -name 'cdp-utils.js' -o \
    -name 'setup-cdp-chrome.js' \
  \) -print0
)

step "dashboard dependencies and tests"
if [ "${OH_STORY_SKIP_NPM_CI:-0}" != "1" ]; then
  npm ci
fi
npm run test:dashboard
if [ "${OH_STORY_SKIP_E2E:-0}" != "1" ]; then
  npm run test:dashboard:e2e
else
  printf '[quality-gate] dashboard e2e explicitly skipped by OH_STORY_SKIP_E2E=1\n'
fi

printf '\n[quality-gate] PASS\n'
