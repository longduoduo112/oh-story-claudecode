#!/usr/bin/env python3
"""Static contract checks for the WorkBuddy / CodeBuddy Code adapter."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
WB = ROOT / "skills/story-setup/references/workbuddy"
DATA_AGENTS = ROOT / "skills/story-data-analyze/agents/workbuddy"
DATA_ROLE_CARDS = ROOT / "skills/story-data-analyze/references/workbuddy-role-cards"
MANIFEST = ROOT / ".codebuddy-plugin/plugin.json"
CANONICAL_SKILL_NAMES = {
    "browser-cdp",
    "story",
    "story-cover",
    "story-data-analyze",
    "story-deslop",
    "story-explore",
    "story-import",
    "story-long-analyze",
    "story-long-scan",
    "story-long-write",
    "story-publish",
    "story-release-package",
    "story-research",
    "story-review",
    "story-setup",
    "story-short-analyze",
    "story-short-scan",
    "story-short-write",
}
GENERAL_AGENT_NAMES = {
    "chapter-extractor",
    "character-designer",
    "consistency-checker",
    "narrative-writer",
    "revision-governor",
    "story-architect",
    "story-explorer",
    "story-researcher",
}
DATA_FETCHER = "story-data-fetcher"
DATA_READONLY_RUNNER = "story-data-readonly-runner"
DATA_READONLY_LOGICAL_ROLES = {
    "story-data-metrics-analyst",
    "story-data-method-validator",
    "story-data-text-improvement-planner",
    "story-data-supervisor",
}


def require(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def frontmatter(path: Path) -> tuple[dict[str, str], str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\n(.*?)\n---\n(.*)\Z", text, re.DOTALL)
    require(match, f"{path.relative_to(ROOT)}: missing closed frontmatter")
    assert match
    metadata: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line or line[0].isspace() or ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip()
    return metadata, match.group(2)


def hook_commands(document: dict[str, object]) -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    hooks = document.get("hooks")
    require(isinstance(hooks, dict), "hook config must contain an object-valued hooks key")
    assert isinstance(hooks, dict)
    for event, groups in hooks.items():
        require(isinstance(groups, list), f"hooks.{event} must be an array")
        for group in groups:
            require(isinstance(group, dict), f"hooks.{event} contains a non-object group")
            matcher = str(group.get("matcher", ""))
            commands = group.get("hooks")
            require(isinstance(commands, list), f"hooks.{event} group lacks hooks array")
            for hook in commands:
                require(isinstance(hook, dict), f"hooks.{event} contains a non-object hook")
                require(set(hook) == {"type", "command", "timeout"}, f"unsupported hook fields: {hook}")
                require(hook["type"] == "command", f"non-command WorkBuddy hook: {hook}")
                require(isinstance(hook["timeout"], int) and 1 <= hook["timeout"] <= 600, f"invalid timeout: {hook}")
                found.append((str(event), matcher, str(hook["command"])))
    return found


def main() -> int:
    print("WorkBuddy adapter static check")
    print("==============================")

    required_files = (
        MANIFEST,
        WB / "CODEBUDDY.md.tmpl",
        WB / "rules/oh-story.md",
        WB / "hooks/hooks.json",
        WB / "hooks/project-hooks.json",
        WB / "hooks/disabled-hooks.json",
        WB / "hooks/story_workbuddy_hook.js",
        WB / "hooks/story_hook_core.js",
        ROOT / "skills/story-setup/scripts/merge-workbuddy-settings.py",
        ROOT / "scripts/generate-workbuddy-adapter.py",
    )
    for path in required_files:
        require(path.is_file(), f"missing {path.relative_to(ROOT)}")

    allowed_workbuddy_files = {
        Path("CODEBUDDY.md.tmpl"),
        Path("runtime-activation.md"),
        Path("rules/oh-story.md"),
        Path("hooks/hooks.json"),
        Path("hooks/project-hooks.json"),
        Path("hooks/disabled-hooks.json"),
        Path("hooks/story_workbuddy_hook.js"),
        Path("hooks/story_hook_core.js"),
        *{Path("commands") / f"{name}.md" for name in CANONICAL_SKILL_NAMES},
        *{Path("agents") / f"{name}.md" for name in GENERAL_AGENT_NAMES},
    }
    actual_workbuddy_files = {
        path.relative_to(WB) for path in WB.rglob("*") if path.is_file()
    }
    require(
        actual_workbuddy_files == allowed_workbuddy_files,
        "WorkBuddy adapter file inventory drift: "
        f"missing={sorted(map(str, allowed_workbuddy_files - actual_workbuddy_files))}, "
        f"extra={sorted(map(str, actual_workbuddy_files - allowed_workbuddy_files))}",
    )
    require(
        {path.name for path in DATA_AGENTS.iterdir() if path.is_file()}
        == {f"{DATA_FETCHER}.md", f"{DATA_READONLY_RUNNER}.md"},
        "WorkBuddy physical data-agent file inventory is not exact",
    )
    require(
        {path.name for path in DATA_ROLE_CARDS.iterdir() if path.is_file()}
        == {f"{name}.md" for name in DATA_READONLY_LOGICAL_ROLES},
        "WorkBuddy logical role-card file inventory is not exact",
    )

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    require(manifest.get("name") == "oh-story", "plugin name must be oh-story")
    require("commands" not in manifest, "plugin must not load same-name Commands beside Skills")
    require(not (ROOT / ".workbuddy-plugin").exists(), "keep one canonical .codebuddy-plugin manifest")
    root_real = ROOT.resolve(strict=True)
    for key in ("skills", "hooks"):
        value = manifest.get(key)
        require(isinstance(value, str) and value.startswith("./"), f"manifest {key} path must start ./")
        candidate = (ROOT / value).resolve(strict=True)
        require(
            candidate == root_real or root_real in candidate.parents,
            f"manifest {key} path escapes repository root: {value}",
        )
    agents_value = manifest.get("agents")
    require(isinstance(agents_value, list) and len(agents_value) == 2, "manifest must expose two agent roots")
    require(
        {str(value).rstrip("/") for value in agents_value} == {
            "./skills/story-setup/references/workbuddy/agents",
            "./skills/story-data-analyze/agents/workbuddy",
        },
        "manifest agent roots must expose physical cards only",
    )
    for value in agents_value:
        require(isinstance(value, str) and value.startswith("./"), f"manifest agent path must start ./: {value}")
        candidate = (ROOT / value).resolve(strict=True)
        require(candidate.is_dir(), f"manifest agent path is not a directory: {value}")
        require(
            candidate == root_real or root_real in candidate.parents,
            f"manifest agent path escapes repository root: {value}",
        )
    print("  OK canonical manifest and no Skill/Command basename collision")

    skills = {
        path.parent.name
        for path in (ROOT / "skills").glob("*/SKILL.md")
        if path.parent.name == "browser-cdp" or path.parent.name.startswith("story")
    }
    require(
        skills == CANONICAL_SKILL_NAMES,
        "canonical Chinese package Skill inventory drift: "
        f"missing={sorted(CANONICAL_SKILL_NAMES - skills)}, "
        f"extra={sorted(skills - CANONICAL_SKILL_NAMES)}",
    )
    commands = {path.stem for path in (WB / "commands").glob("*.md")}
    require(commands == CANONICAL_SKILL_NAMES, "project Commands must match the fixed canonical 18 Skills")
    for name in sorted(commands):
        metadata, body = frontmatter(WB / "commands" / f"{name}.md")
        require(set(metadata) == {"description", "argument-hint"}, f"unsupported command frontmatter: {name}")
        require(f"oh-story-managed: command/{name}" in body, f"missing managed marker: {name}")
        require(f"`{name}` Skill" in body, f"command does not route to its Skill: {name}")
    setup_text = (ROOT / "skills/story-setup/SKILL.md").read_text(encoding="utf-8")
    for name in skills:
        project_name = f"/{name}"
        plugin_name = f"/oh-story:{name}"
        require(project_name != plugin_name, f"namespace collision for {name}")
    for phrase in ("project 命令 `/story-*`", "plugin-only Skill `/oh-story:story-*`", "plugin manifest 不加载这批 Commands"):
        require(phrase in setup_text, f"story-setup lacks dual-mode namespace contract: {phrase}")
    print(f"  OK {len(skills)} project /name Commands vs plugin /oh-story:name Skills")

    base_agents = sorted((WB / "agents").glob("*.md"))
    data_agents = sorted(DATA_AGENTS.glob("*.md"))
    data_role_cards = sorted(DATA_ROLE_CARDS.glob("*.md"))
    require(
        {path.stem for path in base_agents} == GENERAL_AGENT_NAMES,
        f"general Agent inventory drift: {[path.stem for path in base_agents]}",
    )
    require(
        {path.stem for path in data_agents} == {DATA_FETCHER, DATA_READONLY_RUNNER},
        f"expected fetcher + readonly runner physical data agents, got {[path.stem for path in data_agents]}",
    )
    require(
        {path.stem for path in data_role_cards} == DATA_READONLY_LOGICAL_ROLES,
        f"logical data role-card inventory drift: {[path.stem for path in data_role_cards]}",
    )
    require(not ({path.stem for path in base_agents} & {path.stem for path in data_agents}), "agent roots overlap")
    require(len(base_agents) + len(data_agents) == 10, "base WorkBuddy physical roster must contain exactly 10 cards")
    allowed_frontmatter = {
        "name", "description", "tools", "disallowedTools", "model", "effort",
        "maxTurns", "skills", "memory", "background", "isolation",
    }
    for path in base_agents + data_agents:
        metadata, body = frontmatter(path)
        require(metadata.get("name") == path.stem, f"agent name mismatch: {path.name}")
        require(bool(metadata.get("description")), f"agent description missing: {path.name}")
        require(bool(metadata.get("tools")), f"agent tools missing: {path.name}")
        require(set(metadata) <= allowed_frontmatter, f"unsupported agent fields in {path.name}: {set(metadata) - allowed_frontmatter}")
        require(f"oh-story-managed: agent/{path.stem}" in body, f"missing agent marker: {path.name}")
        if ".codebuddy/skills/" in body:
            require("${CODEBUDDY_PLUGIN_ROOT}" in body, f"plugin-root fallback missing: {path.name}")
        require(".trae/" not in body and "TRAE Code" not in body, f"TRAE residue in {path.name}")
    runner_metadata, runner_body = frontmatter(DATA_AGENTS / f"{DATA_READONLY_RUNNER}.md")
    require(runner_metadata.get("tools") == "Read, Glob, Grep", "readonly runner tools must be exact")
    require(
        {part.strip() for part in runner_metadata.get("disallowedTools", "").split(",")}
        >= {"Write", "Edit", "Bash"},
        "readonly runner must disallow Write/Edit/Bash",
    )
    mapping_matches = re.findall(
        r"<!-- oh-story-logical-role-map: story-data-readonly-runner\n(\{.*?\})\n-->",
        runner_body,
        re.DOTALL,
    )
    require(len(mapping_matches) == 1, "readonly runner needs one machine-readable logical-role map")
    logical_map = json.loads(mapping_matches[0])
    require(
        logical_map == {role: DATA_READONLY_RUNNER for role in DATA_READONLY_LOGICAL_ROLES},
        f"readonly runner logical-role map is not total/unique: {logical_map}",
    )
    for path in data_role_cards:
        metadata, body = frontmatter(path)
        require(metadata.get("name") == path.stem, f"logical role-card name mismatch: {path.name}")
        require(bool(metadata.get("description")), f"logical role-card description missing: {path.name}")
        require(metadata.get("tools") == "Read, Glob, Grep", f"logical role-card tools drift: {path.name}")
        require(
            {part.strip() for part in metadata.get("disallowedTools", "").split(",")}
            >= {"Write", "Edit", "Bash"},
            f"logical role-card write boundary drift: {path.name}",
        )
        require(
            f"oh-story-managed: workbuddy-role-card/{path.stem}" in body,
            f"logical role-card marker missing: {path.name}",
        )
        require(DATA_READONLY_RUNNER in body, f"logical role-card lacks pool ownership: {path.name}")
        require("${CODEBUDDY_PLUGIN_ROOT}" in body, f"logical role-card lacks plugin-root fallback: {path.name}")
        require(".trae/" not in body and "TRAE Code" not in body, f"TRAE residue in logical card {path.name}")
    data_skill_text = (ROOT / "skills/story-data-analyze/SKILL.md").read_text(encoding="utf-8")
    for phrase in (
        "WorkBuddy 物理注册只检查 `story-data-fetcher` 与 `story-data-readonly-runner`",
        "`logical_role_card_path`",
        "references/workbuddy-role-cards/<logical_role>.md",
    ):
        require(phrase in data_skill_text, f"story-data-analyze lacks pooled WorkBuddy routing contract: {phrase}")
    print("  OK 10 physical Agent cards + four total/unique readonly logical role cards")

    plugin_hooks = json.loads((WB / "hooks/hooks.json").read_text(encoding="utf-8"))
    project_hooks = json.loads((WB / "hooks/project-hooks.json").read_text(encoding="utf-8"))
    disabled_hooks = json.loads((WB / "hooks/disabled-hooks.json").read_text(encoding="utf-8"))
    plugin_rows = hook_commands(plugin_hooks)
    project_rows = hook_commands(project_hooks)
    require(disabled_hooks == {"hooks": {}}, "disabled template must register no project hooks")
    require({row[0] for row in plugin_rows} == {"SessionStart", "PreToolUse", "PostToolUse"}, "hook event set drift")
    require(len(plugin_rows) == 4, f"expected four plugin hooks, got {len(plugin_rows)}")
    require(len(plugin_rows) == len(project_rows), "plugin/project hook shape count differs")
    official_tools = {"Bash", "PowerShell", "Write", "Edit", "MultiEdit"}
    for event, matcher, command in plugin_rows:
        require("${CODEBUDDY_PLUGIN_ROOT}" in command, f"plugin hook lacks plugin root: {command}")
        require("story_workbuddy_hook.js" in command, f"unexpected plugin hook command: {command}")
        if matcher:
            names = set(re.findall(r"[A-Za-z]+", matcher))
            require(names <= official_tools, f"unsupported matcher tools: {matcher}")
            require("ApplyPatch" not in matcher, f"CodeBuddy matcher must not invent ApplyPatch: {matcher}")
    for event, matcher, command in project_rows:
        require("node -e '" in command, f"project hook must use a cross-shell Node entrypoint: {command}")
        require("process.env.CODEBUDDY_PROJECT_DIR" in command, f"project hook does not read the runtime env in Node: {command}")
        require(
            'if(process.platform==="win32"&&/^\\/[A-Za-z]\\//.test(r))r=r[1]+":"+r.slice(2)' in command,
            f"project hook does not normalize CodeBuddy's /c/... Windows path: {command}",
        )
        require("main(process.argv[1])" in command, f"project hook does not pass its handler safely: {command}")
        require("$CODEBUDDY_PROJECT_DIR/" not in command, f"project hook relies on POSIX-only env expansion: {command}")
        require("story_workbuddy_hook.js" in command, f"unexpected project hook command: {command}")
    plugin_shape = [(event, matcher, command.rsplit(" ", 1)[-1]) for event, matcher, command in plugin_rows]
    project_shape = [(event, matcher, command.rsplit(" ", 1)[-1]) for event, matcher, command in project_rows]
    require(plugin_shape == project_shape, "plugin/project hook handlers or matchers drifted")
    guard_matchers = [matcher for event, matcher, command in plugin_rows if command.endswith("pre-tool-prose-guard")]
    commit_matchers = [matcher for event, matcher, command in plugin_rows if command.endswith("pre-tool-commit-advisory")]
    require(guard_matchers == ["^(Bash|PowerShell|Write|Edit|MultiEdit)$"], f"guard matcher drift: {guard_matchers}")
    require(commit_matchers == ["^(Bash|PowerShell)$"], f"commit matcher drift: {commit_matchers}")
    print("  OK official hook schema/tool names and plugin/project/disabled shapes")

    runner = (WB / "hooks/story_workbuddy_hook.js").read_text(encoding="utf-8")
    require("const WORKBUDDY_AGENTS_VERSION = 39" in runner, "runner lacks agents_version=39 gate")
    require("emit({ systemMessage: warnings })" in runner, "commit advisory must use top-level systemMessage")
    require('hookContext("PreToolUse", warnings)' not in runner, "commit advisory still uses undocumented additionalContext")
    require("extractPowerShellTargets" in runner, "runner lacks PowerShell target extraction")
    require("function main(event = process.argv[2]" in runner and "main," in runner, "runner lacks callable project-hook entrypoint")
    require(runner.count("let hookInputError = null") == 1, "runner lacks unique hook-input failure latch")
    require(
        runner.count('hookInputError && event === "pre-tool-prose-guard"') == 1,
        "runner lacks unique guarded malformed-input fail-closed branch",
    )
    require(
        runner.count('permissionDecisionReason: "oh-story WorkBuddy PreToolUse 机械门意外失败；') == 1,
        "runner lacks unique outer PreToolUse deny",
    )
    require("fail open and are diagnosable" not in runner, "runner retained stale guarded-event fail-open catch")
    require("runtimeTargetEnabled" in runner, "stale project runner lacks target_cli removal gate")
    require(
        "if (deployedWorkspaceRoot() && fs.existsSync(sentinel))" in runner,
        "plugin-only SessionStart must not read project deployment sentinel diagnostics",
    )
    require("{ continue: true, ...value }" in runner, "runner outputs lack top-level continue")
    boundary_files = (
        ROOT / "scripts/generate-workbuddy-adapter.py",
        ROOT / "skills/story-setup/SKILL.md",
        *sorted(path for path in WB.rglob("*") if path.is_file()),
        *sorted(path for path in DATA_AGENTS.rglob("*") if path.is_file()),
        *sorted(path for path in DATA_ROLE_CARDS.rglob("*") if path.is_file()),
    )
    forbidden_patterns = (
        re.compile(r"story_globalize_gate_core", re.IGNORECASE),
        re.compile(r"loadGlobalizeGates", re.IGNORECASE),
        re.compile(r"International[\\/]+(?:drafts|chapters)", re.IGNORECASE),
        re.compile(r"project_extension_root", re.IGNORECASE),
        re.compile(r"managed-skill-ownership", re.IGNORECASE),
    )
    language_route_allowlist = {WB / "agents/narrative-writer.md"}
    for path in boundary_files:
        text = path.read_text(encoding="utf-8")
        for pattern in forbidden_patterns:
            require(
                not pattern.search(text),
                f"Chinese WorkBuddy adapter leaked overseas/extension contract "
                f"{pattern.pattern}: {path.relative_to(ROOT)}",
            )
        if re.search(r"globalize", text, re.IGNORECASE):
            require(
                path in language_route_allowlist,
                f"globalize reference outside the baseline language-route allowlist: {path.relative_to(ROOT)}",
            )
        if "SubagentStop" in text:
            if path == ROOT / "skills/story-setup/SKILL.md":
                bad_lines = [
                    line
                    for line in text.splitlines()
                    if "SubagentStop" in line and not re.search(r"不复制|不得出现", line)
                ]
                require(not bad_lines, f"story-setup registers or positively requires SubagentStop: {bad_lines}")
            else:
                require(False, f"WorkBuddy adapter registers/references SubagentStop: {path.relative_to(ROOT)}")
    subprocess.run([sys.executable, str(ROOT / "scripts/generate-workbuddy-adapter.py"), "--check"], cwd=ROOT, check=True)
    subprocess.run(["node", "--check", str(WB / "hooks/story_workbuddy_hook.js")], cwd=ROOT, check=True)
    subprocess.run(["node", "--check", str(WB / "hooks/story_hook_core.js")], cwd=ROOT, check=True)
    require((WB / "hooks/story_hook_core.js").read_bytes() == (ROOT / "skills/story-setup/references/templates/hooks/story_hook_core.js").read_bytes(), "shared hook core drift")
    require("isHistoricalCopySegment" in (WB / "hooks/story_hook_core.js").read_text(encoding="utf-8"), "shared core does not exclude backup/archive discovery trees")
    print("  OK generated runner, output fields and shared-core parity")

    memory = (WB / "CODEBUDDY.md.tmpl").read_text(encoding="utf-8")
    require(memory.count("{{OH_STORY_AGENTS_IMPORT}}") == 1, "memory template needs one conditional import placeholder")
    require(memory.count("BEGIN oh-story-managed: workbuddy") == 1, "memory template needs one BEGIN marker")
    require(memory.count("END oh-story-managed: workbuddy") == 1, "memory template needs one END marker")
    require(
        memory.index("BEGIN oh-story-managed: workbuddy")
        < memory.index("{{OH_STORY_AGENTS_IMPORT}}")
        < memory.index("END oh-story-managed: workbuddy"),
        "conditional import must live inside the marker-merged block",
    )
    for phrase in (
        "若两份 CODEBUDDY 都存在，停止 memory 写入并报告冲突",
        "直接把 WorkBuddy 管理块合并进现有 `AGENTS.md`",
        "`@AGENTS.md` 或 `.codebuddy` 文件的 `@../AGENTS.md`",
        "三者都不存在时，创建 `.codebuddy/CODEBUDDY.md`",
        "`{{OH_STORY_AGENTS_IMPORT}}` 不得残留",
    ):
        require(phrase in setup_text, f"memory shadowing contract missing: {phrase}")
    rule = (WB / "rules/oh-story.md").read_text(encoding="utf-8")
    require("alwaysApply: true" in rule.split("---", 2)[1], "WorkBuddy rule must always apply")
    require("PowerShell Hook 只对 fixture 覆盖" in rule and ".NET API" in rule, "PowerShell hard-gate boundary missing")
    print("  OK CODEBUDDY/AGENTS anti-shadowing and PowerShell boundary contracts")

    print("\nOK: WorkBuddy adapter static checks passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, json.JSONDecodeError, OSError, subprocess.CalledProcessError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
