#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TOOL="$ROOT/skills/story-deslop/scripts/check-outline-copy.js"
MIRROR="$ROOT/skills/story-long-write/scripts/check-outline-copy.js"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/outline-copy.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

cmp "$TOOL" "$MIRROR"

cat > "$TMP/outline.md" <<'EOF'
## 细纲（第一章）
- 复沓锚句：“今日所言字字为证来日若有半句虚言任凭族规处置”
- 核心事件：林川把染血的旧账册放到祠堂长桌正中当众说出管家的名字
- 本章禁止提前释放：账册真正的主人
EOF

cat > "$TMP/copied.md" <<'EOF'
## 第一章 祠堂

林川把染血的旧账册放到祠堂长桌正中，当众说出管家的名字。
EOF

if node "$TOOL" --outline "$TMP/outline.md" --json "$TMP/copied.md" > "$TMP/copied.json"; then
  echo "FAIL: unanchored outline copy should block" >&2
  exit 1
fi
grep -q 'outline-verbatim-overlap' "$TMP/copied.json"

cat > "$TMP/anchored.md" <<'EOF'
## 第一章 祠堂

“今日所言，字字为证。来日若有半句虚言，任凭族规处置。”
EOF
node "$TOOL" --outline "$TMP/outline.md" --json "$TMP/anchored.md" > "$TMP/anchored.json"
grep -q '"status": "pass"' "$TMP/anchored.json"

cat > "$TMP/partial-anchor.md" <<'EOF'
## 第一章 祠堂

今日所言，字字为证，来日若有半句虚言。
EOF
if node "$TOOL" --outline "$TMP/outline.md" --json "$TMP/partial-anchor.md" > "$TMP/partial-anchor.json"; then
  echo "FAIL: copying only part of a registered refrain should still block" >&2
  exit 1
fi
grep -q 'outline-verbatim-overlap' "$TMP/partial-anchor.json"

cat > "$TMP/paraphrased.md" <<'EOF'
## 第一章 祠堂

林川进门后没急着开口。他把那本带血的账册搁到众人都看得见的地方，目光越过长桌，停在管家脸上。
EOF
node "$TOOL" --outline "$TMP/outline.md" --json "$TMP/paraphrased.md" > "$TMP/paraphrased.json"
grep -q '"status": "pass"' "$TMP/paraphrased.json"

cat > "$TMP/fifteen-outline.md" <<'EOF'
- 核心事件：一二三四五六七八九十甲乙丙丁戊
EOF
cat > "$TMP/fifteen-prose.md" <<'EOF'
## 测试
一二三四五六七八九十甲乙丙丁戊。
EOF
node "$TOOL" --outline "$TMP/fifteen-outline.md" --json "$TMP/fifteen-prose.md" > "$TMP/fifteen.json"
grep -q '"status": "pass"' "$TMP/fifteen.json"

node "$TOOL" --outline "$TMP/outline.md" --fail-on=never "$TMP/copied.md" "$TMP/paraphrased.md" > "$TMP/multiple.txt"
grep -q '\[BLOCK\]' "$TMP/multiple.txt"
grep -q '\[PASS\]' "$TMP/multiple.txt"

echo "OK: outline-copy detector blocks 16+ characters and honors exact refrain anchors"
