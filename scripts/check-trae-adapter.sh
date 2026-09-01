#!/usr/bin/env bash
# Static contract checks for the TRAE Code native adapter.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

fail() { echo "FAIL: $*" >&2; exit 1; }
assert_file() { [ -f "$1" ] || fail "missing file: $1"; }

ROOT="skills/story-setup/references/trae"
HOOK="$ROOT/hooks/story_trae_hook.js"
HOOK_CORE="$ROOT/hooks/story_hook_core.js"
HOOKS_JSON="$ROOT/hooks/hooks.json"
DISABLED_HOOKS_JSON="$ROOT/hooks/disabled-hooks.json"
LEGACY_CORE_HASHES="$ROOT/legacy-managed-sha256.json"
ACTIVATION_DOC="$ROOT/runtime-activation.md"
OWNERSHIP_HELPER="skills/story-setup/scripts/trae-core-ownership.py"
HOOKS_MERGER="skills/story-setup/scripts/merge-trae-hooks.py"

echo "TRAE Code adapter check"
echo "======================="
echo "Repo: $REPO_ROOT"

for file in \
  "$ROOT/AGENTS.md.tmpl" \
  "$ROOT/rules/oh-story.md" \
  "$HOOKS_JSON" \
  "$DISABLED_HOOKS_JSON" \
  "$HOOK" \
  "$HOOK_CORE" \
  "$LEGACY_CORE_HASHES" \
  "$ACTIVATION_DOC" \
  "$OWNERSHIP_HELPER" \
  "$HOOKS_MERGER"; do
  assert_file "$file"
done

python3 -m json.tool "$HOOKS_JSON" >/dev/null
python3 -m json.tool "$DISABLED_HOOKS_JSON" >/dev/null
node --check "$HOOK"
node --check "$HOOK_CORE"
python3 -c 'from pathlib import Path; compile(Path("skills/story-setup/scripts/trae-core-ownership.py").read_text(encoding="utf-8"), "trae-core-ownership.py", "exec")'
python3 -c 'from pathlib import Path; compile(Path("skills/story-setup/scripts/merge-trae-hooks.py").read_text(encoding="utf-8"), "merge-trae-hooks.py", "exec")'
cmp -s "$HOOK_CORE" skills/story-setup/references/zcode/hooks/story_hook_core.js \
  || fail "TRAE and ZCode shared hook cores drifted"
python3 - <<'PY'
import hashlib
import json
import re
from pathlib import Path

core = Path('skills/story-setup/references/trae/hooks/story_hook_core.js').read_bytes()
assert b'oh-story-managed: shared-hook-core' in core

registry_path = Path('skills/story-setup/references/trae/legacy-managed-sha256.json')
registry = json.loads(registry_path.read_text(encoding='utf-8'))
assert set(registry) == {'story_hook_core.js'}
hashes = registry['story_hook_core.js']
assert isinstance(hashes, list) and hashes
assert len(hashes) == len(set(hashes))
assert all(re.fullmatch(r'[0-9a-f]{64}', value) for value in hashes)

# v37 及更早的共享核没有 marker。新版首次升级时必须能以历史
# SHA-256 证明它是 oh-story 资产；否则“只认新 marker”会让现存用户永久无法升级。
legacy_current = core.replace(b'// oh-story-managed: shared-hook-core\n\n', b'', 1)
assert hashlib.sha256(legacy_current).hexdigest() in hashes
assert hashlib.sha256(legacy_current.replace(b'\n', b'\r\n')).hexdigest() in hashes
assert hashlib.sha256(b'user-owned unrelated core\n').hexdigest() not in hashes
PY
echo "  OK JSON/JavaScript syntax + shared core parity/ownership registry"

python3 - <<'PY'
import re
from pathlib import Path

skills = sorted(
    path for path in Path('skills').glob('*/SKILL.md')
    if path.parent.name == 'browser-cdp' or path.parent.name.startswith('story')
)
commands = sorted(Path('skills/story-setup/references/trae/commands').glob('*.md'))
canonical = {
    'browser-cdp', 'story', 'story-cover', 'story-data-analyze', 'story-deslop',
    'story-explore', 'story-import', 'story-long-analyze', 'story-long-scan',
    'story-long-write', 'story-publish', 'story-release-package', 'story-research',
    'story-review', 'story-setup', 'story-short-analyze', 'story-short-scan',
    'story-short-write',
}
assert skills, 'no source skills discovered'
assert {p.parent.name for p in skills} == canonical, (
    sorted(canonical - {p.parent.name for p in skills}),
    sorted({p.parent.name for p in skills} - canonical),
)
assert len(commands) == len(skills), (len(commands), len(skills))
expected = {p.parent.name for p in skills}
assert {p.stem for p in commands} == expected

for command in commands:
    text = command.read_text(encoding='utf-8')
    assert text.startswith('---\n'), command
    front, body = text.split('---', 2)[1:]
    keys = {line.split(':', 1)[0] for line in front.splitlines() if ':' in line}
    assert keys == {'name', 'description'}, (command, keys)
    name = re.search(r'^name:\s*(\S+)\s*$', front, re.M)
    desc = re.search(r'^description:\s*(.+)$', front, re.M)
    assert name and name.group(1) == command.stem, command
    assert desc and desc.group(1).strip(), command
    assert f'<!-- oh-story-managed: command/{command.stem} -->' in body, command
    assert f'使用 {command.stem} skill' in body, command
PY
echo "  OK native Commands match the canonical 18-Skill Chinese package one-to-one"

python3 - <<'PY'
import re
from pathlib import Path

agents = sorted(Path('skills/story-setup/references/trae/agents').glob('*.md'))
expected_agents = {
    'chapter-extractor', 'character-designer', 'consistency-checker',
    'narrative-writer', 'revision-governor', 'story-architect',
    'story-explorer', 'story-researcher',
}
assert {path.stem for path in agents} == expected_agents, [path.stem for path in agents]
allowed_keys = {'name', 'description', 'tools', 'disallowedTools'}
allowed_tools = {'Bash', 'Edit', 'Glob', 'Grep', 'Read', 'Skill', 'TodoWrite', 'WebFetch', 'WebSearch', 'Write', 'LSP'}
readonly = {'chapter-extractor', 'consistency-checker', 'revision-governor', 'story-explorer'}

for agent in agents:
    text = agent.read_text(encoding='utf-8')
    assert text.startswith('---\n'), agent
    front, body = text.split('---', 2)[1:]
    keys = {line.split(':', 1)[0] for line in front.splitlines() if ':' in line and not line.startswith(' ')}
    assert keys <= allowed_keys, (agent, keys - allowed_keys)
    assert {'name', 'description', 'tools'} <= keys, (agent, keys)
    name = re.search(r'^name:\s*(\S+)\s*$', front, re.M)
    assert name and name.group(1) == agent.stem, agent
    assert re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{0,48}[A-Za-z0-9]|[A-Za-z]', agent.stem), agent
    assert f'<!-- oh-story-managed: agent/{agent.stem} -->' in body, agent
    tools_line = re.search(r'^tools:\s*(.+)$', front, re.M)
    assert tools_line and '[' not in tools_line.group(1), agent
    tools = {item.strip() for item in tools_line.group(1).split(',') if item.strip()}
    assert tools <= allowed_tools and tools, (agent, tools)
    disallowed_line = re.search(r'^disallowedTools:\s*(.+)$', front, re.M)
    if disallowed_line:
        assert '[' not in disallowed_line.group(1), agent
        disallowed = {item.strip() for item in disallowed_line.group(1).split(',') if item.strip()}
        assert disallowed <= allowed_tools, (agent, disallowed)
        assert not tools & disallowed, (agent, tools & disallowed)
    if agent.stem in readonly:
        assert tools == {'Read', 'Glob', 'Grep'}, (agent, tools)
        assert disallowed_line, agent
    assert '.claude/skills' not in body and 'Agent(subagent_type' not in body, agent
    assert not re.search(r'内置\s*`?Agent`?\s*工具', body), agent
PY
echo "  OK 8 native subagents (TRAE frontmatter, tools, paths and managed markers)"

python3 - <<'PY'
from pathlib import Path

base = {path.stem for path in Path('skills/story-setup/references/trae/agents').glob('*.md')}
data = {path.stem for path in Path('skills/story-data-analyze/agents/trae').glob('*.md')}
expected_base = {
    'chapter-extractor', 'character-designer', 'consistency-checker',
    'narrative-writer', 'revision-governor', 'story-architect',
    'story-explorer', 'story-researcher',
}
expected_data = {
    'story-data-fetcher', 'story-data-method-validator',
    'story-data-metrics-analyst', 'story-data-supervisor',
    'story-data-text-improvement-planner',
}
assert base == expected_base, (sorted(expected_base - base), sorted(base - expected_base))
assert data == expected_data, (sorted(expected_data - data), sorted(data - expected_data))
assert not (base & data), base & data
assert len(base | data) == 13
PY
echo "  OK fixed TRAE physical roster: 8 general + 5 data = 13"

python3 - <<'PY'
import re
from pathlib import Path

root = Path('skills/story-setup/references/trae')
canonical = {
    'browser-cdp', 'story', 'story-cover', 'story-data-analyze', 'story-deslop',
    'story-explore', 'story-import', 'story-long-analyze', 'story-long-scan',
    'story-long-write', 'story-publish', 'story-release-package', 'story-research',
    'story-review', 'story-setup', 'story-short-analyze', 'story-short-scan',
    'story-short-write',
}
general = {
    'chapter-extractor', 'character-designer', 'consistency-checker',
    'narrative-writer', 'revision-governor', 'story-architect',
    'story-explorer', 'story-researcher',
}
data = {
    'story-data-fetcher', 'story-data-method-validator',
    'story-data-metrics-analyst', 'story-data-supervisor',
    'story-data-text-improvement-planner',
}
allowed = {
    Path('AGENTS.md.tmpl'), Path('runtime-activation.md'),
    Path('legacy-managed-sha256.json'), Path('rules/oh-story.md'),
    Path('hooks/hooks.json'), Path('hooks/disabled-hooks.json'),
    Path('hooks/story_trae_hook.js'), Path('hooks/story_hook_core.js'),
    *{Path('commands') / f'{name}.md' for name in canonical},
    *{Path('agents') / f'{name}.md' for name in general},
}
actual = {path.relative_to(root) for path in root.rglob('*') if path.is_file()}
assert actual == allowed, (
    f'missing={sorted(map(str, allowed - actual))}',
    f'extra={sorted(map(str, actual - allowed))}',
)
data_root = Path('skills/story-data-analyze/agents/trae')
assert {path.name for path in data_root.iterdir() if path.is_file()} == {
    f'{name}.md' for name in data
}
language_allowlist = {
    root / 'AGENTS.md.tmpl',
    root / 'agents/narrative-writer.md',
}
for path in [
    *(item for item in root.rglob('*') if item.is_file()),
    *(item for item in data_root.rglob('*') if item.is_file()),
]:
    text = path.read_text(encoding='utf-8')
    for pattern in (
        r'story_globalize_gate_core', r'loadGlobalizeGates',
        r'International[\\/]+(?:drafts|chapters)', r'project_extension_root',
        r'managed-skill-ownership', r'SubagentStop',
    ):
        assert not re.search(pattern, text, re.I), (path, pattern)
    if re.search(r'globalize', text, re.I):
        assert path in language_allowlist, path
PY
echo "  OK exact TRAE file inventory and Chinese-package boundary allowlist"

python3 - <<'PY'
import json
import re
from pathlib import Path

config = json.loads(Path('skills/story-setup/references/trae/hooks/hooks.json').read_text(encoding='utf-8'))
assert set(config) == {'version', 'hooks'}
assert config['version'] == 1
events = config['hooks']
supported = {'SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'Stop', 'Notification'}
assert set(events) == {'SessionStart', 'PreToolUse', 'PostToolUse'}
assert set(events) <= supported

expected_handlers = {
    'SessionStart': {'session-start'},
    'PreToolUse': {'pre-tool-prose-guard', 'pre-tool-commit-advisory'},
    'PostToolUse': {'post-tool-prose-check'},
}

for event, groups in events.items():
    seen = set()
    for group in groups:
        assert set(group) <= {'matcher', 'hooks'}, (event, group)
        if event == 'SessionStart':
            assert 'matcher' not in group, group
        else:
            matcher = group.get('matcher', '')
            assert matcher, (event, group)
            assert not re.search(r'Bash|ApplyPatch|MultiEdit', matcher), matcher
            assert set(matcher.split('|')) <= {'RunCommand', 'Write', 'Edit'}, matcher
        for hook in group['hooks']:
            assert set(hook) == {'type', 'command', 'timeout'}, (event, hook)
            assert hook['type'] == 'command'
            assert isinstance(hook['timeout'], int) and 1 <= hook['timeout'] <= 600
            match = re.fullmatch(r'node \.trae/hooks/story_trae_hook\.js ([a-z-]+)', hook['command'])
            assert match, hook['command']
            seen.add(match.group(1))
    assert seen == expected_handlers[event], (event, seen, expected_handlers[event])

pre = events['PreToolUse']
guard = next(group for group in pre if 'pre-tool-prose-guard' in group['hooks'][0]['command'])
assert guard['matcher'] == 'RunCommand|Write|Edit'
commit = next(group for group in pre if 'pre-tool-commit-advisory' in group['hooks'][0]['command'])
assert commit['matcher'] == 'RunCommand'
assert events['PostToolUse'][0]['matcher'] == 'RunCommand|Write|Edit'
PY
echo "  OK native hooks.json schema, events, handlers and tool matchers"

python3 - <<'PY'
from pathlib import Path

agents = Path('skills/story-setup/references/trae/AGENTS.md.tmpl').read_text(encoding='utf-8')
assert '<!-- BEGIN oh-story-managed: trae -->' in agents
assert '<!-- END oh-story-managed: trae -->' in agents
for path in ('.trae/skills/', '.trae/commands/', '.trae/agents/', '.trae/rules/', '.trae/hooks.json'):
    assert path in agents, path
for required in (
    '设置 > Hooks',
    '将 AGENTS.md 包含在上下文中',
    '启用 Subagents 目录',
    '内置 **Agent** 智能体',
    'TRAE_PROJECT_DIR',
    '静态兼容，运行时未验证',
    'runtime-activation.md',
    '斜杠输入命中的是 Command',
):
    assert required in agents, required
assert '内置 Agent 工具' not in agents
assert '同名 Skill 或 Command' not in agents

activation = Path('skills/story-setup/references/trae/runtime-activation.md').read_text(encoding='utf-8')
for required in (
    'TraeCode IDE',
    '.traecli/commands/',
    '设置 > Hooks',
    '设置 > 技能与命令',
    '.trae/skill-config.json',
    '设置 > Rules',
    '将 AGENTS.md 包含在上下文中',
    '启用 Subagents 目录',
    'TRAE_PROJECT_DIR',
    '静态兼容，运行时未验证',
    'https://docs.trae.cn/ide_hook-configuration-reference',
):
    assert required in activation, required

rule = Path('skills/story-setup/references/trae/rules/oh-story.md').read_text(encoding='utf-8')
front, body = rule.split('---', 2)[1:]
assert 'alwaysApply: false' in front
assert 'globs:' in front
assert '<!-- oh-story-managed: rule/oh-story -->' in body
PY
echo "  OK managed AGENTS/rules templates"

grep -q 'TRAE_PROJECT_DIR' "$HOOK" || fail "runner does not honor TRAE_PROJECT_DIR"
grep -q 'CLAUDE_PROJECT_DIR' "$HOOK" || fail "runner lost documented compatibility env"
grep -q 'target_cli' "$HOOK" || fail "session start does not read target_cli"
grep -q 'runtimeTargetEnabled' "$HOOK" || fail "runner does not yield after target_cli removes TRAE"
grep -q 'includes("trae")' "$HOOK" || fail "session start does not verify target_cli=trae"
grep -q 'runcommand' "$HOOK" || fail "runner does not parse RunCommand targets"
if grep -Eqs 'story_globalize_gate_core|loadGlobalizeGates|International[/\\].*(drafts|chapters)' "$HOOK" "$HOOKS_JSON"; then
  fail "Chinese TRAE adapter contains an overseas-only hook contract"
fi
if grep -RqsE 'PreCompact|PostCompact|SessionEnd|SubagentStop|PermissionRequest|PostToolUseFailure' "$ROOT/hooks"; then
  fail "TRAE adapter contains unsupported or unregistered hook events"
fi
echo "  OK runtime adapter boundaries"

python3 - "$DISABLED_HOOKS_JSON" <<'PY' || fail "TRAE disabled hook template must be empty"
import json, sys
assert json.load(open(sys.argv[1], encoding="utf-8")) == {"version": 1, "hooks": {}}
PY
echo "  OK disabled hook template for safe target removal"

echo ""
echo "OK: TRAE Code adapter checks passed"
