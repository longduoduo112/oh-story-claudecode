#!/bin/bash
# test-degeneration.sh — regression tests for the model-degeneration detector.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
if [ -z "$REPO_ROOT" ]; then
  echo "Error: not in a git repository" >&2
  exit 1
fi

SCRIPT="$REPO_ROOT/skills/story-deslop/scripts/check-degeneration.js"
TMP_DIR="$(mktemp -d)"
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

POS="$TMP_DIR/degen-positive.md"
NEG="$TMP_DIR/degen-negative.md"
OUT="$TMP_DIR/out.json"

# Positive: 紧邻整行复读 + 长句复读3次 + AI自指 + 括号省略占位符 + 末尾截断。
cat > "$POS" <<'EOF'
他握紧了拳头，慢慢站起身来，眼里全是不甘。
他握紧了拳头，慢慢站起身来，眼里全是不甘。
她看着窗外那场下了整夜的大雨，心里空落落的。
过了一会儿。
她看着窗外那场下了整夜的大雨，心里空落落的。
又过了一会儿。
她看着窗外那场下了整夜的大雨，心里空落落的。
作为一个AI语言模型，我无法继续生成这段内容。
（此处省略五百字）
他转过身，慢慢地走向门口，手还在
EOF

# Negative: 通俗网文体裁内的「正常重复」必须不报——弹幕道歉刷屏、短句排比、对话复沓。
cat > "$NEG" <<'EOF'
他站在原地，看着那条消息，久久没有动。
“对不起。”
“对不起。”
“对不起。”
我等你。我等你。我等你。
风很大，吹得人睁不开眼。
作为一个人工智能时代的产物，他对孤独习以为常。
“作为人工智能，我会一直陪着你。”
这一刻，他终于明白了什么叫做释怀。
EOF

set +e
node "$SCRIPT" --json "$POS" > "$OUT"
pos_status=$?
set -e
if [ "$pos_status" -ne 1 ]; then
  echo "FAIL: expected degeneration detector to exit 1 on positive fixture, got $pos_status" >&2
  cat "$OUT" >&2 || true
  exit 1
fi

node - "$OUT" <<'NODE'
const fs = require('fs');
const report = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const counts = report.findings.reduce((m, f) => ((m[f.type] = (m[f.type] || 0) + 1), m), {});
const want = { 'verbatim-repeat': 2, 'placeholder-leak': 2, 'language-leak': 1, 'truncated': 1 };
if (report.findings.length !== 6) {
  throw new Error(`expected 6 positive findings, got ${report.findings.length}: ${JSON.stringify(report.findings.map((f) => `${f.type}@${f.line}`))}`);
}
for (const [type, n] of Object.entries(want)) {
  if (counts[type] !== n) throw new Error(`expected ${n} ${type}, got ${counts[type] || 0}`);
}
NODE

# Negative fixture must be clean (exit 0). 通俗网文 的排比/复沓/弹幕刷屏不是退化。
set +e
neg_out="$(node "$SCRIPT" "$NEG" 2>&1)"
neg_status=$?
set -e
if [ "$neg_status" -ne 0 ]; then
  echo "FAIL: degeneration detector false-positive on legit 重复/排比/弹幕 prose (exit $neg_status):" >&2
  echo "$neg_out" >&2
  exit 1
fi

# --- AI 自指（不带拒绝语）：上面正例的第29行其实是被「生成拒绝语」规则接住的，AI 自指规则
#     本身此前零覆盖——带型号后缀的最典型退化开场（AI语言模型/AI助手/人工智能语言模型/AI模型）
#     整类漏检也照样通过。这条只留自指、不含 我无法/我不能，逐条锁到 label 上。
AI_SELF="$TMP_DIR/ai-selfref.md"
cat > "$AI_SELF" <<'EOF'
作为一个AI语言模型，我需要提醒您。
作为一个AI助手，这段内容涉及敏感话题。
作为一个人工智能语言模型，我会尽力帮您续写。
作为一个AI模型，这段情节需要调整。
他把灯关了。
EOF
set +e
node "$SCRIPT" --json "$AI_SELF" > "$OUT"
ai_self_status=$?
set -e
if [ "$ai_self_status" -ne 1 ]; then
  echo "FAIL: AI 自指 fixture 应退出 1，实际 $ai_self_status" >&2
  cat "$OUT" >&2 || true
  exit 1
fi
node - "$OUT" <<'NODE'
const fs = require('fs');
const r = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const leaks = r.findings.filter((f) => f.type === 'placeholder-leak');
if (leaks.length !== 4) {
  throw new Error(`expected 4 AI 自指 findings, got ${leaks.length}: ${JSON.stringify(leaks.map((f) => `${f.line}:${f.excerpt}`))}`);
}
if (!leaks.every((f) => f.message.includes('AI 自指'))) {
  throw new Error('必须由 AI 自指规则命中（不得靠拒绝语规则代劳）: ' + JSON.stringify(leaks.map((f) => f.message)));
}
NODE

# --- 工程词泄漏 meta-leak（issue #173 comment 4814607240）---
META_POS="$TMP_DIR/meta-positive.md"
META_NEG="$TMP_DIR/meta-negative.md"

# 正例：纯工程词(细纲/情节点) + 章节结构词(本章/下一章，含对话里的) + 系统标签词(任务描述)。
cat > "$META_POS" <<'EOF'
## 第5章 真相
他握紧了拳头，慢慢站起身来。
本章他终于发现了真相。
“该到下一章了。”他低声说。
按照细纲，他应该先去找她。
这个情节点其实早就埋下了。
任务描述：保护好那个女孩。
EOF
set +e
node "$SCRIPT" --json "$META_POS" > "$OUT"
set -e
node - "$OUT" <<'NODE'
const fs = require('fs');
const report = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const meta = report.findings.filter((f) => f.type === 'meta-leak');
if (meta.length !== 5) {
  throw new Error(`expected 5 meta-leak findings (本章/下一章/细纲/情节点/任务描述), got ${meta.length}: ${JSON.stringify(meta.map((f) => f.excerpt))}`);
}
NODE

# --- 裸英文词泄漏（实测样本：「watcher 伏在暗里」）---
# 中文正文里冒出的小写英文常用词基本是内部代号/占位没换成中文名。判据要两层：
# 旧规则曾要求整行以中文为主；现在由文档级 language gate 判定。未登记的
# PDF/LABADMIN 也必须 blocking；只有文件名/扩展名等非叙事结构保护。
BARE_POS="$TMP_DIR/bare-positive.md"
BARE_NEG="$TMP_DIR/bare-negative.md"
cat > "$BARE_POS" <<'PROSE'
她望向废园更深处，那里土气眼还压着没说破的东西，watcher 伏在暗里，像是也在等她下一步。
这一趟东行本是为母系的根，如今根到了手，却牵出更长的 shadow 线。
PROSE
set +e
node "$SCRIPT" --json "$BARE_POS" > "$OUT"
set -e
node - "$OUT" <<'NODE'
const fs = require('fs');
const report = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const hits = report.findings.filter((f) => /裸英文词泄漏/.test(f.message));
if (hits.length !== 2) {
  throw new Error(`expected 2 bare-latin leaks (watcher/shadow), got ${hits.length}: ${JSON.stringify(hits.map((f) => f.excerpt))}`);
}
if (!hits.every((f) => f.severity === 'blocking')) {
  throw new Error('bare-latin leaks outside dialogue must be blocking');
}
NODE
echo "  OK 裸英文词泄漏：watcher / shadow 命中且为 blocking"

cat > "$BARE_NEG" <<'PROSE'
两个文件的签名像素、章印缺口和纸纤维灰点完全重合，批次放行PDF 就是抠出来的。
排好授权生效、检索DB-40、调阅原图和权限关闭的时点，逐项核过。
我的名字从《星桥项目_v28.pptx》首页消失，掌声还没停下来。
四月七日凌晨，周妍用 LABADMIN 账号重新上传过同名文件。
PROSE
set +e
node "$SCRIPT" --json "$BARE_NEG" > "$OUT"
set -e
node - "$OUT" <<'NODE'
const fs = require('fs');
const report = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const hits = report.findings.filter((f) => f.type === 'language-leak');
const expectedLines = [1, 2, 4];
if (hits.length !== expectedLines.length || !expectedLines.every((line) => hits.some((f) => f.line === line))) {
  throw new Error(`PDF / DB-40 / LABADMIN 应 blocking，文件名应保护，got ${JSON.stringify(hits.map((f) => `${f.line}:${f.excerpt}`))}`);
}
if (!hits.every((f) => f.severity === 'blocking')) {
  throw new Error(`未授权缩写与型号应全为 blocking: ${JSON.stringify(hits)}`);
}
NODE
echo "  OK 缩写/型号零容忍：PDF / DB-40 / LABADMIN blocking，.pptx 文件名保护"

# --- 中文正文语言门禁：纯英文句段 / 完整英文台词 / 连续短语 / TitleCase / 短词 ---
LANG_POS="$TMP_DIR/language-zh-positive.md"
cat > "$LANG_POS" <<'PROSE'
她盯着墙上的字：The room was quiet and nobody moved.
Please close the old door before midnight.
她停住。Go home. 她没有回头。
他说：“Take the old road.”
她在名单里看见 Aiden，指尖停住了。
她低声说 go，门却没有开。
“别动。”她看见 shadow 藏在门后。
她说：“去 shadow 那边。”然后关灯。
他说：“Sorry.”
她只回了一句：“Go.”
门外传来一声：“Yes.”
Sorry.
PROSE
set +e
node "$SCRIPT" --language=zh --json "$LANG_POS" > "$OUT"
lang_pos_status=$?
set -e
if [ "$lang_pos_status" -ne 1 ]; then
  echo "FAIL: zh 语言门禁正例应退出 1，实际 $lang_pos_status" >&2
  cat "$OUT" >&2 || true
  exit 1
fi
node - "$OUT" <<'NODE'
const fs = require('fs');
const r = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const hits = r.findings.filter((f) => f.type === 'language-leak');
const expected = new Map([
  [1, ['blocking', '连续英文短语泄漏']],
  [2, ['blocking', '纯英文句段泄漏']],
  [3, ['blocking', '纯英文句段泄漏']],
  [4, ['blocking', '完整英文台词泄漏']],
  [5, ['blocking', '英文专名/短词泄漏']],
  [6, ['blocking', '英文专名/短词泄漏']],
  [7, ['blocking', '裸英文词泄漏']],
  [8, ['blocking', '裸英文词泄漏']],
  [9, ['blocking', '完整英文台词泄漏']],
  [10, ['blocking', '完整英文台词泄漏']],
  [11, ['blocking', '完整英文台词泄漏']],
  [12, ['blocking', '纯英文句段泄漏']],
]);
if (hits.length !== expected.size) {
  throw new Error(`expected ${expected.size} language hits, got ${hits.length}: ${JSON.stringify(hits.map((f) => `${f.line}:${f.severity}:${f.message}`))}`);
}
for (const [line, [severity, label]] of expected) {
  const hit = hits.find((f) => f.line === line);
  if (!hit || hit.severity !== severity || !hit.message.includes(label)) {
    throw new Error(`line ${line} expected ${severity}/${label}, got ${JSON.stringify(hit)}`);
  }
}
NODE
echo "  OK 中文语言门禁：英文句段/台词/短语/TitleCase 全部 blocking，引号 offset 精确"

# --- .deslop-whitelist：Latin token / 完整短句只做大小写敏感的精确豁免，不做子串 ---
WHITE_ROOT="$TMP_DIR/whitelist-project"
WHITE_BODY="$WHITE_ROOT/正文"
mkdir -p "$WHITE_BODY"
cat > "$WHITE_ROOT/.deslop-whitelist" <<'EOF'
# AI 不能把 Aiden 一并豁免
AI
shadow
Open the door
Sorry
EOF
WHITE_DOC="$WHITE_BODY/chapter.md"
cat > "$WHITE_DOC" <<'PROSE'
她在名单里看见 Aiden。
门牌上写着 shadow。
他说：“Open the door.”
他说：“Open the door now.”
门牌上写着 shadowed。
他说：“Sorry.”
他说：“OK。”
PROSE
set +e
node "$SCRIPT" --language=zh --json "$WHITE_DOC" > "$OUT"
set -e
node - "$OUT" <<'NODE'
const fs = require('fs');
const hits = JSON.parse(fs.readFileSync(process.argv[2], 'utf8')).findings.filter((f) => f.type === 'language-leak');
if (hits.length !== 4) {
  throw new Error(`whitelist 精确匹配应只剩 Aiden / Open...now / shadowed / OK 四项，got ${JSON.stringify(hits.map((f) => `${f.line}:${f.message}`))}`);
}
const aiden = hits.find((f) => f.line === 1);
if (!aiden || aiden.severity !== 'blocking' || !aiden.message.includes('Aiden')) {
  throw new Error(`白名单 AI 不得子串豁免 Aiden: ${JSON.stringify(aiden)}`);
}
if (hits.some((f) => f.line === 2 || f.line === 3 || f.line === 6)) {
  throw new Error('精确登记的 Latin token / 完整英文短句应豁免');
}
if (![4, 5, 7].every((line) => hits.some((f) => f.line === line && f.severity === 'blocking'))) {
  throw new Error('完整短句/Latin token 的超集与未登记缩写不得豁免');
}
NODE
echo "  OK .deslop-whitelist：AI≠Aiden，token/完整短句精确豁免，超集不豁免"

# --- 等长保护遮罩：只保护 URL/邮箱/Markdown target/inline code/路径/文件名/数字 ---
PROTECTED="$TMP_DIR/language-protected.md"
cat > "$PROTECTED" <<'PROSE'
她把结果发到 https://example.com/open/the-door?file=draft.txt。
请联系 editor@example.com，不要抄送别人。
正文见 [说明页](docs/open-the-door.md)，那里有原件。
命令写成 `open the old door`，不要执行。
文件在 /Users/demo/story/open-door/draft.txt，备份是 C:\draft\story_v28.md。
她核对 draft.txt、.pptx 和 2026-08-12。
PROSE
set +e
node "$SCRIPT" --language=zh --json "$PROTECTED" > "$OUT"
set -e
node - "$OUT" <<'NODE'
const fs = require('fs');
const hits = JSON.parse(fs.readFileSync(process.argv[2], 'utf8')).findings.filter((f) => f.type === 'language-leak');
if (hits.length !== 0) {
  throw new Error(`保护区不得产生语言泄漏 finding: ${JSON.stringify(hits.map((f) => `${f.line}:${f.message}`))}`);
}
NODE
echo "  OK 等长遮罩：URL/邮箱/Markdown target/代码/路径/文件名/数字 0 误伤"

# 缩写、型号、科学名称、分组字母与剧情代号都是叙事内容，必须人工确认后登记白名单。
NARRATIVE_LATIN="$TMP_DIR/narrative-latin.md"
cat > "$NARRATIVE_LATIN" <<'PROSE'
她核对 DB-40、GPT-4、story_v28 和 A13。
医生复核 Ara h 2、F17-Q、V0、PA66、R66-7、QP-07、PDF、KB 和 IP。
她把 A客户和 B客户分开记录。
封签下方，A、B、C三个编号还在。
系统只写了一个字母：C，文件名后面有Q。
PROSE
set +e
node "$SCRIPT" --language=zh --fail-on=blocking --json "$NARRATIVE_LATIN" > "$OUT"
narrative_latin_status=$?
set -e
if [ "$narrative_latin_status" -ne 1 ]; then
  echo "FAIL: 未授权的缩写/型号/分组字母必须 blocking" >&2
  exit 1
fi
node - "$OUT" <<'NODE'
const fs = require('fs');
const hits = JSON.parse(fs.readFileSync(process.argv[2], 'utf8')).findings.filter((f) => f.type === 'language-leak');
for (const line of [1, 2, 3, 4, 5]) {
  const lineHits = hits.filter((f) => f.line === line);
  if (lineHits.length === 0 || !lineHits.every((f) => f.severity === 'blocking')) {
    throw new Error(`line ${line} 应包含 blocking language-leak: ${JSON.stringify(lineHits)}`);
  }
}
NODE
echo "  OK 叙事内 Latin 零容忍：缩写/型号/分组字母必须白名单授权"

# --- 边界回归：全大写句、无句号单词台词、短中文 auto 不得逃逸 ---
LANG_EDGE="$TMP_DIR/language-edge.md"
cat > "$LANG_EDGE" <<'PROSE'
她看见墙上写着：GET OUT NOW.
她只说：“Go”
她说：The room was quiet.
正文见 [docs](https://example.com/doc)。
PROSE
set +e
node "$SCRIPT" --language=auto --fail-on=blocking --json "$LANG_EDGE" > "$OUT"
lang_edge_status=$?
set -e
if [ "$lang_edge_status" -ne 1 ]; then
  echo "FAIL: 全大写英文/无句号单词台词/短中文 auto 均应 blocking" >&2
  cat "$OUT" >&2 || true
  exit 1
fi
node - "$OUT" <<'NODE'
const fs = require('fs');
const hits = JSON.parse(fs.readFileSync(process.argv[2], 'utf8')).findings.filter((f) => f.type === 'language-leak');
for (const line of [1, 2, 3, 4]) {
  const hit = hits.find((f) => f.line === line);
  if (!hit || hit.severity !== 'blocking') throw new Error(`line ${line} must be blocking: ${JSON.stringify(hit)}`);
}
NODE
echo "  OK 全大写英文/无句号单词台词/短中文 auto 均不逃逸"

# `-` 是 Windows/POSIX 通用的显式 stdin 别名，CRLF 也必须保留正确行号。
set +e
printf '她开门。\r\nThe room was quiet.\r\n' \
  | node "$SCRIPT" --language=zh --fail-on=blocking --json - > "$OUT"
stdin_dash_status=$?
set -e
if [ "$stdin_dash_status" -ne 1 ]; then
  echo "FAIL: - stdin CRLF 中的英文句必须 blocking" >&2
  exit 1
fi
node - "$OUT" <<'NODE'
const fs = require('fs');
const hit = JSON.parse(fs.readFileSync(process.argv[2], 'utf8')).findings.find((f) => f.type === 'language-leak');
if (!hit || hit.file !== '-' || hit.line !== 2) throw new Error(`dash stdin/CRLF line contract drift: ${JSON.stringify(hit)}`);
NODE
echo "  OK - stdin + CRLF 保留 language-leak 行号"

# POSIX 保留惯用的 /dev/stdin 别名。Git Bash/MSYS 会在启动原生
# node.exe 前改写这个参数，Windows 端不把它当公共契约，统一使用 `-`。
case "$(uname -s 2>/dev/null || true)" in
  MINGW*|MSYS*|CYGWIN*) ;;
  *)
    set +e
    printf '她开门。\r\nThe room was quiet.\r\n' \
      | node "$SCRIPT" --language=zh --fail-on=blocking --json /dev/stdin > "$OUT"
    stdin_crlf_status=$?
    set -e
    if [ "$stdin_crlf_status" -ne 1 ]; then
      echo "FAIL: /dev/stdin CRLF 中的英文句必须 blocking" >&2
      exit 1
    fi
    node - "$OUT" <<'NODE'
const fs = require('fs');
const hit = JSON.parse(fs.readFileSync(process.argv[2], 'utf8')).findings.find((f) => f.type === 'language-leak');
if (!hit || hit.file !== '/dev/stdin' || hit.line !== 2) throw new Error(`stdin/CRLF line contract drift: ${JSON.stringify(hit)}`);
NODE
    echo "  OK POSIX /dev/stdin + CRLF 保留 language-leak 行号"
    ;;
esac

# --- Markdown 围栏长度、reference id/定义、Unicode 路径均是不可见或结构性内容 ---
MARKDOWN_PROTECTED="$TMP_DIR/markdown-protected.md"
cat > "$MARKDOWN_PROTECTED" <<'PROSE'
````js
const name = "hello";
```
`````js
The room was quiet and nobody moved.
````
正文见 [说明][docs]。
[docs]: https://example.com/open-the-door
文件在 /Users/张三/open-door。
她继续走。
PROSE
set +e
node "$SCRIPT" --language=zh --json "$MARKDOWN_PROTECTED" > "$OUT"
markdown_status=$?
set -e
if [ "$markdown_status" -ne 0 ]; then
  echo "FAIL: 长围栏、reference id/定义、Unicode 路径不得产生 finding" >&2
  cat "$OUT" >&2 || true
  exit 1
fi
echo "  OK 长 Markdown 围栏、reference id/定义、Unicode 无扩展名路径受保护"

# --- 白名单必须按每个文件的项目根解析，不能被 cwd 跨项目污染 ---
WHITE_A="$TMP_DIR/whitelist-a"
WHITE_B="$TMP_DIR/whitelist-b"
mkdir -p "$WHITE_A" "$WHITE_B"
cat > "$WHITE_A/.deslop-whitelist" <<'EOF'
shadow
EOF
cat > "$WHITE_A/a.md" <<'PROSE'
门牌写着 shadow。
PROSE
cat > "$WHITE_B/b.md" <<'PROSE'
她看见 shadow 藏在门后。
PROSE
set +e
(cd "$WHITE_A" && node "$SCRIPT" --language=zh --fail-on=blocking --json a.md "$WHITE_B/b.md") > "$OUT"
white_isolation_status=$?
set -e
if [ "$white_isolation_status" -ne 1 ]; then
  echo "FAIL: cwd 白名单不得豁免另一项目" >&2
  cat "$OUT" >&2 || true
  exit 1
fi
node - "$OUT" "$WHITE_A/a.md" "$WHITE_B/b.md" <<'NODE'
const fs = require('fs');
const report = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const aPath = process.argv[3];
const bPath = process.argv[4];
if (report.findings.some((f) => f.file === 'a.md')) throw new Error('project A exact whitelist must still apply');
const b = report.findings.find((f) => f.file === bPath && f.type === 'language-leak');
if (!b || b.severity !== 'blocking') throw new Error(`project B must not inherit cwd whitelist: ${JSON.stringify(report.findings)}`);
NODE
echo "  OK 多文件扫描按各自项目根隔离 .deslop-whitelist"

# --- column 以原文 Unicode code point 计数，保留前导空白 ---
COLUMN_DOC="$TMP_DIR/column.md"
cat > "$COLUMN_DOC" <<'PROSE'
  🙂她看见 Aiden。
她回头。
PROSE
set +e
node "$SCRIPT" --language=zh --json "$COLUMN_DOC" > "$OUT"
set -e
node - "$OUT" <<'NODE'
const fs = require('fs');
const hit = JSON.parse(fs.readFileSync(process.argv[2], 'utf8')).findings.find((f) => f.type === 'language-leak');
if (!hit || hit.column !== 8) throw new Error(`Aiden must start at visible source column 8, got ${JSON.stringify(hit)}`);
NODE
echo "  OK language-leak column 保留前导空白并按 Unicode code point 计数"

# --- auto/en：英文文档跳过语言门禁；显式 zh 才拦。en 仍保留其他退化检测。 ---
EN_DOC="$TMP_DIR/english-document.md"
cat > "$EN_DOC" <<'PROSE'
The room was quiet and nobody moved.
A storm rolled over the hills before dawn.
PROSE
for mode in auto en; do
  set +e
  node "$SCRIPT" --language="$mode" --json "$EN_DOC" > "$OUT"
  en_status=$?
  set -e
  if [ "$en_status" -ne 0 ]; then
    echo "FAIL: 英文文档 --language=$mode 应放行，实际退出 $en_status" >&2
    cat "$OUT" >&2 || true
    exit 1
  fi
done
set +e
node "$SCRIPT" --language=zh --json "$EN_DOC" > "$OUT"
zh_english_status=$?
set -e
if [ "$zh_english_status" -ne 1 ]; then
  echo "FAIL: 同一英文文档显式 --language=zh 应被拦截" >&2
  exit 1
fi

EN_META="$TMP_DIR/english-meta.md"
cat > "$EN_META" <<'PROSE'
The 细纲 says the door must stay closed.
PROSE
set +e
node "$SCRIPT" --language=en --json "$EN_META" > "$OUT"
en_meta_status=$?
set -e
if [ "$en_meta_status" -ne 1 ]; then
  echo "FAIL: --language=en 只跳过语言门禁，不能跳过原有工程词检测" >&2
  exit 1
fi
node - "$OUT" <<'NODE'
const fs = require('fs');
const r = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
if (!r.findings.some((f) => f.type === 'meta-leak' && f.severity === 'blocking')) throw new Error('en 模式必须保留 meta-leak');
if (r.findings.some((f) => f.type === 'language-leak')) throw new Error('en 模式不得运行 language gate');
NODE
echo "  OK language=auto/en 放行英文文档；language=en 仍保留原退化/工程词检测"

# --- 命中级引号判断：同一行其他台词不能把引号外的工程词/软拒绝语误降级或豁免 ---
QUOTE_EXACT="$TMP_DIR/quote-exact.md"
cat > "$QUOTE_EXACT" <<'PROSE'
“细纲只是个术语。”她合上本子，细纲又从旁白里漏了出来。
“作为AI，我会保护你。”她转身。作为AI，我需要提醒您。
PROSE
set +e
node "$SCRIPT" --language=zh --json "$QUOTE_EXACT" > "$OUT"
set -e
node - "$OUT" <<'NODE'
const fs = require('fs');
const r = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const meta = r.findings.find((f) => f.type === 'meta-leak' && f.line === 1);
const self = r.findings.find((f) => f.type === 'placeholder-leak' && f.line === 2);
if (!meta || meta.severity !== 'blocking') throw new Error(`引号外第二个细纲必须优先 blocking: ${JSON.stringify(meta)}`);
if (!self || self.severity !== 'blocking') throw new Error(`引号外 AI 自指不能因本行另有台词而豁免: ${JSON.stringify(self)}`);
NODE
echo "  OK 引号命中按精确 offset 判定，同一行其他引号不误降级"

# --- 章号引用泄漏 chNN（实测泄漏样本：「她在 ch13 便学乖了」）---
# 中文工程词表只收「第X章/本章/前文」，ch13 这类英文缩写一条都不命中，
# 曾整段漏进正文无人拦。正例查五种变体全中，负例查 Bach13/A13 不误伤。
CHAPREF_POS="$TMP_DIR/chapref-positive.md"
CHAPREF_NEG="$TMP_DIR/chapref-negative.md"

cat > "$CHAPREF_POS" <<'EOF'
她在 ch13 便学乖了，灵田异样能藏便藏。
Ch.13 那夜合苔三倍灵气第一次涌，她只当是土砂。
CH 13 之后，母亲教她认清楚自己是谁。
chapter 13 的事她记了很久。
chap13 那页她折了角。
EOF
set +e
node "$SCRIPT" --json "$CHAPREF_POS" > "$OUT"
set -e
node - "$OUT" <<'NODE'
const fs = require('fs');
const report = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const hits = report.findings.filter((f) => f.type === 'meta-leak' && /章号引用泄漏/.test(f.message));
if (hits.length !== 5) {
  throw new Error(`expected 5 chapter-ref leaks (ch13/Ch.13/CH 13/chapter 13/chap13), got ${hits.length}: ${JSON.stringify(hits.map((f) => f.excerpt))}`);
}
if (!hits.every((f) => f.severity === 'blocking')) {
  throw new Error('chapter-ref leaks outside dialogue must be blocking');
}
NODE

cat > "$CHAPREF_NEG" <<'EOF'
她翻开巴赫 Bach13 号作品的谱子，指尖停在封皮上。
他数了数，一共 13 个箱子，每个箱子上都写着 A13。
苏苗把册子合上，灵田活格又拓开半格。
EOF
set +e
node "$SCRIPT" --json "$CHAPREF_NEG" > "$OUT"
set -e
node - "$OUT" <<'NODE'
const fs = require('fs');
const report = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const hits = report.findings.filter((f) => f.type === 'meta-leak');
if (hits.length !== 0) {
  throw new Error(`Bach13 / A13 / 正常正文不得判为章号泄漏，got ${JSON.stringify(hits.map((f) => f.excerpt))}`);
}
NODE

# 负例：标题行「第N章 章名」(无 ## 前缀) 必须不算工程词泄漏；正常正文 0 命中。
cat > "$META_NEG" <<'EOF'
第1章 军宣新星
他站在台上，看着台下黑压压的人群。
风很大，吹得旗子猎猎作响。
他握紧了话筒，深吸一口气。
EOF
set +e
meta_neg_out="$(node "$SCRIPT" "$META_NEG" 2>&1)"
meta_neg_status=$?
set -e
if [ "$meta_neg_status" -ne 0 ]; then
  echo "FAIL: meta-leak false-positive on chapter title line / clean prose (exit $meta_neg_status):" >&2
  echo "$meta_neg_out" >&2
  exit 1
fi

# --- 引号整行豁免回归：混合行（叙述 + 引号内物件）的复读不能被一个引号整行跳过 ---
MIX="$TMP_DIR/mix-repeat.md"
cat > "$MIX" <<'EOF'
他把纸条展开，上面写着“归来”，她看着窗外那场整夜的大雨，心里空落落的。
他把纸条展开，上面写着“归来”，她看着窗外那场整夜的大雨，心里空落落的。
他把纸条展开，上面写着“归来”，她看着窗外那场整夜的大雨，心里空落落的。
EOF
set +e
node "$SCRIPT" --json "$MIX" > "$OUT"
set -e
node - "$OUT" <<'NODE'
const fs = require('fs');
const r = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const rep = r.findings.filter((f) => f.type === 'verbatim-repeat');
if (rep.length === 0) throw new Error('引号整行豁免回归：混合行复读未被检出');
if (!rep.every((f) => f.severity === 'blocking')) throw new Error('verbatim-repeat 应为 severity=blocking');
NODE

# 纯台词复沓仍豁免（体裁手法）：三行相同台词不报。
PURE_DLG="$TMP_DIR/pure-dialogue.md"
cat > "$PURE_DLG" <<'EOF'
“我不走。”
“我不走。”
“我不走。”
EOF
set +e
pure_dlg_out="$(node "$SCRIPT" "$PURE_DLG" 2>&1)"
pure_dlg_status=$?
set -e
if [ "$pure_dlg_status" -ne 0 ]; then
  echo "FAIL: 纯台词复沓被误判为复读 (exit $pure_dlg_status):" >&2
  echo "$pure_dlg_out" >&2
  exit 1
fi

# --- severity 字段 + --fail-on 语义：仅 advisory（tier2）时默认退出 1，--fail-on=blocking 退出 0 ---
ADV="$TMP_DIR/advisory-only.md"
cat > "$ADV" <<'EOF'
他翻看着那段记录，想起本章之前发生的事，那个伏笔一直没人提起。
EOF
set +e
node "$SCRIPT" --json "$ADV" > "$OUT"
adv_all_status=$?
node "$SCRIPT" --fail-on=blocking "$ADV" >/dev/null 2>&1
adv_blocking_status=$?
set -e
node - "$OUT" <<'NODE'
const fs = require('fs');
const r = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
if (r.findings.length === 0) throw new Error('expected tier2 advisory finding');
if (!r.findings.every((f) => f.severity === 'advisory')) {
  throw new Error('tier2-only fixture 应全为 advisory: ' + JSON.stringify(r.findings.map((f) => f.severity)));
}
NODE
if [ "$adv_all_status" -ne 1 ]; then
  echo "FAIL: advisory-only 默认 --fail-on=all 应退出 1，实际 $adv_all_status" >&2
  exit 1
fi
if [ "$adv_blocking_status" -ne 0 ]; then
  echo "FAIL: advisory-only --fail-on=blocking 应退出 0，实际 $adv_blocking_status" >&2
  exit 1
fi

# --- tier1 工程词：叙述行 blocking；对话行（写手/编剧题材合法台词）降级 advisory ---
TIER1="$TMP_DIR/tier1-dialogue.md"
cat > "$TIER1" <<'EOF'
“今天的字数目标是六千字。”他盯着屏幕，烟一根接一根。
按照字数目标，他还差六千字没写。
EOF
set +e
node "$SCRIPT" --json "$TIER1" > "$OUT"
set -e
node - "$OUT" <<'NODE'
const fs = require('fs');
const meta = JSON.parse(fs.readFileSync(process.argv[2], 'utf8')).findings.filter((f) => f.type === 'meta-leak');
const dlg = meta.find((f) => f.line === 1);
const nar = meta.find((f) => f.line === 2);
if (!dlg || dlg.severity !== 'advisory') throw new Error('tier1 在对话行应为 advisory: ' + JSON.stringify(dlg));
if (!nar || nar.severity !== 'blocking') throw new Error('tier1 在叙述行应为 blocking: ' + JSON.stringify(nar));
NODE

# --- wiring：携带 check-degeneration.js 副本的 skill 必须在 SKILL.md 工作流中实际调用它 ---
for skill_js in $(find "$REPO_ROOT/skills" -name check-degeneration.js); do
  skill_md="$(dirname "$(dirname "$skill_js")")/SKILL.md"
  if [ -f "$skill_md" ] && ! grep -q 'check-degeneration.js' "$skill_md"; then
    echo "FAIL: $skill_md 携带 check-degeneration.js 副本却未在工作流中调用" >&2
    exit 1
  fi
done

echo "Degeneration detector regression tests passed."
