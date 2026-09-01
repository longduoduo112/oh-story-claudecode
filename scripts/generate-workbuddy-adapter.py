#!/usr/bin/env python3
"""Generate deterministic WorkBuddy / CodeBuddy commands and agent cards.

The narrative role prompts remain sourced from the already-reviewed TRAE
cards because both runtimes use Markdown agents with comma-separated tool
names.  WorkBuddy's bounded agent registry gets physical pool cards while
the complete logical prompts stay inside their owning Skill as references.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
WORKBUDDY_ROOT = ROOT / "skills/story-setup/references/workbuddy"
TRAE_ROOT = ROOT / "skills/story-setup/references/trae"
DATA_TRAE_ROOT = ROOT / "skills/story-data-analyze/agents/trae"
DATA_WORKBUDDY_ROOT = ROOT / "skills/story-data-analyze/agents/workbuddy"
DATA_WORKBUDDY_ROLE_CARDS = (
    ROOT / "skills/story-data-analyze/references/workbuddy-role-cards"
)
WORKBUDDY_AGENTS_VERSION = 39
CANONICAL_SKILL_NAMES = (
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
)

GENERAL_AGENT_NAMES = (
    "chapter-extractor",
    "character-designer",
    "consistency-checker",
    "narrative-writer",
    "revision-governor",
    "story-architect",
    "story-explorer",
    "story-researcher",
)

DATA_FETCHER = "story-data-fetcher"
DATA_READONLY_RUNNER = "story-data-readonly-runner"
DATA_READONLY_LOGICAL_ROLES = (
    "story-data-metrics-analyst",
    "story-data-method-validator",
    "story-data-text-improvement-planner",
    "story-data-supervisor",
)
TRAE_DATA_AGENT_NAMES = (DATA_FETCHER, *DATA_READONLY_LOGICAL_ROLES)
WORKBUDDY_PHYSICAL_DATA_AGENT_NAMES = (DATA_FETCHER, DATA_READONLY_RUNNER)


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    """Replace a generator anchor exactly once so source drift fails loudly."""

    count = text.count(old)
    if count != 1:
        raise ValueError(f"{label}: expected one source anchor, found {count}")
    return text.replace(old, new, 1)


def source_skill_names() -> list[str]:
    discovered = sorted(
        path.parent.name
        for path in (ROOT / "skills").glob("*/SKILL.md")
        if path.parent.name == "browser-cdp" or path.parent.name.startswith("story")
    )
    expected = sorted(CANONICAL_SKILL_NAMES)
    if discovered != expected:
        raise ValueError(
            f"canonical Chinese Skill inventory drift: expected={expected}, got={discovered}"
        )
    return expected


def command_description(name: str) -> str:
    source = TRAE_ROOT / "commands" / f"{name}.md"
    text = source.read_text(encoding="utf-8")
    match = re.search(r"^description:\s*(.+?)\s*$", text, re.MULTILINE)
    if not match:
        raise ValueError(f"missing command description: {source}")
    value = match.group(1).strip()
    if value.startswith(('"', "'")) and value.endswith(value[0]):
        value = value[1:-1]
    return value


def render_command(name: str) -> str:
    description = json.dumps(command_description(name), ensure_ascii=False)
    return f"""---
description: {description}
argument-hint: "[可选参数]"
---

<!-- oh-story-managed: command/{name} -->

请使用 `{name}` Skill 执行本次任务。

用户参数：$ARGUMENTS
"""


def transform_agent(text: str, *, data_agent: bool) -> str:
    transformed = text.replace(".trae/skills/", ".codebuddy/skills/")
    transformed = transformed.replace("TRAE Code", "WorkBuddy / CodeBuddy Code")
    transformed = transformed.replace(
        "TRAE 的项目 subagent", "WorkBuddy / CodeBuddy Code 的项目 subagent"
    )
    transformed = transformed.replace("调用该 Agent", "调用该子 Agent")

    # Plugin component content supports inline replacement of this variable;
    # project-local cards hit .codebuddy/ first and simply ignore this fallback.
    if data_agent:
        needle = (
            "`{项目根}/skills/story-data-analyze/`、"
            "`{项目根}/.codebuddy/skills/story-data-analyze/`、当前已加载 skill 的实际目录"
        )
        replacement = (
            "`{项目根}/.codebuddy/skills/story-data-analyze/`、"
            "`${CODEBUDDY_PLUGIN_ROOT}/skills/story-data-analyze/`、"
            "`{项目根}/skills/story-data-analyze/`、当前已加载 skill 的实际目录"
        )
        transformed = transformed.replace(needle, replacement)
        transformed = transformed.replace(
            "1. `{项目根}/skills/story-data-analyze/`\n"
            "2. `{项目根}/.codebuddy/skills/story-data-analyze/`\n"
            "3. 当前已加载 skill 的实际目录",
            "1. `{项目根}/.codebuddy/skills/story-data-analyze/`\n"
            "2. `${CODEBUDDY_PLUGIN_ROOT}/skills/story-data-analyze/`（插件模式；变量由 CodeBuddy 内联替换）\n"
            "3. `{项目根}/skills/story-data-analyze/`\n"
            "4. 当前已加载 skill 的实际目录",
        )
    else:
        needle = (
            "1. `{项目根}/.codebuddy/skills/story-setup/references/"
            "agent-references/{文件名}`"
        )
        replacement = (
            "1. `{项目根}/.codebuddy/skills/story-setup/references/"
            "agent-references/{文件名}`\n"
            "2. `${CODEBUDDY_PLUGIN_ROOT}/skills/story-setup/references/"
            "agent-references/{文件名}`（插件模式；变量由 CodeBuddy 内联替换）"
        )
        transformed = transformed.replace(needle, replacement)

    if data_agent and "${CODEBUDDY_PLUGIN_ROOT}" not in transformed:
        raise ValueError("generated WorkBuddy agent lacks plugin-root fallback")
    if ".trae/" in transformed or "TRAE Code" in transformed:
        raise ValueError("generated WorkBuddy agent still contains TRAE runtime paths")
    return transformed


def render_data_readonly_runner() -> str:
    role_map = json.dumps(
        {role: DATA_READONLY_RUNNER for role in DATA_READONLY_LOGICAL_ROLES},
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    role_rows = "\n".join(
        f"- `{role}` → `story-data-readonly-runner`" for role in DATA_READONLY_LOGICAL_ROLES
    )
    role_names = "|".join(re.escape(role) for role in DATA_READONLY_LOGICAL_ROLES)
    return f"""---
name: {DATA_READONLY_RUNNER}
description: WorkBuddy 数据分析只读角色池；根据 prompt 指定的逻辑角色卡执行指标分析、方法校验、文本定位或监督，不抓取、不写盘、不递归调用子 Agent。
tools: Read, Glob, Grep
disallowedTools: Write, Edit, Bash
---

<!-- oh-story-managed: agent/{DATA_READONLY_RUNNER} -->

# Story Data Read-only Runner — WorkBuddy 只读角色池

你是 WorkBuddy / CodeBuddy Code 中的物理 Agent，仅承载下列四个逻辑角色：

{role_rows}

<!-- oh-story-logical-role-map: story-data-readonly-runner
{role_map}
-->

`story-data-fetcher` 仍由同名独立物理 Agent 执行，不得由本 Runner 代理。除上述四个名称外，不接受别名、命名空间推测或其他角色。

## Prompt 必填封装

任务 prompt 必须是自包含对象，且同时提供：

- `logical_role`：必须唯一匹配 `^({role_names})$`。
- `logical_role_card_path`：已解析的真实绝对路径，不得保留 `${{CODEBUDDY_PLUGIN_ROOT}}`、`{{项目根}}` 或其他占位符。
- `project_abs_path`：当前作品项目的真实绝对路径。
- `task_contract`：对应 lane 的完整自包含任务合同；除该逻辑角色原合同外，必须显式含 `role=<logical_role>`、`run_id`、`lane`、`project_abs_path`、作品身份、冻结输入与 hash、允许输出和禁区。

## 启动前强制校验

1. 检查四个必填字段的类型与完整性；`task_contract.project_abs_path` 必须与顶层 `project_abs_path` 字节一致。
2. `logical_role_card_path` 必须是现存普通文件，规范化前不含 `..`，规范化后其直接父目录必须是调用方已解析的真实 `references/workbuddy-role-cards/` 根，文件名必须精确为 `<logical_role>.md`。不得只按路径后缀放行任意同名文件。
3. 打开并完整读取该卡。YAML frontmatter 必须闭合；`name` 必须精确等于 `logical_role`；`description` 非空；`tools` 必须精确为 `Read, Glob, Grep`；`disallowedTools` 必须至少含 `Write, Edit, Bash`。
4. 核对卡片中的自包含输入合同与 `task_contract`：`role`、`lane`、`run_id`、项目根、冻结 hash 或输出边界不一致时必须阻断，不得自行补齐。
5. 任一校验失败时，只返回该逻辑角色合同规定的 `blocked` envelope 与精确 gap，不执行后续分析。

## 执行边界

校验通过后，将卡片正文作为当次逻辑角色的完整职责，严格执行 `task_contract`，并使用 `logical_role` 而不是物理 Runner 名填写返回 envelope 的 `role`。

你只能使用 `Read, Glob, Grep`；不得 Write/Edit/Bash，不得调用 `Agent`、`Task`、subagent、spawn 或任何等价的子 Agent 机制，也不得通过其他工具间接写盘。你是叶子节点，永不递归 spawn。
"""


def render_data_role_card(source: Path) -> str:
    role = source.stem
    if role not in DATA_READONLY_LOGICAL_ROLES:
        raise ValueError(f"unsupported pooled data role: {role}")
    transformed = transform_agent(source.read_text(encoding="utf-8"), data_agent=True)
    transformed = replace_once(
        transformed,
        f"<!-- oh-story-managed: agent/{role} -->",
        f"<!-- oh-story-managed: workbuddy-role-card/{role} -->\n\n"
        f"> WorkBuddy 逻辑角色卡；只能由 `{DATA_READONLY_RUNNER}` 读取后执行，"
        "不得作为物理 Agent 注册。",
        label=f"WorkBuddy logical role marker/{role}",
    )
    return transformed


def transform_hook(text: str) -> str:
    transformed = text.replace(
        "// oh-story TRAE hook adapter for writing projects. It has no third-party\n"
        "// dependencies and emits only fields accepted by TRAE Code's strict hook\n"
        "// output schema. Diagnostics go to stderr; a healthy no-op keeps stdout empty.",
        "// oh-story WorkBuddy / CodeBuddy hook adapter for writing projects. It has\n"
        "// no third-party dependencies and emits only fields accepted by CodeBuddy's\n"
        "// hook output schema. Diagnostics go to stderr; healthy no-op keeps stdout empty.",
    )
    transformed = transformed.replace(
        "function emit(value) {\n"
        "  if (value && typeof value === \"object\") process.stdout.write(JSON.stringify(value))\n"
        "}",
        "function emit(value) {\n"
        "  if (value && typeof value === \"object\") {\n"
        "    process.stdout.write(JSON.stringify({ continue: true, ...value }))\n"
        "  }\n"
        "}",
    )
    transformed = transformed.replace(".trae", ".codebuddy")
    transformed = transformed.replace("TRAE_PROJECT_DIR", "CODEBUDDY_PROJECT_DIR")
    transformed = transformed.replace("includes(\"trae\")", "includes(\"workbuddy\")")
    transformed = transformed.replace(
        'runtimeTargetEnabled(projectRoot(), "trae")',
        'runtimeTargetEnabled(projectRoot(), "workbuddy")',
    )
    transformed = transformed.replace('runtime: "trae"', 'runtime: "workbuddy"')
    transformed = transformed.replace("未包含 trae", "未包含 workbuddy")
    transformed = transformed.replace("TRAE Code 项目适配", "WorkBuddy / CodeBuddy Code 项目适配")
    transformed = transformed.replace("选择 TRAE Code", "选择 WorkBuddy / CodeBuddy Code")
    transformed = transformed.replace("/runcommand/i", "/bash/i")
    transformed = transformed.replace("TRAE hook event", "WorkBuddy hook event")
    transformed = transformed.replace("oh-story trae hook", "oh-story workbuddy hook")
    transformed = replace_once(
        transformed,
        "function runtimeTargetEnabled(root, targetName) {\n",
        "function runtimeTargetEnabled(root, targetName) {\n"
        "  // Plugin runners live under the plugin package rather than the project's\n"
        "  // .codebuddy/hooks directory, so a project sentinel must not disable them.\n"
        "  if (!deployedWorkspaceRoot()) return true\n",
        label="WorkBuddy plugin/project target gate",
    )

    version_helpers = f'''const WORKBUDDY_AGENTS_VERSION = {WORKBUDDY_AGENTS_VERSION}

function workbuddyAgentsVersionFindings(sentinelText) {{
  const match = String(sentinelText || "").match(/^agents_version:\\s*(.*?)\\s*$/m)
  if (!match || !/^[0-9]+$/.test(match[1])) {{
    return [`[story-setup] .story-deployed 的 agents_version 缺失或无效；当前 WorkBuddy 适配要求版本 ${{WORKBUDDY_AGENTS_VERSION}}，请重新运行 /story-setup。`]
  }}
  const deployed = Number(match[1])
  if (deployed < WORKBUDDY_AGENTS_VERSION) {{
    return [`[story-setup] WorkBuddy Agents 版本 ${{deployed}} 低于当前要求的 ${{WORKBUDDY_AGENTS_VERSION}}；请重新运行 /story-setup 升级。`]
  }}
  if (deployed > WORKBUDDY_AGENTS_VERSION) {{
    return [`[story-setup] WorkBuddy Agents 版本 ${{deployed}} 高于当前适配器支持的 ${{WORKBUDDY_AGENTS_VERSION}}；请先更新 oh-story / WorkBuddy 适配器，勿用旧适配器降级覆盖。`]
  }}
  return []
}}

'''
    transformed = replace_once(
        transformed,
        "function sessionStart() {",
        version_helpers + "function sessionStart() {",
        label="WorkBuddy agents-version helpers",
    )
    transformed = replace_once(
        transformed,
        '    try { text = fs.readFileSync(sentinel, "utf8") } catch {}\n'
        '    const match = text.match(/^target_cli:\\s*(.+)$/m)',
        '    try { text = fs.readFileSync(sentinel, "utf8") } catch {}\n'
        '    messages.push(...workbuddyAgentsVersionFindings(text))\n'
        '    const match = text.match(/^target_cli:\\s*(.+)$/m)',
        label="WorkBuddy agents-version check",
    )
    transformed = replace_once(
        transformed,
        "  if (fs.existsSync(sentinel)) {\n",
        "  if (deployedWorkspaceRoot() && fs.existsSync(sentinel)) {\n",
        label="WorkBuddy project-only sentinel diagnostics",
    )

    powershell_helpers = r'''function powerShellSegments(command) {
  const segments = []
  let current = ""
  let quote = ""
  let escaped = false
  for (const ch of String(command || "")) {
    if (escaped) {
      current += ch
      escaped = false
      continue
    }
    if (ch === "`") {
      current += ch
      escaped = true
      continue
    }
    if (quote) {
      current += ch
      if (ch === quote) quote = ""
      continue
    }
    if (ch === '"' || ch === "'") {
      quote = ch
      current += ch
      continue
    }
    if (ch === ";" || ch === "|" || ch === "\n") {
      if (current.trim()) segments.push(current)
      current = ""
      continue
    }
    current += ch
  }
  if (current.trim()) segments.push(current)
  return segments
}

function powerShellNamedValue(args, names) {
  const wanted = new Set(names.map((name) => name.toLowerCase()))
  for (let index = 0; index < args.length; index++) {
    const token = String(args[index])
    const colon = token.match(/^-([^:=]+)[:=](.*)$/)
    if (colon && wanted.has(colon[1].toLowerCase()) && colon[2]) return colon[2]
    const plain = token.match(/^-([^:=]+)$/)
    if (plain && wanted.has(plain[1].toLowerCase())) return args[index + 1] || ""
  }
  return ""
}

function powerShellPositionals(args, valueOptions) {
  const options = new Set(valueOptions.map((name) => name.toLowerCase()))
  const positionals = []
  for (let index = 0; index < args.length; index++) {
    const token = String(args[index])
    if (token === "--%") {
      positionals.push(...args.slice(index + 1))
      break
    }
    const option = token.match(/^-([^:=]+)(?:[:=](.*))?$/)
    if (!option) {
      positionals.push(token)
      continue
    }
    if (options.has(option[1].toLowerCase()) && option[2] === undefined) index++
  }
  return positionals
}

function powerShellBasename(value) {
  const parts = String(value || "").replace(/\\/g, "/").split("/")
  return parts[parts.length - 1]
}

function powerShellJoin(directory, name) {
  return `${String(directory || "").replace(/[\\/]+$/, "")}/${name}`
}

function powerShellDestination(destination, source) {
  const normalized = String(destination || "").replace(/\\/g, "/")
  return normalized.endsWith("/") || normalized.split("/").pop() === "正文"
    ? powerShellJoin(destination, powerShellBasename(source))
    : destination
}

function isPowerShellStoryTarget(value) {
  return /(^|[\\/])(正文|大纲|设定)([\\/]|$)/.test(String(value || ""))
}

function extractPowerShellTargets(command) {
  const targets = [...extractProseTargets(command)]
  const contentOptions = ["path", "literalpath", "value", "encoding", "filter", "include", "exclude", "stream"]
  const fileOptions = ["filepath", "inputobject", "encoding", "width"]
  const itemOptions = ["path", "literalpath", "destination", "filter", "include", "exclude", "name", "value", "itemtype"]
  const aliases = new Map([
    ["sc", "set-content"], ["ac", "add-content"], ["clc", "clear-content"],
    ["ni", "new-item"], ["cp", "copy-item"], ["cpi", "copy-item"],
    ["mv", "move-item"], ["mi", "move-item"], ["ren", "rename-item"],
    ["rni", "rename-item"], ["tee", "tee-object"],
  ])
  for (const segment of powerShellSegments(command)) {
    const words = shellWords(segment.trim())
    if (!words.length) continue
    const rawCommandName = powerShellBasename(words[0]).replace(/\.exe$/i, "").toLowerCase()
    const commandName = aliases.get(rawCommandName) || rawCommandName
    const args = words.slice(1)
    let target = ""
    if (["set-content", "add-content", "clear-content"].includes(commandName)) {
      target = powerShellNamedValue(args, ["path", "literalpath"])
        || powerShellPositionals(args, contentOptions)[0]
    } else if (["out-file", "tee-object"].includes(commandName)) {
      target = powerShellNamedValue(args, ["filepath", "literalpath"])
        || powerShellPositionals(args, fileOptions)[0]
    } else if (commandName === "new-item") {
      const base = powerShellNamedValue(args, ["path", "literalpath"])
        || powerShellPositionals(args, itemOptions)[0]
      const name = powerShellNamedValue(args, ["name"])
      target = name && base && !/\.md$/i.test(base) ? powerShellJoin(base, name) : base
    } else if (["copy-item", "move-item"].includes(commandName)) {
      const positionals = powerShellPositionals(args, itemOptions)
      const namedSource = powerShellNamedValue(args, ["path", "literalpath"])
      const source = namedSource || positionals[0] || ""
      const destination = powerShellNamedValue(args, ["destination"])
        || (namedSource && positionals.length
          ? positionals[positionals.length - 1]
          : (positionals.length > 1 ? positionals[positionals.length - 1] : ""))
      target = powerShellDestination(destination, source)
    } else if (commandName === "rename-item") {
      const positionals = powerShellPositionals(args, itemOptions)
      const namedSource = powerShellNamedValue(args, ["path", "literalpath"])
      const source = namedSource || positionals[0] || ""
      const destination = powerShellNamedValue(args, ["newname"])
        || (namedSource && positionals.length
          ? positionals[positionals.length - 1]
          : (positionals.length > 1 ? positionals[positionals.length - 1] : ""))
      const normalizedDestination = String(destination).replace(/\\/g, "/")
      const normalizedSource = String(source).replace(/\\/g, "/")
      target = destination && !normalizedDestination.includes("/")
        ? powerShellJoin(normalizedSource.split("/").slice(0, -1).join("/"), destination)
        : destination
    }
    if (target && isPowerShellStoryTarget(target)) targets.push(target)
  }
  return [...new Set(targets.filter(Boolean))]
}

'''
    transformed = replace_once(
        transformed,
        "function targetPaths(input) {",
        powershell_helpers + "function targetPaths(input) {",
        label="PowerShell target helpers",
    )
    transformed = replace_once(
        transformed,
        "  if (command) {\n"
        "    if (/bash/i.test(name)) rawTargets.push(...extractProseTargets(command))\n"
        "    else rawTargets.push(...extractPatchTargets(command), ...extractProseTargets(command))\n"
        "  }",
        "  if (command) {\n"
        "    if (/powershell/i.test(name)) rawTargets.push(...extractPowerShellTargets(command))\n"
        "    else if (/bash/i.test(name)) rawTargets.push(...extractProseTargets(command))\n"
        "    else rawTargets.push(...extractPatchTargets(command), ...extractProseTargets(command))\n"
        "  }",
        label="PowerShell target routing",
    )
    transformed = replace_once(
        transformed,
        '  if (warnings) emit(hookContext("PreToolUse", warnings))',
        "  if (warnings) emit({ systemMessage: warnings })",
        label="CodeBuddy commit systemMessage",
    )
    transformed = replace_once(
        transformed,
        'function main() {\n  const event = process.argv[2] || ""',
        'function main(event = process.argv[2] || "") {',
        label="cross-shell project-hook entrypoint",
    )
    transformed = replace_once(
        transformed,
        '  } catch (error) {\n'
        '    const detail = error instanceof Error ? error.message : String(error)\n'
        '    process.stderr.write(`[oh-story workbuddy hook] ${detail}\\n`)\n'
        '    if (event === "pre-tool-prose-guard") {\n'
        '      emit({\n'
        '        hookSpecificOutput: {\n'
        '          hookEventName: "PreToolUse",\n'
        '          permissionDecision: "deny",\n'
        '          permissionDecisionReason: "oh-story TRAE PreToolUse 机械门意外失败；为避免受保护写入绕过，已按 fail-closed 拒绝。请检查 hook stderr 并重新运行 story-setup。",\n'
        '        },\n'
        '      })\n'
        '    }\n'
        '  }',
        '  } catch (error) {\n'
        '    const detail = error instanceof Error ? error.message : String(error)\n'
        '    process.stderr.write(`[oh-story workbuddy hook] ${detail}\\n`)\n'
        '    if (event === "pre-tool-prose-guard") {\n'
        '      emit({\n'
        '        hookSpecificOutput: {\n'
        '          hookEventName: "PreToolUse",\n'
        '          permissionDecision: "deny",\n'
        '          permissionDecisionReason: "oh-story WorkBuddy PreToolUse 机械门意外失败；为避免受保护写入绕过，已按 fail-closed 拒绝。请检查 hook stderr 并重新运行 story-setup。",\n'
        '        },\n'
        '      })\n'
        '    }\n'
        '  }',
        label="WorkBuddy guarded-event outer fail-closed",
    )
    transformed = replace_once(
        transformed,
        "  isGitCommitCommand,\n"
        "  isPathInside,\n"
        "  runtimeTargetEnabled,\n"
        "}",
        "  isGitCommitCommand,\n"
        "  isPathInside,\n"
        "  runtimeTargetEnabled,\n"
        "  workbuddyAgentsVersionFindings,\n"
        "  extractPowerShellTargets,\n"
        "  main,\n"
        "}",
        label="WorkBuddy test exports",
    )
    if ".trae" in transformed or "TRAE_PROJECT_DIR" in transformed:
        raise ValueError("generated WorkBuddy hook still contains TRAE runtime paths")
    if "continue: true" not in transformed or "CODEBUDDY_PROJECT_DIR" not in transformed:
        raise ValueError("generated WorkBuddy hook lacks CodeBuddy output/root contract")
    fail_closed_contracts = {
        "hook-input error latch": "let hookInputError = null",
        "guarded malformed-input check": 'hookInputError && event === "pre-tool-prose-guard"',
        "PreToolUse outer deny": (
            'permissionDecisionReason: "oh-story WorkBuddy PreToolUse 机械门意外失败；'
        ),
    }
    for label, contract in fail_closed_contracts.items():
        if transformed.count(contract) != 1:
            raise ValueError(f"generated WorkBuddy hook lacks unique {label} contract")
    if "fail open and are diagnosable" in transformed:
        raise ValueError("generated WorkBuddy guarded events still carry the stale fail-open outer catch")
    return transformed


def expected_outputs() -> dict[Path, str]:
    outputs: dict[Path, str] = {}
    for name in source_skill_names():
        outputs[WORKBUDDY_ROOT / "commands" / f"{name}.md"] = render_command(name)
    base_sources = {
        source.stem: source for source in (TRAE_ROOT / "agents").glob("*.md")
    }
    expected_base_sources = set(GENERAL_AGENT_NAMES)
    if set(base_sources) != expected_base_sources:
        missing = sorted(expected_base_sources - set(base_sources))
        extra = sorted(set(base_sources) - expected_base_sources)
        raise ValueError(
            f"TRAE general-agent inventory drift: missing={missing}, extra={extra}"
        )
    for name in GENERAL_AGENT_NAMES:
        source = base_sources[name]
        outputs[WORKBUDDY_ROOT / "agents" / source.name] = transform_agent(
            source.read_text(encoding="utf-8"), data_agent=False
        )
    data_sources = {source.stem: source for source in DATA_TRAE_ROOT.glob("*.md")}
    expected_data_sources = set(TRAE_DATA_AGENT_NAMES)
    if set(data_sources) != expected_data_sources:
        missing = sorted(expected_data_sources - set(data_sources))
        extra = sorted(set(data_sources) - expected_data_sources)
        raise ValueError(f"TRAE data-agent inventory drift: missing={missing}, extra={extra}")
    fetcher_source = data_sources[DATA_FETCHER]
    outputs[DATA_WORKBUDDY_ROOT / fetcher_source.name] = transform_agent(
        fetcher_source.read_text(encoding="utf-8"), data_agent=True
    )
    outputs[DATA_WORKBUDDY_ROOT / f"{DATA_READONLY_RUNNER}.md"] = (
        render_data_readonly_runner()
    )
    for role in DATA_READONLY_LOGICAL_ROLES:
        source = data_sources[role]
        outputs[DATA_WORKBUDDY_ROLE_CARDS / source.name] = render_data_role_card(source)
    outputs[WORKBUDDY_ROOT / "hooks/story_workbuddy_hook.js"] = transform_hook(
        (TRAE_ROOT / "hooks/story_trae_hook.js").read_text(encoding="utf-8")
    )
    outputs[WORKBUDDY_ROOT / "hooks/story_hook_core.js"] = (
        ROOT / "skills/story-setup/references/templates/hooks/story_hook_core.js"
    ).read_text(encoding="utf-8")
    return outputs


def check(outputs: dict[Path, str]) -> int:
    problems: list[str] = []
    for path, expected in outputs.items():
        if not path.exists():
            problems.append(f"missing {path.relative_to(ROOT)}")
        elif path.read_text(encoding="utf-8") != expected:
            problems.append(f"stale {path.relative_to(ROOT)}")

    expected_paths = set(outputs)
    for directory in (
        WORKBUDDY_ROOT / "commands",
        WORKBUDDY_ROOT / "agents",
        DATA_WORKBUDDY_ROOT,
        DATA_WORKBUDDY_ROLE_CARDS,
        WORKBUDDY_ROOT / "hooks",
    ):
        if directory.exists():
            for path in directory.glob("*.md"):
                if path not in expected_paths:
                    problems.append(f"unexpected {path.relative_to(ROOT)}")

    if problems:
        for problem in problems:
            print(f"FAIL: {problem}", file=sys.stderr)
        return 1
    print(f"OK WorkBuddy generated adapter ({len(outputs)} files)")
    return 0


def write(outputs: dict[Path, str]) -> int:
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return check(outputs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify committed outputs")
    args = parser.parse_args()
    outputs = expected_outputs()
    return check(outputs) if args.check else write(outputs)


if __name__ == "__main__":
    raise SystemExit(main())
