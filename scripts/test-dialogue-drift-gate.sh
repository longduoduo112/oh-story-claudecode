#!/bin/bash
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
SCRIPT="$REPO_ROOT/skills/story-deslop/scripts/dialogue_drift_gate.js"
TMP_DIR="$(mktemp -d)"
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT
HISTORY="$TMP_DIR/正文"
mkdir -p "$HISTORY"

cat > "$HISTORY/第001章.md" <<'PROSE'
“你来了。”
他放下茶杯。
“是。”
她把门关上。
PROSE

cat > "$HISTORY/第002章.md" <<'PROSE'
“走吧。”张三说。
他拎起箱子。
“等等。”李四问。
她回头看了一眼门。
“没时间了。”张三说。
风吹掉了桌上的纸。
“那就走。”李四问。
两人一前一后出了门。
“灯呢？”张三说。
她又折回去。
“不用管。”李四问。
PROSE

set +e
node "$SCRIPT" --current "$HISTORY/第002章.md" --history-dir "$HISTORY" --json > "$TMP_DIR/advisory.json"
advisory_status=$?
set -e
[ "$advisory_status" -eq 0 ] || { echo "FAIL: density warnings alone must not reject" >&2; exit 1; }
node - "$TMP_DIR/advisory.json" <<'NODE'
const fs = require('fs');
const report = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
if (report.status !== 'passed' || report.findings.length !== 0 || report.advisories.length === 0) throw new Error(JSON.stringify(report));
if (report.next_action !== 'continue_with_semantic_dialogue_review') throw new Error(report.next_action);
NODE

cat > "$HISTORY/第003章.md" <<'PROSE'
“你来了。”张三说。
“我来了。”李四说。
“东西呢？”张三问。
“在这里。”李四回答。
“打开。”张三说。
PROSE
set +e
node "$SCRIPT" --current "$HISTORY/第003章.md" --history-dir "$HISTORY" --json > "$TMP_DIR/rejected.json"
rejected_status=$?
set -e
[ "$rejected_status" -eq 2 ] || { echo "FAIL: four consecutive tagged turns must reject" >&2; exit 1; }
node - "$TMP_DIR/rejected.json" <<'NODE'
const fs = require('fs');
const report = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const finding = report.findings.find((item) => item.code === 'consecutive-tagged-turns');
if (report.status !== 'rejected' || !finding || report.metrics.max_consecutive_tagged_turns !== 5) throw new Error(JSON.stringify(report));
if (report.next_action !== 'return_to_narrative_writer_for_contextual_revision') throw new Error(report.next_action);
if (report.examples.length < 5 || !report.examples.every((item) => item.line && item.context)) throw new Error('missing line evidence');
NODE

set +e
node "$SCRIPT" --current "$HISTORY/不存在.md" >/dev/null 2>&1
missing_status=$?
set -e
[ "$missing_status" -eq 3 ] || { echo "FAIL: unreadable input should exit 3" >&2; exit 1; }

echo "OK: dialogue drift gate separates advisories from blocking consecutive-tag degeneration"
