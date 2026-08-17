#!/bin/bash
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
SCRIPT="$REPO_ROOT/skills/story-deslop/scripts/language_gate.js"
TMP_DIR="$(mktemp -d)"
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

mkdir -p "$TMP_DIR/project/正文"
DOC="$TMP_DIR/project/正文/第001章.md"
cat > "$DOC" <<'PROSE'
她在名单里看见 Aiden。
她核对 DB-40 后合上本子。
门上印着 Α。
正文见 draft.txt，备份在 https://example.com/a.txt，联系 editor@example.com，命令是 `open door`。
PROSE

set +e
node "$SCRIPT" --json "$DOC" > "$TMP_DIR/report.json"
status=$?
set -e
[ "$status" -eq 2 ] || { echo "FAIL: language gate positive fixture should exit 2, got $status" >&2; exit 1; }
node - "$TMP_DIR/report.json" <<'NODE'
const fs = require('fs');
const report = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
if (report.status !== 'rejected' || report.next_action !== 'revise_reported_sentences_and_rerun_gate') throw new Error(JSON.stringify(report));
for (const line of [1, 2, 3]) {
  const hit = report.findings.find((item) => item.line === line);
  if (!hit || hit.severity !== 'blocking' || hit.action !== 'return_to_narrative_writer') throw new Error(`line ${line}: ${JSON.stringify(hit)}`);
}
if (report.findings.some((item) => item.line === 4)) throw new Error('non-narrative structures must be protected');
NODE

cat > "$TMP_DIR/project/.deslop-whitelist" <<'EOF'
# comments never authorize tokens
Aiden
DB-40
Open the door
EOF
cat > "$DOC" <<'PROSE'
她在名单里看见 Aiden。
她核对 DB-40。
他说：“Open the door。”
她又看见 AidenX。
她核对 DB-400。
他说：“Open the door now。”
PROSE
set +e
node "$SCRIPT" --json "$DOC" > "$TMP_DIR/report.json"
status=$?
set -e
[ "$status" -eq 2 ] || { echo "FAIL: whitelist supersets should still reject" >&2; exit 1; }
node - "$TMP_DIR/report.json" "$TMP_DIR/project/.deslop-whitelist" <<'NODE'
const fs = require('fs');
const report = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
if (report.whitelist_file !== process.argv[3]) throw new Error(`wrong whitelist: ${report.whitelist_file}`);
if (report.findings.length !== 3 || report.findings.map((item) => item.line).join(',') !== '4,5,6') {
  throw new Error(`exact whitelist contract drift: ${JSON.stringify(report.findings)}`);
}
NODE

set +e
node "$SCRIPT" "$TMP_DIR/missing.md" >/dev/null 2>&1
missing_status=$?
set -e
[ "$missing_status" -eq 3 ] || { echo "FAIL: unreadable input should exit 3" >&2; exit 1; }

echo "OK: language gate blocks narrative foreign tokens, protects structures, and honors exact whitelist entries"
