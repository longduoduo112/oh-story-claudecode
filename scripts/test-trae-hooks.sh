#!/usr/bin/env bash
# Synthetic tests for the TRAE Code strict hook contract.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

SOURCE="$REPO_ROOT/skills/story-setup/references/trae/hooks/story_trae_hook.js"
SOURCE_CORE="$REPO_ROOT/skills/story-setup/references/trae/hooks/story_hook_core.js"
HOOKS_TEMPLATE="$REPO_ROOT/skills/story-setup/references/trae/hooks/hooks.json"
DISABLED_HOOKS_TEMPLATE="$REPO_ROOT/skills/story-setup/references/trae/hooks/disabled-hooks.json"
MERGE_HOOKS="$REPO_ROOT/skills/story-setup/scripts/merge-trae-hooks.py"
CLAUDE_HOOKS_SOURCE="$REPO_ROOT/skills/story-setup/references/templates/hooks"
CLAUDE_SETTINGS_SOURCE="$REPO_ROOT/skills/story-setup/references/templates/settings-hooks.json"
ROOT="$TMP_DIR/project"
HOOK="$ROOT/.trae/hooks/story_trae_hook.js"
mkdir -p "$ROOT/.trae/hooks" "$ROOT/.claude"
cp "$SOURCE" "$HOOK"
cp "$SOURCE_CORE" "$ROOT/.trae/hooks/story_hook_core.js"
cp -R "$CLAUDE_HOOKS_SOURCE" "$ROOT/.claude/hooks"

run_hook() {
  local event="$1" payload="$2"
  (cd "$ROOT" && printf '%s' "$payload" | TRAE_PROJECT_DIR="$ROOT" node "$HOOK" "$event")
}

assert_empty() {
  [ -z "$1" ] || fail "$2 expected empty stdout, got: $1"
}

assert_contract() {
  local output="$1" event="$2" label="$3"
  printf '%s' "$output" | python3 -c '
import json, sys
obj = json.loads(sys.stdin.buffer.read().decode("utf-8"))
assert set(obj) == {"hookSpecificOutput"}, obj
specific = obj["hookSpecificOutput"]
allowed = {"hookEventName", "additionalContext"}
if sys.argv[1] == "PreToolUse":
    allowed |= {"permissionDecision", "permissionDecisionReason", "updatedInput"}
assert set(specific) <= allowed, specific
assert specific["hookEventName"] == sys.argv[1], specific
' "$event" || fail "$label violates strict TRAE output contract: $output"
}

assert_denied() {
  assert_contract "$1" PreToolUse "$2"
  printf '%s' "$1" | python3 -c 'import json,sys; x=json.load(sys.stdin)["hookSpecificOutput"]; assert x["permissionDecision"]=="deny" and x["permissionDecisionReason"]' \
    || fail "$2 did not deny"
}

write_clean_state() {
  mkdir -p "$1/追踪"
  printf '{"schema_version":5,"state_revision":0,"last_committed_chapter":%s}\n' "${2:-0}" > "$1/追踪/_tracking-state.json"
  printf '%s\n' '> 状态修订：0' > "$1/追踪/上下文.md"
}

echo "TRAE hook synthetic tests"
echo "=========================="
echo "Fixture: $ROOT"

# TRAE 官方配置必须由 version=1 + hooks 包裹。兼容迁移旧部署曾使用的
# 顶层事件直挂格式，同时不能吞掉用户 Hook、扩展字段或错误版本。
cat > "$TMP_DIR/trae-direct-map.json" <<'JSON'
{
  "version": 1,
  "customTop": {"keep": true},
  "SessionStart": [
    {"hooks": [
      {"type": "command", "command": "node .trae/hooks/story_trae_hook.js old-session", "timeout": 7},
      {"type": "command", "command": "bash ./user-session.sh", "timeout": 8}
    ]}
  ],
  "PreToolUse": [
    {"matcher": "Write", "hooks": [
      {"type": "command", "command": "node ${TRAE_PROJECT_DIR}/.trae/hooks/story_trae_hook.js old-guard", "timeout": 9},
      {"type": "command", "command": "bash ./user-guard.sh", "timeout": 10}
    ]}
  ]
}
JSON
python3 "$MERGE_HOOKS" \
  --existing "$TMP_DIR/trae-direct-map.json" \
  --template "$HOOKS_TEMPLATE" \
  --output "$TMP_DIR/trae-v1.json"
python3 - "$TMP_DIR/trae-v1.json" "$HOOKS_TEMPLATE" <<'PY' || fail "TRAE direct-map migration failed"
import json, sys
from collections import Counter

merged, template = [json.load(open(path, encoding="utf-8")) for path in sys.argv[1:]]
assert merged["version"] == 1 and isinstance(merged["hooks"], dict)
assert "SessionStart" not in merged and "PreToolUse" not in merged
assert merged["customTop"] == {"keep": True}

def commands(doc):
    result=[]
    for groups in doc["hooks"].values():
        for group in groups:
            if not isinstance(group, dict):
                continue
            candidates=group.get("hooks", []) if isinstance(group.get("hooks"), list) else [group]
            result.extend(hook.get("command", "") for hook in candidates if isinstance(hook, dict))
    return result

values=commands(merged)
assert "bash ./user-session.sh" in values and "bash ./user-guard.sh" in values
assert not any("old-session" in value or "old-guard" in value for value in values)
expected=[value for value in commands(template) if ".trae/hooks/story_trae_hook.js" in value]
counts=Counter(values)
assert expected and all(counts[value] == 1 for value in expected), counts
PY
python3 "$MERGE_HOOKS" \
  --existing "$TMP_DIR/trae-v1.json" \
  --template "$HOOKS_TEMPLATE" \
  --output "$TMP_DIR/trae-v1-again.json"
cmp -s "$TMP_DIR/trae-v1.json" "$TMP_DIR/trae-v1-again.json" \
  || fail "TRAE schema-v1 merge is not byte-idempotent"

cat > "$TMP_DIR/trae-direct-no-version.json" <<'JSON'
{"Notification":[{"hooks":[{"type":"command","command":"bash ./notify.sh","timeout":5}]}]}
JSON
python3 "$MERGE_HOOKS" \
  --existing "$TMP_DIR/trae-direct-no-version.json" \
  --template "$DISABLED_HOOKS_TEMPLATE" \
  --output "$TMP_DIR/trae-direct-no-version-v1.json"
python3 - "$TMP_DIR/trae-direct-no-version-v1.json" <<'PY' || fail "unversioned direct-map migration failed"
import json, sys
doc=json.load(open(sys.argv[1], encoding="utf-8"))
assert set(doc) == {"version", "hooks"} and doc["version"] == 1
assert doc["hooks"]["Notification"][0]["hooks"][0]["command"] == "bash ./notify.sh"
PY

assert_merge_rejected() {
  local input="$1" label="$2" output="$TMP_DIR/rejected-output.json"
  rm -f "$output"
  if python3 "$MERGE_HOOKS" --existing "$input" --template "$HOOKS_TEMPLATE" --output "$output" \
    >"$TMP_DIR/rejected.stdout" 2>"$TMP_DIR/rejected.stderr"; then
    fail "$label unexpectedly succeeded"
  fi
  [ ! -e "$output" ] || fail "$label wrote output despite rejection"
  grep -q '^ERROR:' "$TMP_DIR/rejected.stderr" || fail "$label did not report a merge error"
}
printf '[]\n' > "$TMP_DIR/trae-malformed-root.json"
assert_merge_rejected "$TMP_DIR/trae-malformed-root.json" "malformed TRAE root"
printf '%s\n' '{"version":2,"hooks":{}}' > "$TMP_DIR/trae-wrong-version.json"
assert_merge_rejected "$TMP_DIR/trae-wrong-version.json" "wrong TRAE schema version"
printf '%s\n' '{"hooks":{}}' > "$TMP_DIR/trae-missing-version.json"
assert_merge_rejected "$TMP_DIR/trae-missing-version.json" "wrapped TRAE schema without version"
printf '%s\n' '{"version":1,"PreToolUse":{}}' > "$TMP_DIR/trae-malformed-direct-event.json"
assert_merge_rejected "$TMP_DIR/trae-malformed-direct-event.json" "malformed legacy TRAE event"
printf '%s\n' '{"version":1,"SubagentStop":[]}' > "$TMP_DIR/trae-non-native-direct-event.json"
assert_merge_rejected "$TMP_DIR/trae-non-native-direct-event.json" "non-TRAE direct event"
printf '%s\n' '{"version":1,"hooks":{"SubagentStop":[]}}' > "$TMP_DIR/trae-non-native-wrapped-event.json"
assert_merge_rejected "$TMP_DIR/trae-non-native-wrapped-event.json" "non-TRAE wrapped event"
printf '%s\n' '{"version":1,"hooks":{"FutureMadeUpEvent":[]}}' > "$TMP_DIR/trae-unknown-wrapped-event.json"
assert_merge_rejected "$TMP_DIR/trae-unknown-wrapped-event.json" "unknown wrapped TRAE event"
printf '%s\n' '{"version":1,"FutureMadeUpEvent":[]}' > "$TMP_DIR/trae-ambiguous-direct-event.json"
assert_merge_rejected "$TMP_DIR/trae-ambiguous-direct-event.json" "ambiguous unknown direct event"
printf '%s\n' '{"version":1,"hooks":{"PreToolUse":["oops"]}}' > "$TMP_DIR/trae-non-object-group.json"
assert_merge_rejected "$TMP_DIR/trae-non-object-group.json" "non-object TRAE hook group"
printf '%s\n' '{"version":1,"hooks":{"PreToolUse":[{"process":"node","args":["hook.js"],"timeoutMs":1000}]}}' > "$TMP_DIR/trae-zcode-group.json"
assert_merge_rejected "$TMP_DIR/trae-zcode-group.json" "ZCode process/args hook group"
printf '%s\n' '{"version":1,"hooks":{"PreToolUse":[{"bogus":true,"hooks":[{"command":"true"}]}]}}' > "$TMP_DIR/trae-bogus-group.json"
assert_merge_rejected "$TMP_DIR/trae-bogus-group.json" "TRAE hook group with bogus key"
printf '%s\n' '{"version":1,"hooks":{"PreToolUse":[{"hooks":[{"command":" ","timeout":0,"bogus":true}]}]}}' > "$TMP_DIR/trae-bogus-hook.json"
assert_merge_rejected "$TMP_DIR/trae-bogus-hook.json" "malformed TRAE executable hook"
printf '%s\n' '{"version":1,"hooks":{"PreToolUse":[{"loop_limit":2,"hooks":[{"command":"true"}]}]}}' > "$TMP_DIR/trae-loop-limit-wrong-event.json"
assert_merge_rejected "$TMP_DIR/trae-loop-limit-wrong-event.json" "loop_limit outside Stop"
echo "  OK schema v1 hook merge + direct-map migration + fail-closed malformed inputs"

# Claude Code 注册保持原生直接 `bash ...` 形式，不在 JSON command
# 里嵌 POSIX `if [ ... ]`：TraeCode 在 Windows 使用 PowerShell 解析 command，
# 这种 inline shell 条件会先语法失败，根本进不到去重守卫。去重统一在
# 各入口 source 的 lib/common.sh 执行。
python3 - "$CLAUDE_SETTINGS_SOURCE" "$CLAUDE_HOOKS_SOURCE" <<'PY' || fail "Claude hook de-dup registration contract failed"
import json
from pathlib import Path
import re
import sys

settings = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
hooks_dir = Path(sys.argv[2])
expected = {
    "session-start.sh", "session-end.sh", "detect-story-gaps.sh",
    "pre-compact.sh", "post-compact.sh", "validate-story-commit.sh",
    "guard-outline-before-prose.sh", "check-prose-after-write.sh",
}
commands = []
for groups in settings["hooks"].values():
    for group in groups:
        commands.extend(hook["command"] for hook in group["hooks"])
assert len(commands) == len(expected), commands
assert all("TRAE_PROJECT_DIR" not in command and "if [" not in command for command in commands), commands
registered = set()
for command in commands:
    match = re.fullmatch(r'bash "\$CLAUDE_PROJECT_DIR"/\.claude/hooks/([A-Za-z0-9-]+\.sh)', command)
    assert match, command
    registered.add(match.group(1))
assert registered == expected, (registered, expected)
for name in expected:
    text = (hooks_dir / name).read_text(encoding="utf-8")
    assert re.search(r'^source .*lib/common\.sh', text, re.M), name
PY
echo "  OK portable Claude registration + common.sh de-dup ownership"

mkdir -p "$ROOT/book/正文" "$ROOT/book/大纲" "$ROOT/book/设定"
out="$(run_hook pre-tool-prose-guard '{"hook_event_name":"PreToolUse","tool_name":"Write","tool_input":{"file_path":"book/正文/第001章_开端.md"}}')"
assert_denied "$out" "long prose without outline"

cat > "$ROOT/.story-deployed" <<'EOF'
agents_version: 39
target_cli: generic
EOF
out="$(run_hook pre-tool-prose-guard '{"hook_event_name":"PreToolUse","tool_name":"Write","tool_input":{"file_path":"book/正文/第001章_开端.md"}}')"
assert_empty "$out" "stale TRAE PreToolUse registration after target removal"
out="$(run_hook session-start '{"hook_event_name":"SessionStart","source":"startup"}')"
assert_empty "$out" "stale TRAE SessionStart registration after target removal"
rm "$ROOT/.story-deployed"
echo "  OK stale TRAE registrations yield to target_cli removal"

# TraeCode 会合并所有已启用的 `.trae/hooks.json` 与 `.claude/settings*.json`。
# 同一项目多端部署时，导入的 Claude 入口必须在 TRAE_PROJECT_DIR
# 下静默让路，原生 TRAE runner 则仍然 deny，否则 Write/Edit 会执行两遍。
dual_payload='{"hook_event_name":"PreToolUse","tool_name":"Write","tool_input":{"file_path":"book/正文/第001章_开端.md"}}'
if ! claude_out="$(cd "$ROOT" && printf '%s' "$dual_payload" \
  | TRAE_PROJECT_DIR="$ROOT" CLAUDE_PROJECT_DIR="$ROOT" \
    bash "$ROOT/.claude/hooks/guard-outline-before-prose.sh" 2>&1)"; then
  fail "imported Claude prose guard did not exit 0 inside TraeCode"
fi
assert_empty "$claude_out" "imported Claude prose guard inside TraeCode"
native_out="$(run_hook pre-tool-prose-guard "$dual_payload")"
assert_denied "$native_out" "native TRAE guard after Claude-hook de-dup"
echo "  OK .trae/.claude merged-hook de-dup"

: > "$ROOT/book/大纲/细纲_第1章.md"
out="$(run_hook pre-tool-prose-guard '{"tool_name":"Write","tool_input":{"file_path":"book/正文/第001章_开端.md"}}')"
assert_denied "$out" "long prose without tracking metadata"
printf '%s' "$out" | grep -q '_tracking-state.json 缺失' || fail "missing tracking denial did not explain re-import/init: $out"
write_clean_state "$ROOT/book"
out="$(run_hook pre-tool-prose-guard '{"tool_name":"Write","tool_input":{"file_path":"book/正文/第001章_开端.md"}}')"
assert_empty "$out" "long prose with outline"

# 新书还没有大纲/追踪/设定脚手架时也必须 fail closed；相对目标按 hook cwd 解析，
# 不能为了掩盖错误的项目根拼接而把核心守卫削成 fail open。
mkdir -p "$ROOT/bare/正文" "$ROOT/cwd-book/正文" "$ROOT/cwd-book/大纲"
out="$(run_hook pre-tool-prose-guard '{"tool_name":"Write","tool_input":{"file_path":"bare/正文/第1章_首章.md"}}')"
assert_denied "$out" "bare long project without scaffolding"
relative_payload="$(node -e '
const path = require("path")
process.stdout.write(JSON.stringify({
  cwd: path.resolve(process.argv[1]),
  tool_name: "Write",
  tool_input: { file_path: "正文/第8章_相对.md" },
}))
' "$ROOT/cwd-book")"
out="$(run_hook pre-tool-prose-guard "$relative_payload")"
assert_denied "$out" "relative prose target from hook cwd"
printf '%s' "$out" | grep -q 'cwd-book/大纲' || fail "relative target was not resolved from hook cwd: $out"
: > "$ROOT/cwd-book/大纲/细纲_第8章.md"
out="$(run_hook pre-tool-prose-guard "$relative_payload")"
assert_denied "$out" "relative prose target without tracking metadata"
write_clean_state "$ROOT/cwd-book" 7
out="$(run_hook pre-tool-prose-guard "$relative_payload")"
assert_empty "$out" "relative prose target with cwd-local outline"

: > "$ROOT/book/正文/第009章_已存在.md"
printf '%s\n' '{"schema_version":5,"state_revision":1,"last_committed_chapter":0}' > "$ROOT/book/追踪/_tracking-state.json"
out="$(run_hook pre-tool-prose-guard '{"tool_name":"Write","tool_input":{"file_path":"book/正文/第009章_已存在.md"}}')"
assert_denied "$out" "existing prose rewrite with mismatched derived state"
printf '%s' "$out" | grep -q 'mode=revision 事务重建派生视图' || fail "state mismatch denial missed retry action: $out"
write_clean_state "$ROOT/book"

# containment 判据必须按 Windows 路径语义覆盖：path.relative 跨盘会返回绝对路径，
# 目录名恰好以 `..` 开头则仍在项目内。只用 startsWith("..") 会把两者同时判反。
node - "$SOURCE" <<'JS' || fail "TRAE cwd containment is not cross-volume safe"
const path = require("path")
const { isPathInside } = require(process.argv[2])
if (isPathInside("C:\\repo", "D:\\elsewhere", path.win32)) {
  throw new Error("different Windows volume must be outside the project")
}
if (!isPathInside("C:\\repo", "C:\\repo\\..draft", path.win32)) {
  throw new Error("an in-project directory named ..draft must remain inside")
}
if (!isPathInside("C:\\repo", "C:\\repo\\sub", path.win32)) {
  throw new Error("ordinary in-project directory must remain inside")
}
JS

# TRAE 把 macOS/Linux 和 Windows 的命令统一交给 RunCommand，runner 必须
# 同时具备 POSIX 与静态 PowerShell 目标抽取。这里直接锁定导出函数，
# 并明确把变量表达式、splatting 和 .NET API 留在硬拦范围之外。
node - "$SOURCE" <<'JS' || fail "TRAE static PowerShell target extraction failed"
const hook = require(process.argv[2])
if (typeof hook.extractUnifiedPowerShellTargets !== "function") {
  throw new Error("extractUnifiedPowerShellTargets is not exported")
}
const fixtures = new Map([
  [String.raw`Set-Content -Path "book\正文\第020章.md" -Value x`, [String.raw`book\正文\第020章.md`]],
  ['Add-Content -LiteralPath "book/正文/第021章.md" -Value x', ['book/正文/第021章.md']],
  ['"正文" | Out-File -FilePath "book/正文/第022章.md"', ['book/正文/第022章.md']],
  ['Copy-Item draft.md -Destination "book/正文/第023章.md"', ['book/正文/第023章.md']],
  ['Move-Item -Path draft.md -Destination "book/正文/"', ['book/正文/draft.md']],
  ['New-Item -Path "book/正文" -Name "第024章.md" -ItemType File', ['book/正文/第024章.md']],
  ['Set-Content -ErrorAction Stop "book/正文/第028章.md" x', ['book/正文/第028章.md']],
  ['Get-Content "book/正文/第020章.md"', []],
  ['Set-Content -Path "book/正文/第029章.md" -Value x -WhatIf', []],
  ['Set-Content -Path "$root/book/正文/第025章.md" -Value x', []],
  ['Set-Content @storyTarget', []],
  ['& $writer "book/正文/第026章.md"', []],
  ['[System.IO.File]::WriteAllText("book/正文/第027章.md", "x")', []],
])
for (const [command, expected] of fixtures) {
  const actual = hook.extractUnifiedPowerShellTargets(command)
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${command}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`)
  }
}
JS
echo "  OK static PowerShell export covers Chinese writes and rejects dynamic forms"

out="$(run_hook pre-tool-prose-guard '{"tool_name":"Edit","tool_input":{"file_path":"book/正文/第002章_新局.md","old_string":"旧","new_string":"正文"}}')"
assert_denied "$out" "Edit prose without outline"
out="$(run_hook pre-tool-prose-guard '{"tool_name":"RunCommand","tool_input":{"command":"echo x | tee book/正文/第003章_命令.md"}}')"
assert_denied "$out" "RunCommand prose write without outline"
out="$(run_hook pre-tool-prose-guard '{"tool_name":"RunCommand","tool_input":{"command":"grep -n book/正文/第003章_命令.md notes.md"}}')"
assert_empty "$out" "RunCommand mention without write"

powershell_commands=(
  'Set-Content -Path "book/正文/第020章_PowerShell.md" -Value x'
  'Add-Content -LiteralPath "book/正文/第020章_PowerShell.md" -Value x'
  '"x" | Out-File -FilePath "book/正文/第020章_PowerShell.md"'
  'Copy-Item draft.md -Destination "book/正文/第020章_PowerShell.md"'
  'Move-Item -Path draft.md -Destination "book/正文/第020章_PowerShell.md"'
  'New-Item -Path "book/正文" -Name "第020章_PowerShell.md" -ItemType File'
)
for command in "${powershell_commands[@]}"; do
  payload="$(node -e 'process.stdout.write(JSON.stringify({tool_name:"RunCommand",tool_input:{command:process.argv[1]}}))' "$command")"
  out="$(run_hook pre-tool-prose-guard "$payload")"
  assert_denied "$out" "RunCommand static PowerShell prose write without outline: $command"
done
echo "  OK RunCommand PreToolUse covers six static PowerShell write families"

mkdir -p "$ROOT/short"
: > "$ROOT/short/设定.md"
out="$(run_hook pre-tool-prose-guard '{"tool_name":"Write","tool_input":{"file_path":"short/正文.md"}}')"
assert_denied "$out" "short prose without outline"
for command in \
  'Copy-Item "正文.md" -Destination "short"' \
  'Move-Item -Path "正文.md" -Destination "short"'; do
  payload="$(node -e 'process.stdout.write(JSON.stringify({tool_name:"RunCommand",tool_input:{command:process.argv[1]}}))' "$command")"
  out="$(run_hook pre-tool-prose-guard "$payload")"
  assert_denied "$out" "RunCommand PowerShell existing short-directory target: $command"
done
: > "$ROOT/short/小节大纲.md"
out="$(run_hook pre-tool-prose-guard '{"tool_name":"Write","tool_input":{"file_path":"short/正文.md"}}')"
assert_empty "$out" "short prose with outline"

mkdir -p "$ROOT/impbook/正文" "$ROOT/拆文库/impbook"
out="$(run_hook pre-tool-prose-guard '{"tool_name":"Write","tool_input":{"file_path":"impbook/正文/第1章_导入.md"}}')"
assert_empty "$out" "story-import long migration"
mkdir -p "$ROOT/impbook/大纲" "$ROOT/impbook/追踪"
: > "$ROOT/impbook/大纲/细纲_第2章.md"
printf '%s\n' '{"schema_version":5,"state_revision":1,"last_committed_chapter":1}' > "$ROOT/impbook/追踪/_tracking-state.json"
printf '%s\n' '> 状态修订：0' > "$ROOT/impbook/追踪/上下文.md"
out="$(run_hook pre-tool-prose-guard '{"tool_name":"Write","tool_input":{"file_path":"impbook/正文/第2章_导入后续.md"}}')"
assert_denied "$out" "imported project must not permanently bypass invalid tracking guard"
echo "  OK outline-before-prose guard"

printf '这是正文里的 TODO，他想 this should never happen again，而且最后一句被截断' > "$ROOT/short/正文.md"
out="$(run_hook post-tool-prose-check '{"hook_event_name":"PostToolUse","tool_name":"Write","tool_input":{"file_path":"short/正文.md"}}')"
assert_contract "$out" PostToolUse "post-write prose check"
printf '%s' "$out" | grep -q '占位符' || fail "post-write check missed TODO"
printf '%s' "$out" | grep -q '疑似截断' || fail "post-write check missed truncation"
printf '%s' "$out" | grep -q '连续英文短语泄漏' || fail "post-write check missed English language drift"
echo "  OK post-write strict JSON + UTF-8 findings"

printf '命令写入的正文 TODO。' > "$ROOT/short/正文.md"
out="$(run_hook post-tool-prose-check '{"hook_event_name":"PostToolUse","tool_name":"RunCommand","tool_input":{"command":"cat input.txt > short/正文.md"}}')"
assert_contract "$out" PostToolUse "post-RunCommand prose check"
printf '%s' "$out" | grep -q '占位符' || fail "post-RunCommand check missed prose target"
echo "  OK RunCommand write post-check"

printf 'PowerShell 命令写入的正文 TODO。' > "$ROOT/short/正文.md"
ps_post_command='Copy-Item "正文.md" -Destination "short"'
ps_post_payload="$(node -e 'process.stdout.write(JSON.stringify({hook_event_name:"PostToolUse",tool_name:"RunCommand",tool_input:{command:process.argv[1]}}))' "$ps_post_command")"
out="$(run_hook post-tool-prose-check "$ps_post_payload")"
assert_contract "$out" PostToolUse "post-RunCommand static PowerShell prose check"
printf '%s' "$out" | grep -q '占位符' || fail "post-RunCommand PowerShell check missed prose target"
echo "  OK RunCommand PowerShell write post-check"

cat > "$ROOT/.story-deployed" <<'EOF'
agents_version: 19
setup_skill_version: 1.2.8
target_cli: trae
resolver_strategy: project-local-skill-reference
references_dir: .trae/skills/story-setup/references/agent-references
EOF
printf 'book\n' > "$ROOT/.active-book"
mkdir -p "$ROOT/book/追踪"
printf '# 上下文\n' > "$ROOT/book/追踪/上下文.md"
out="$(run_hook session-start '{"hook_event_name":"SessionStart","source":"startup"}')"
assert_contract "$out" SessionStart "session start"
printf '%s' "$out" | grep -q '当前书目' || fail "session start missed active book"
printf '%s\n' '{"schema_version":5,"state_revision":1,"last_committed_chapter":0}' > "$ROOT/book/追踪/_tracking-state.json"
out="$(run_hook session-start '{"hook_event_name":"SessionStart","source":"startup"}')"
assert_contract "$out" SessionStart "session tracking mismatch warning"
printf '%s' "$out" | grep -q '状态修订' || fail "session start missed tracking revision mismatch"
write_clean_state "$ROOT/book"
echo "  OK session-start context"

mkdir -p \
  "$ROOT/备份/2026-01-01_旧稿/正文" \
  "$ROOT/归档_旧追踪/正文" \
  "$ROOT/archives/snapshot/正文"
printf '# 旧章\n' > "$ROOT/备份/2026-01-01_旧稿/正文/第1章.md"
printf '# 旧章\n' > "$ROOT/归档_旧追踪/正文/第1章.md"
printf '# 旧章\n' > "$ROOT/archives/snapshot/正文/第1章.md"
printf '备份/2026-01-01_旧稿\n' > "$ROOT/.active-book"
out="$(run_hook session-start '{"hook_event_name":"SessionStart","source":"startup"}')"
assert_contract "$out" SessionStart "historical-copy discovery filter"
printf '%s' "$out" | grep -q '当前书目：book' || fail "active-book fallback did not skip declared backup: $out"
if printf '%s' "$out" | grep -Eq '备份/|归档_|archives/'; then
  fail "session start treated a backup/archive tree as a live book: $out"
fi
printf 'book\n' > "$ROOT/.active-book"
echo "  OK active-book and all-book discovery skip 备份/归档/archive(s) trees"

printf '# 旧上下文\n' > "$ROOT/book/追踪/上下文.md"
sleep 2
printf '# 第1章\n正文。\n' > "$ROOT/book/正文/第001章_撞名.md"
printf '# 第2章\n正文。\n' > "$ROOT/book/正文/第002章_撞名.md"
out="$(run_hook session-start '{"hook_event_name":"SessionStart","source":"startup"}')"
assert_contract "$out" SessionStart "session continuity"
printf '%s' "$out" | grep -q '续写状态卡更早' || fail "session start missed stale tracking context"
printf '%s' "$out" | grep -q '标题重复' || fail "session start missed duplicate chapter title"
echo "  OK session-start continuity guard"

git -C "$ROOT" init -q
git -C "$ROOT" config user.email trae-hook@example.invalid
git -C "$ROOT" config user.name trae-hook-test
printf '年龄：18\n' > "$ROOT/book/正文/第010章_属性.md"
git -C "$ROOT" add "$ROOT/book/正文/第010章_属性.md"
out="$(run_hook pre-tool-commit-advisory '{"tool_name":"RunCommand","tool_input":{"command":"git -C . commit -m test"}}')"
assert_contract "$out" PreToolUse "commit advisory"
printf '%s' "$out" | grep -q '硬编码角色属性' || fail "commit advisory missed staged prose"
out="$(run_hook pre-tool-commit-advisory '{"tool_name":"RunCommand","tool_input":{"command":"echo git commit docs"}}')"
assert_empty "$out" "non-commit command"
echo "  OK commit advisory"

: > "$TMP_DIR/trae-malformed-input.stderr"
for malformed_payload in 'not-json' '[]'; do
  out="$(printf '%s' "$malformed_payload" | TRAE_PROJECT_DIR="$ROOT" node "$HOOK" pre-tool-prose-guard 2>> "$TMP_DIR/trae-malformed-input.stderr")"
  assert_denied "$out" "malformed PreToolUse input must fail closed"
done
rg -q 'oh-story trae hook' "$TMP_DIR/trae-malformed-input.stderr" || fail "malformed PreToolUse input lacks stderr diagnostics"

NO_PROJECT="$TMP_DIR/no-project"
mkdir -p "$NO_PROJECT"
out="$(cd "$NO_PROJECT" && printf '%s' '{"hook_event_name":"SessionStart","source":"startup"}' | env -u TRAE_PROJECT_DIR -u CLAUDE_PROJECT_DIR node "$SOURCE" session-start)"
assert_empty "$out" "session start outside an oh-story project"

: > "$ROOT/book/大纲/细纲_第8章.md"
write_clean_state "$ROOT/book" 7
out="$(cd "$TMP_DIR" && printf '%s' '{"tool_name":"Write","tool_input":{"file_path":"book/正文/第8章_自定位.md"}}' | env -u TRAE_PROJECT_DIR -u CLAUDE_PROJECT_DIR node "$HOOK" pre-tool-prose-guard)"
assert_empty "$out" "deployed __dirname self-location"
echo "  OK malformed input + no-project no-op + workspace self-location"

# 所有 Claude hook 入口都 source 同一 common.sh。这里锁定完整入口集，
# 防止未来新增/重构某个脚本时绕过 TRAE_PROJECT_DIR 去重。
for script in \
  session-start.sh session-end.sh detect-story-gaps.sh pre-compact.sh post-compact.sh \
  validate-story-commit.sh guard-outline-before-prose.sh check-prose-after-write.sh; do
  if ! claude_out="$(cd "$ROOT" && printf '%s' "$dual_payload" \
    | TRAE_PROJECT_DIR="$ROOT" CLAUDE_PROJECT_DIR="$ROOT" STORY_SESSION_LOG=1 \
      STORY_COMMIT_COMMAND='git commit -m trae-dedup' \
      bash "$ROOT/.claude/hooks/$script" 2>&1)"; then
    fail "imported Claude hook $script did not exit 0 inside TraeCode"
  fi
  assert_empty "$claude_out" "imported Claude hook $script inside TraeCode"
done
echo "  OK every imported Claude hook entrypoint yields to native TRAE hooks"

echo ""
echo "OK: TRAE hook synthetic tests passed"
