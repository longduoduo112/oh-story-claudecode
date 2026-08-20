#!/bin/bash
# test-prose-backstop-hook.sh — regression tests for check-prose-after-write.sh
# 核心保证：① 绝不过度捕获非正文文件（代码/细纲/设定/大纲/游离正文）；② 真正文兜底触发；
# ③ 轻量内容网抓对硬信号（截断/拒绝语/工程词/复读），干净正文（排比+对话+悬念）静默。
# 过度捕获用路径门验证（不依赖解释器）；内容网用内嵌 python（与 parity 测试同源）。
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
[ -z "$REPO_ROOT" ] && { echo "Error: not in a git repository" >&2; exit 1; }
HOOK="$REPO_ROOT/skills/story-setup/references/templates/hooks/check-prose-after-write.sh"
[ -f "$HOOK" ] || { echo "FAIL: hook not found: $HOOK" >&2; exit 1; }

bash -n "$HOOK" || { echo "FAIL: hook has syntax errors" >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
# 真书结构：设定.md + 大纲/ + 正文/
mkdir -p "$TMP/某书/正文" "$TMP/某书/大纲" "$TMP/docs/正文" "$TMP/游离/正文"
printf '# 设定\n主角顾临。\n' > "$TMP/某书/设定.md"
printf '## 细纲（第1章）\n- 情节点序列：本章细纲，作为AI我无法继续。他握紧拳头。他握紧拳头。\n' > "$TMP/某书/大纲/细纲_第001章.md"
printf '# 大纲\n第1章 第2章 细纲 本章 下一章\n' > "$TMP/某书/大纲/大纲.md"
printf '# 卷纲\n本卷细纲。\n' > "$TMP/某书/大纲/卷纲_第1卷.md"
printf 'const x=1; // 细纲 本章 下一章 作为AI我无法继续 复读复读复读\n' > "$TMP/某书/x.js"
printf '## 正文\n按照细纲，作为AI我无法继续。\n' > "$TMP/docs/正文.md"   # 正文.md 但无 设定.md 兄弟
printf '## 第5章\n按照细纲，作为AI我无法继续。\n' > "$TMP/游离/正文/第005章.md" # 正文/第N章 但无书结构
printf '他' > "$TMP/某书/正文/第001章_截断.md"                            # 真正文，极短 → 落盘触发

run() { CLAUDE_PROJECT_DIR="$TMP" CLAUDE_TOOL_INPUT="{\"tool_input\":{\"file_path\":\"$1\"}}" bash "$HOOK" 2>/dev/null; }

fails=0
expect_silent() {
  local out; out="$(run "$1")"
  if [ -n "$out" ]; then echo "FAIL: expected silent hook result: $1" >&2; echo "$out" | head -2 >&2; fails=$((fails+1)); fi
}
expect_fire() {
  local out; out="$(run "$1")"
  if [ -z "$out" ]; then echo "FAIL: backstop did not fire on real 正文: $1" >&2; fails=$((fails+1)); fi
}

# ① 绝不捕获这些非正文文件（含工程词/复读/拒绝语文本，证明确实没被扫）
expect_silent "$TMP/某书/大纲/细纲_第001章.md"
expect_silent "$TMP/某书/大纲/大纲.md"
expect_silent "$TMP/某书/大纲/卷纲_第1卷.md"
expect_silent "$TMP/某书/x.js"
expect_silent "$TMP/某书/设定.md"
expect_silent "$TMP/docs/正文.md"
expect_silent "$TMP/游离/正文/第005章.md"
# ② 真正文（极短→落盘信号）必须触发
expect_fire "$TMP/某书/正文/第001章_截断.md"

# ③ 内容网：真正文里的硬信号必须被抓，且抓对类型；干净正文（排比+AI角色对话+悬念收尾）静默。
expect_fire_kw() {
  local out; out="$(run "$1")"
  if ! printf '%s' "$out" | grep -q "$2"; then
    echo "FAIL: 内容网未抓到「$2」: $1" >&2; printf '%s\n' "$out" | head -4 >&2; fails=$((fails+1))
  fi
}
# bash 字符串重复填充正文（不走 python stdout：Windows runner 上 python<3.15 的文本 stdout
# 是 cp1252，写中文会 UnicodeEncodeError；printf 直出脚本里的 UTF-8 字节字面量才稳）。
PAD() { local s='顾临握紧拳头慢慢走向门口心里盘算着接下来的每一步棋。'; printf '%s' "$s$s$s$s$s$s$s$s"; }
# 干净：长正文 + 排比 + 纯中文角色对话 + 悬念收尾标点 → 完全静默
{ printf '# 第10章 决战\n\n'; PAD; printf '\n要么生，要么死。\n要么战，要么逃。\n「作为智能管家，我陪你到最后。」\n他终于停下了脚步。\n'; } > "$TMP/某书/正文/第010章_决战.md"
expect_silent "$TMP/某书/正文/第010章_决战.md"
# 截断：结尾无标点
{ printf '# 第11章\n\n'; PAD; printf '\n他猛地冲过去一拳砸在'; } > "$TMP/某书/正文/第011章_截断.md"
expect_fire_kw "$TMP/某书/正文/第011章_截断.md" 截断
# 生成拒绝语 / AI 自指（叙述行，非对话）
{ printf '# 第12章\n\n'; PAD; printf '\n作为AI我无法继续创作这部分内容。\n'; } > "$TMP/某书/正文/第012章_拒绝.md"
expect_fire_kw "$TMP/某书/正文/第012章_拒绝.md" 元信息泄漏
# 工程词漏进正文
{ printf '# 第13章\n\n'; PAD; printf '\n按照本章细纲的情节点，他该出场了。\n他出场了。\n'; } > "$TMP/某书/正文/第013章_工程词.md"
expect_fire_kw "$TMP/某书/正文/第013章_工程词.md" 工程词
# 紧邻整行复读（≥8 可见字符）
{ printf '# 第14章\n\n'; PAD; printf '\n他握紧拳头一步步走过去缓缓逼近。\n他握紧拳头一步步走过去缓缓逼近。\n他停下了。\n'; } > "$TMP/某书/正文/第014章_复读.md"
expect_fire_kw "$TMP/某书/正文/第014章_复读.md" 复读

# 中文语言网：混合长英文必须由真实 PostToolUse hook 命中；旧 HTML 跳过标记自身也阻断，且不豁免语言；
# 项目级精确白名单则应放行指定 token。
{ printf '# 第15章\n\n'; PAD; printf '\n他盯着门，this should never happen again，然后关了灯。\n他走了。\n'; } > "$TMP/某书/正文/第015章_英文.md"
expect_fire_kw "$TMP/某书/正文/第015章_英文.md" 连续英文短语泄漏
{ printf '# 第16章\n\n<!-- 去味:跳过 -->\n'; PAD; printf '\n他看见 shadow 伏在暗处。\n他走了。\n'; } > "$TMP/某书/正文/第016章_去味不豁免英文.md"
expect_fire_kw "$TMP/某书/正文/第016章_去味不豁免英文.md" 裸外文字母泄漏
expect_fire_kw "$TMP/某书/正文/第016章_去味不豁免英文.md" "HTML 标记泄漏"
printf '%s\n' 'watcher' > "$TMP/某书/.deslop-whitelist"
{ printf '# 第17章\n\n'; PAD; printf '\n他看见 watcher 伏在暗处。\n他走了。\n'; } > "$TMP/某书/正文/第017章_白名单.md"
expect_silent "$TMP/某书/正文/第017章_白名单.md"

# Windows 专用的 stdin 相对路径桥：同一正文应读到书目级白名单，
# 绝对路径与 `..` 逃逸一律静默拒绝。
RELATIVE_OUT="$(printf '%s' '某书/正文/第017章_白名单.md' \
  | (cd "$TMP" && node "$(dirname "$HOOK")/story_hook_cli.js" prose-net-relative) 2>/dev/null || true)"
if [ -n "$RELATIVE_OUT" ]; then
  echo "FAIL: relative prose-net ignored exact whitelist" >&2
  printf '%s\n' "$RELATIVE_OUT" | head -2 >&2
  fails=$((fails+1))
fi
for BAD_RELATIVE in '../outside.md' '/absolute/outside.md'; do
  BAD_OUT="$(printf '%s' "$BAD_RELATIVE" \
    | (cd "$TMP" && node "$(dirname "$HOOK")/story_hook_cli.js" prose-net-relative) 2>/dev/null || true)"
  if [ -n "$BAD_OUT" ]; then
    echo "FAIL: relative prose-net accepted escaping target: $BAD_RELATIVE" >&2
    fails=$((fails+1))
  fi
done

# 项目根精确白名单也必须被读取。Windows Git Bash 下这两级 fixture 同时锁定
# root/file 必须统一到同一盘符路径空间，不得因 MSYS argv 转换漏读任一级。
rm -f "$TMP/某书/.deslop-whitelist"
printf '%s\n' 'watcher' > "$TMP/.deslop-whitelist"
expect_silent "$TMP/某书/正文/第017章_白名单.md"
rm -f "$TMP/.deslop-whitelist"
expect_fire_kw "$TMP/某书/正文/第017章_白名单.md" 裸外文字母泄漏

if [ "$fails" -ne 0 ]; then
  echo "Prose backstop hook tests FAILED ($fails)." >&2
  exit 1
fi
echo "Prose backstop hook regression tests passed."
