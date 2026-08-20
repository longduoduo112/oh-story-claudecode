#!/usr/bin/env bash
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
if (report.findings.some((item) => item.line === 4)) throw new Error('non-narrative structures must be mechanically protected');
NODE

cat > "$TMP_DIR/project/.deslop-whitelist" <<'EOF'
# 用户已单独确认逐字保留；注释本身不授权任何 token
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

run_case() {
  local name="$1" expected="$2" body="$3"
  local file="$TMP_DIR/$name.md" out="$TMP_DIR/$name.json"
  printf '%s\n' "$body" > "$file"
  set +e
  node "$SCRIPT" --json "$file" > "$out"
  local case_status=$?
  set -e
  [ "$case_status" -eq "$expected" ] || {
    echo "FAIL: $name expected $expected, got $case_status" >&2
    cat "$out" >&2
    exit 1
  }
}

# 补充对抗回归：宽字符、数学字母、混淆字符和零宽分割都不得逃逸。
run_case pure_chinese 0 '她推开窗，雨声一下涌进来。'
run_case ascii 2 '她打开dashboard页面。'
run_case unicode_variants 2 '门牌上写着ＡＩ、𝐀𝐈和ⒶⒾ。'
run_case confusables 2 '纸上写着Ⅳ、Α和А。'
run_case zero_width 2 $'纸上写着A\u200bI。'

# URL 本身是可机械保护的明确非叙事结构，但 HTML 标签/注释/实体不是交付正文，必须阻断。
run_case url_structure 0 '参考页面是 https://example.com。'
run_case inline_code_structure 0 '非叙事示例是 `<br>`。'
run_case fenced_code_structure 0 $'非叙事代码示例：\n```html\n<br>\n```'
run_case html_tag 2 '他停了一下。<br>然后继续。'
run_case html_comment 2 $'第一段。\n<!-- 内部备注 -->\n第二段。'
run_case html_entity 2 '两人之间隔着&nbsp;一步。'
node - "$TMP_DIR/html_tag.json" "$TMP_DIR/html_comment.json" "$TMP_DIR/html_entity.json" <<'NODE'
const fs = require('fs');
for (const file of process.argv.slice(2)) {
  const report = JSON.parse(fs.readFileSync(file, 'utf8'));
  if (!report.findings.some((finding) => finding.type === 'forbidden-markup')) {
    throw new Error(`expected forbidden-markup in ${file}`);
  }
}
NODE

set +e
node "$SCRIPT" "$TMP_DIR/missing.md" >/dev/null 2>&1
missing_status=$?
set -e
[ "$missing_status" -eq 3 ] || { echo "FAIL: unreadable input should exit 3" >&2; exit 1; }

echo "OK: language gate blocks narrative foreign tokens and HTML, protects structures, and honors exact user-confirmed whitelist entries"
