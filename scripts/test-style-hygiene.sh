#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
SCRIPT="$ROOT/skills/story-deslop/scripts/check-style-hygiene.js"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
EMOJI="$(printf '\360\237\230\200')"

mkdir -p "$TMP/book/设定" "$TMP/book/正文"

printf '%s\n' '# 第一章' '她把药簿合上，问：“这笔账算清了吗？”' > "$TMP/book/正文/clean.md"
node "$SCRIPT" --check --fail-on=blocking "$TMP/book/正文/clean.md" >/dev/null

printf '%s\n' '# 第二章' "她在账页旁画了一个${EMOJI}。" > "$TMP/book/正文/emoji.md"
if node "$SCRIPT" --check --fail-on=blocking "$TMP/book/正文/emoji.md" >/dev/null 2>&1; then
  echo 'FAIL: emoji must block under publish-clean' >&2
  exit 1
fi

printf '%s\n' '# 第三章' '她在纸角写了^_^，随后把纸折好。' > "$TMP/book/正文/emoticon.md"
if node "$SCRIPT" --check --fail-on=blocking "$TMP/book/正文/emoticon.md" >/dev/null 2>&1; then
  echo 'FAIL: emoticon must block under publish-clean' >&2
  exit 1
fi

printf '%s\n' '# 第四章' '“伱好。”她在旧帖子里写道。' > "$TMP/book/正文/mars.md"
if node "$SCRIPT" --check --fail-on=blocking "$TMP/book/正文/mars.md" >/dev/null 2>&1; then
  echo 'FAIL: mars text must block under publish-clean' >&2
  exit 1
fi

printf '%s\n' '# 第五章' '她追问：“你确定？！”' '纸上没有回音……' > "$TMP/book/正文/functional.md"
node "$SCRIPT" --check --fail-on=blocking "$TMP/book/正文/functional.md" >/dev/null

printf '%s\n' '# 第六章' '她连问：“你确定？？？”' > "$TMP/book/正文/spam.md"
if node "$SCRIPT" --check --fail-on=blocking "$TMP/book/正文/spam.md" >/dev/null 2>&1; then
  echo 'FAIL: punctuation spam must block' >&2
  exit 1
fi

printf '%s\n' '# 文风' '## 文风卫生' '- 文风卫生：对白弹性' > "$TMP/book/设定/文风.md"
printf '%s\n' '# 第七章' "“收到${EMOJI}。”她回完消息，收起手机。" > "$TMP/book/正文/dialogue.md"
node "$SCRIPT" --check --fail-on=blocking "$TMP/book/正文/dialogue.md" >/dev/null
printf '%s\n' '# 第八章' "她笑了${EMOJI}。" > "$TMP/book/正文/narration.md"
if node "$SCRIPT" --check --fail-on=blocking "$TMP/book/正文/narration.md" >/dev/null 2>&1; then
  echo 'FAIL: dialogue-flex must not silently allow narrative emoji' >&2
  exit 1
fi

printf '%s\n' '# 文风' '## 文风卫生' '- 火星文：复核' '- 表情符号：禁止' > "$TMP/book/设定/文风.md"
node "$SCRIPT" --json --fail-on=blocking "$TMP/book/正文/mars.md" > "$TMP/report.json"
node -e 'const r=require(process.argv[1]); if(r.blocking!==0||r.advisory!==1) process.exit(1)' "$TMP/report.json"

printf '%s\n' '伱好' > "$TMP/book/.style-hygiene-whitelist"
node "$SCRIPT" --check --fail-on=blocking "$TMP/book/正文/mars.md" >/dev/null

node --check "$SCRIPT"
echo 'OK: style hygiene blocks emoji, emoticons, mars text, punctuation spam; honors dialogue policy and exact whitelist'
