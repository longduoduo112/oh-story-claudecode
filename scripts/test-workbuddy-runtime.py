#!/usr/bin/env python3
"""Synthetic WorkBuddy hook, memory and plugin/project mutex tests."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
WB = ROOT / "skills/story-setup/references/workbuddy"
RUNNER = WB / "hooks/story_workbuddy_hook.js"
MERGER = ROOT / "skills/story-setup/scripts/merge-workbuddy-settings.py"
DATA_AGENTS = ROOT / "skills/story-data-analyze/agents/workbuddy"
DATA_ROLE_CARDS = ROOT / "skills/story-data-analyze/references/workbuddy-role-cards"
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


def configure_utf8_stdio() -> None:
    """Keep Chinese diagnostics portable on non-UTF-8 Windows consoles."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="strict")


def git_for_windows_bash() -> str:
    """Locate Git for Windows Bash, the shell CodeBuddy uses for Hooks."""

    candidates: list[Path] = []
    git = shutil.which("git")
    if git:
        git_path = Path(git).resolve()
        candidates.extend(
            (
                git_path.parent / "bash.exe",
                git_path.parent.parent / "bin/bash.exe",
                git_path.parent.parent / "usr/bin/bash.exe",
            )
        )
    bash = shutil.which("bash")
    if bash:
        candidates.append(Path(bash).resolve())
    for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(variable)
        if not base:
            continue
        root = Path(base)
        candidates.extend(
            (
                root / "Git/bin/bash.exe",
                root / "Git/usr/bin/bash.exe",
                root / "Programs/Git/bin/bash.exe",
                root / "Programs/Git/usr/bin/bash.exe",
            )
        )

    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(str(candidate))
        if key in seen or not candidate.is_file():
            continue
        seen.add(key)
        probe = subprocess.run(
            [str(candidate), "-lc", "uname -s"],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        if probe.returncode == 0 and re.match(r"^(?:MINGW|MSYS)", probe.stdout.strip(), re.IGNORECASE):
            return str(candidate)
    raise AssertionError(
        "Windows WorkBuddy Hook tests require Git for Windows Bash; "
        "CodeBuddy Hooks do not use cmd.exe or PowerShell"
    )


def run_hook(
    project: Path,
    event: str,
    payload: dict[str, object],
    *,
    runner: Path = RUNNER,
) -> tuple[str, str]:
    completed = run_hook_process(project, event, payload, runner=runner)
    if completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode,
            completed.args,
            output=completed.stdout,
            stderr=completed.stderr,
        )
    return completed.stdout, completed.stderr


def run_hook_process(
    project: Path,
    event: str,
    payload: dict[str, object],
    *,
    runner: Path = RUNNER,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["CODEBUDDY_PROJECT_DIR"] = str(project)
    return subprocess.run(
        ["node", str(runner), event],
        cwd=project,
        env=environment,
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def run_hook_raw(
    project: Path,
    event: str,
    payload: str,
    *,
    runner: Path = RUNNER,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["CODEBUDDY_PROJECT_DIR"] = str(project)
    return subprocess.run(
        ["node", str(runner), event],
        cwd=project,
        env=environment,
        input=payload,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def parsed_output(output: str, event: str) -> dict[str, object]:
    require(output, f"{event}: expected JSON output")
    value = json.loads(output)
    require(isinstance(value, dict), f"{event}: output must be an object")
    require(value.get("continue") is True, f"{event}: output must carry continue=true")
    return value


def additional_context(output: str, event: str) -> str:
    value = parsed_output(output, event)
    specific = value.get("hookSpecificOutput")
    require(isinstance(specific, dict), f"{event}: missing hookSpecificOutput")
    assert isinstance(specific, dict)
    require(specific.get("hookEventName") == event, f"{event}: hookEventName mismatch")
    context = specific.get("additionalContext")
    require(isinstance(context, str) and context, f"{event}: missing additionalContext")
    return context


def denied(output: str, label: str) -> str:
    value = parsed_output(output, "PreToolUse")
    specific = value.get("hookSpecificOutput")
    require(isinstance(specific, dict), f"{label}: missing deny payload")
    assert isinstance(specific, dict)
    require(specific.get("hookEventName") == "PreToolUse", f"{label}: wrong event")
    require(specific.get("permissionDecision") == "deny", f"{label}: did not deny")
    reason = specific.get("permissionDecisionReason")
    require(isinstance(reason, str) and reason, f"{label}: missing deny reason")
    return reason


def install_project_runner(project: Path) -> Path:
    hook_dir = project / ".codebuddy/hooks"
    hook_dir.mkdir(parents=True, exist_ok=True)
    runner = hook_dir / RUNNER.name
    shutil.copy2(RUNNER, runner)
    shutil.copy2(WB / "hooks/story_hook_core.js", hook_dir / "story_hook_core.js")
    return runner


def write_clean_state(book: Path, last_chapter: int = 0) -> None:
    tracking = book / "追踪"
    tracking.mkdir(parents=True, exist_ok=True)
    (tracking / "_tracking-state.json").write_text(
        json.dumps(
            {
                "schema_version": 5,
                "state_revision": 0,
                "last_committed_chapter": last_chapter,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (tracking / "上下文.md").write_text("> 状态修订：0\n", encoding="utf-8")


def power_shell_targets(command: str) -> list[str]:
    script = (
        "const h=require(process.argv[1]);"
        "process.stdout.write(JSON.stringify(h.extractPowerShellTargets(process.argv[2])))"
    )
    completed = subprocess.run(
        ["node", "-e", script, str(RUNNER), command],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    value = json.loads(completed.stdout)
    require(isinstance(value, list) and all(isinstance(item, str) for item in value), "invalid PowerShell extraction result")
    return value


def managed_commands(document: dict[str, object]) -> list[str]:
    found: list[str] = []
    hooks = document.get("hooks", {})
    require(isinstance(hooks, dict), "settings hooks must be an object")
    assert isinstance(hooks, dict)
    for groups in hooks.values():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            nested = group.get("hooks")
            candidates = nested if isinstance(nested, list) else [group]
            for hook in candidates:
                if isinstance(hook, dict) and isinstance(hook.get("command"), str):
                    command = str(hook["command"])
                    if "story_workbuddy_hook.js" in command.lower().replace("\\", "/"):
                        found.append(command)
    return found


def all_commands(document: dict[str, object]) -> list[str]:
    found: list[str] = []
    hooks = document.get("hooks", {})
    if not isinstance(hooks, dict):
        return found
    for groups in hooks.values():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            nested = group.get("hooks")
            candidates = nested if isinstance(nested, list) else [group]
            for hook in candidates:
                if isinstance(hook, dict) and isinstance(hook.get("command"), str):
                    found.append(str(hook["command"]))
    return found


def run_merge(existing: Path, template: Path, output: Path) -> None:
    subprocess.run(
        [sys.executable, str(MERGER), "--existing", str(existing), "--template", str(template), "--output", str(output)],
        cwd=ROOT,
        check=True,
    )


def test_versions(project: Path) -> None:
    runner = install_project_runner(project)
    sentinel = project / ".story-deployed"
    cases = (
        ("target_cli: workbuddy\n", "缺失或无效"),
        ("agents_version: invalid\ntarget_cli: workbuddy\n", "缺失或无效"),
        ("agents_version: 38\ntarget_cli: workbuddy\n", "低于当前要求的 39"),
        ("agents_version: 40\ntarget_cli: workbuddy\n", "高于当前适配器支持的 39"),
    )
    for payload, expected in cases:
        sentinel.write_text(payload, encoding="utf-8")
        output, _ = run_hook(
            project,
            "session-start",
            {"hook_event_name": "SessionStart", "source": "startup"},
            runner=runner,
        )
        context = additional_context(output, "SessionStart")
        require(expected in context, f"agents_version case missed {expected!r}: {context}")

    # setup_skill_version has an independent lifecycle. It must not be compared
    # to the agent bundle number or turn a current agents_version into a stale warning.
    sentinel.write_text(
        "agents_version: 39\nsetup_skill_version: 0.0.1\ntarget_cli: workbuddy\n",
        encoding="utf-8",
    )
    output, _ = run_hook(
        project,
        "session-start",
        {"hook_event_name": "SessionStart", "source": "startup"},
        runner=runner,
    )
    require(output == "", f"agents=39 with old setup_skill_version must not warn: {output}")


def test_plugin_runner_ignores_project_sentinel(project: Path) -> None:
    project.mkdir(parents=True)
    (project / ".story-deployed").write_text(
        "agents_version: 38\nsetup_skill_version: 1.2.21\n"
        "target_cli: generic\nresolver_strategy: project-local-skill-reference\n"
        "references_dir: skills/story-setup/references/agent-references\n",
        encoding="utf-8",
    )
    session, _ = run_hook(
        project,
        "session-start",
        {"hook_event_name": "SessionStart", "source": "startup"},
    )
    require(
        session == "",
        f"plugin-only runner read unrelated project version/target sentinel: {session}",
    )

    (project / "book/正文").mkdir(parents=True)
    guard, _ = run_hook(
        project,
        "pre-tool-prose-guard",
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": "book/正文/第1章.md", "content": "正文。"},
        },
    )
    denied(guard, "plugin-only runner with unrelated project sentinel")


def test_project_hook_commands_cross_shell(temp: Path) -> None:
    project = temp / "含 空格 project"
    hook_dir = project / ".codebuddy/hooks"
    hook_dir.mkdir(parents=True)
    shutil.copy2(RUNNER, hook_dir / RUNNER.name)
    shutil.copy2(WB / "hooks/story_hook_core.js", hook_dir / "story_hook_core.js")
    (project / ".story-deployed").write_text(
        "agents_version: 39\ntarget_cli: workbuddy\n",
        encoding="utf-8",
    )
    config = json.loads((WB / "hooks/project-hooks.json").read_text(encoding="utf-8"))
    commands = managed_commands(config)
    require(len(commands) == 4, f"expected four project hook commands, got {len(commands)}")
    for command in commands:
        require(
            'if(process.platform==="win32"&&/^\\/[A-Za-z]\\//.test(r))r=r[1]+":"+r.slice(2)' in command,
            f"project hook command lacks CodeBuddy /c/... drive normalization: {command}",
        )
    shells: list[tuple[str, list[str]]] = []
    if os.name == "nt":
        shells.append(("Git for Windows Bash", [git_for_windows_bash(), "-lc"]))
    else:
        shells.append(("POSIX sh", ["/bin/sh", "-c"]))
    for shell_name, prefix in shells:
        project_dirs = [str(project.resolve())]
        if os.name == "nt":
            match = re.fullmatch(r"([A-Za-z]):[\\/](.*)", project_dirs[0])
            require(match, f"Windows project path lacks a drive letter: {project_dirs[0]}")
            assert match
            # CodeBuddy 2.115 has emitted both native Windows and MSYS drive
            # shapes across launch paths. The Git Bash command must accept both.
            project_dirs.append(f"/{match.group(1).lower()}/{match.group(2).replace(chr(92), '/')}")
        for project_dir in project_dirs:
            environment = os.environ.copy()
            environment["CODEBUDDY_PROJECT_DIR"] = project_dir
            for command in commands:
                completed = subprocess.run(
                    [*prefix, command],
                    cwd=project,
                    env=environment,
                    input="{}",
                    text=True,
                    encoding="utf-8",
                    capture_output=True,
                    check=False,
                )
                require(
                    completed.returncode == 0 and completed.stdout == "",
                    f"{shell_name} could not execute project hook command for {project_dir}\n"
                    f"command={command}\nexit={completed.returncode}\n"
                    f"stdout={completed.stdout}\nstderr={completed.stderr}",
                )


def test_guarded_outer_fail_closed(temp: Path) -> None:
    project = temp / "outer-failure"
    hook_dir = project / ".codebuddy/hooks"
    hook_dir.mkdir(parents=True)
    runner = hook_dir / RUNNER.name
    shutil.copy2(RUNNER, runner)
    shutil.copy2(WB / "hooks/story_hook_core.js", hook_dir / "story_hook_core.js")
    (project / ".story-deployed").write_text(
        "agents_version: 39\ntarget_cli: workbuddy\n",
        encoding="utf-8",
    )

    malformed_guard = run_hook_raw(project, "pre-tool-prose-guard", "{not-json", runner=runner)
    require(malformed_guard.returncode == 0, f"malformed PreToolUse should return a JSON deny: {malformed_guard}")
    require("fail-closed" in denied(malformed_guard.stdout, "malformed PreToolUse"), malformed_guard.stdout)
    require(malformed_guard.stderr, "malformed PreToolUse must retain a diagnostic on stderr")
    injected = r"""
const fs = require("node:fs")
const runner = require(process.argv[1])
const originalExistsSync = fs.existsSync
fs.existsSync = function injectedExistsSync(file) {
  if (String(file).endsWith(".story-deployed")) throw new Error("injected fs/root failure")
  return originalExistsSync.call(fs, file)
}
runner.main(process.argv[2])
"""

    def run_injected(event: str, payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["CODEBUDDY_PROJECT_DIR"] = str(project)
        return subprocess.run(
            ["node", "-e", injected, str(runner), event],
            cwd=project,
            env=environment,
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

    guard = run_injected(
        "pre-tool-prose-guard",
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": "book/正文/第1章.md"},
        },
    )
    require(guard.returncode == 0, f"PreToolUse outer failure should use a JSON deny: {guard}")
    require("fail-closed" in denied(guard.stdout, "injected PreToolUse failure"), guard.stdout)
    require("injected fs/root failure" in guard.stderr, f"PreToolUse lost outer error diagnostic: {guard.stderr}")

    diagnostic = run_injected("session-start", {"hook_event_name": "SessionStart", "source": "startup"})
    require(
        diagnostic.returncode == 0 and diagnostic.stdout == "" and "injected fs/root failure" in diagnostic.stderr,
        f"non-guard SessionStart outer diagnostic should remain non-blocking: {diagnostic}",
    )


def test_historical_copy_discovery(project: Path) -> None:
    project.mkdir(parents=True)
    live = project / "book"
    (live / "正文").mkdir(parents=True)
    (live / "正文/第1章.md").write_text("正文。\n", encoding="utf-8")
    write_clean_state(live, last_chapter=1)
    historical = (
        project / "备份_2026" / "old-book",
        project / "归档_旧追踪" / "old-book",
        project / "archives" / "old-book",
    )
    for old in historical:
        (old / "正文").mkdir(parents=True)
        (old / "正文/第1章.md").write_text("历史副本。\n", encoding="utf-8")
    (project / ".story-deployed").write_text(
        "agents_version: 39\nsetup_skill_version: 1.2.22\n"
        "target_cli: workbuddy\nresolver_strategy: project-local-skill-reference\n"
        "references_dir: .codebuddy/skills/story-setup/references/agent-references\n",
        encoding="utf-8",
    )
    for old in historical:
        (project / ".active-book").write_text(old.relative_to(project).as_posix() + "\n", encoding="utf-8")
        output, _ = run_hook(
            project,
            "session-start",
            {"hook_event_name": "SessionStart", "source": "startup"},
        )
        context = additional_context(output, "SessionStart")
        require("当前书目：book" in context, f"historical .active-book did not fall back to live book: {context}")
        for segment in ("备份_2026", "归档_旧追踪", "archives"):
            require(segment not in context, f"historical copy leaked into discovery: {context}")
        require("_tracking-state.json 缺失" not in context, f"historical copy produced continuity debt: {context}")


def test_removed_target_runner_gate(project: Path) -> None:
    hook_dir = project / ".codebuddy/hooks"
    hook_dir.mkdir(parents=True)
    runner = hook_dir / RUNNER.name
    shutil.copy2(RUNNER, runner)
    shutil.copy2(WB / "hooks/story_hook_core.js", hook_dir / "story_hook_core.js")
    (project / ".story-deployed").write_text(
        "agents_version: 39\nsetup_skill_version: 1.2.22\n"
        "target_cli: generic\nresolver_strategy: project-local-skill-reference\n"
        "references_dir: skills/story-setup/references/agent-references\n",
        encoding="utf-8",
    )
    (project / "book/正文").mkdir(parents=True)
    session, _ = run_hook(
        project,
        "session-start",
        {"hook_event_name": "SessionStart", "source": "startup"},
        runner=runner,
    )
    require(session == "", f"removed WorkBuddy target still emitted SessionStart output: {session}")
    guard, _ = run_hook(
        project,
        "pre-tool-prose-guard",
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": "book/正文/第1章.md", "content": "x"},
        },
        runner=runner,
    )
    require(guard == "", f"removed WorkBuddy target still enforced PreToolUse: {guard}")


def test_powershell(project: Path) -> None:
    fixtures = {
        'Set-Content -Path "book\\正文\\第1章.md" -Value "正文;仍在引号内"': ["book\\正文\\第1章.md"],
        'Add-Content -LiteralPath "book/正文/第2章.md" -Value x': ["book/正文/第2章.md"],
        'Clear-Content "book/正文/第3章.md"': ["book/正文/第3章.md"],
        '"正文" | Out-File -FilePath "book/正文/第4章.md"': ["book/正文/第4章.md"],
        'Get-Content input.txt | Tee-Object -FilePath "book/正文/第5章.md"': ["book/正文/第5章.md"],
        'New-Item -Path "book/正文" -Name "第6章.md" -ItemType File': ["book/正文/第6章.md"],
        'Copy-Item draft.md -Destination "book/正文/第7章.md"': ["book/正文/第7章.md"],
        'Copy-Item -Path draft.md "book/正文/第8章.md"': ["book/正文/第8章.md"],
        'Move-Item -Path draft.md -Destination "book/正文/"': ["book/正文/draft.md"],
        'Rename-Item -Path "book/正文/draft.md" -NewName "第9章.md"': ["book/正文/第9章.md"],
        'sc "book/正文/第10章.md" x': ["book/正文/第10章.md"],
        'cp draft.md "book/正文/第11章.md"': ["book/正文/第11章.md"],
        '"正文" > "book/正文/第12章.md"': ["book/正文/第12章.md"],
        'Get-Content "book/正文/第1章.md"': [],
        'Set-Content -Path ($dir + "/正文/第13章.md") -Value x': [],
    }
    for command, expected in fixtures.items():
        actual = power_shell_targets(command)
        require(actual == expected, f"PowerShell target mismatch\ncommand={command}\nexpected={expected}\nactual={actual}")

    book = project / "book"
    (book / "正文").mkdir(parents=True)
    output, _ = run_hook(
        project,
        "pre-tool-prose-guard",
        {"hook_event_name": "PreToolUse", "tool_name": "PowerShell", "tool_input": {"command": 'Set-Content -Path "book/正文/第1章.md" -Value x'}},
    )
    reason = denied(output, "PowerShell prose without outline")
    require("细纲" in reason, f"PowerShell denial lacks outline diagnosis: {reason}")
    (book / "大纲").mkdir()
    (book / "大纲/细纲_第1章.md").write_text("# 细纲\n", encoding="utf-8")
    write_clean_state(book)
    output, _ = run_hook(
        project,
        "pre-tool-prose-guard",
        {"tool_name": "PowerShell", "tool_input": {"command": 'Set-Content -Path "book/正文/第1章.md" -Value x'}},
    )
    require(output == "", f"PowerShell prose with outline/state should pass: {output}")
    output, _ = run_hook(
        project,
        "pre-tool-prose-guard",
        {"tool_name": "PowerShell", "tool_input": {"command": 'Get-Content "book/正文/第1章.md"'}},
    )
    require(output == "", f"read-only PowerShell mention must not be treated as write: {output}")

    (book / "正文/第1章.md").write_text("正文 TODO。", encoding="utf-8")
    output, _ = run_hook(
        project,
        "post-tool-prose-check",
        {"hook_event_name": "PostToolUse", "tool_name": "PowerShell", "tool_input": {"command": 'Set-Content -Path "book/正文/第1章.md" -Value x'}},
    )
    context = additional_context(output, "PostToolUse")
    require("占位符" in context, f"post-PowerShell scan missed written prose: {context}")


def test_commit_system_message(project: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.email", "workbuddy-hook@example.invalid"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.name", "workbuddy-hook-test"], cwd=project, check=True)
    target = project / "book/正文/第10章_属性.md"
    target.write_text("年龄：18\n", encoding="utf-8")
    subprocess.run(["git", "add", str(target)], cwd=project, check=True)
    output, _ = run_hook(
        project,
        "pre-tool-commit-advisory",
        {"hook_event_name": "PreToolUse", "tool_name": "PowerShell", "tool_input": {"command": "git commit -m test"}},
    )
    value = parsed_output(output, "PreToolUse")
    require(set(value) == {"continue", "systemMessage"}, f"commit advisory must use stable top-level fields: {value}")
    require("硬编码角色属性" in str(value["systemMessage"]), f"commit advisory missed staged warning: {value}")
    output, _ = run_hook(
        project,
        "pre-tool-commit-advisory",
        {"tool_name": "PowerShell", "tool_input": {"command": "Write-Output 'git commit docs'"}},
    )
    require(output == "", f"non-commit command must be a no-op: {output}")


def test_hook_mutex(temp: Path) -> None:
    temp.mkdir(parents=True, exist_ok=True)
    existing = temp / "existing.json"
    project_output = temp / "project.json"
    project_again = temp / "project-again.json"
    plugin_output = temp / "plugin.json"
    plugin_again = temp / "plugin-again.json"
    existing.write_text(
        json.dumps(
            {
                "model": "user-model",
                "permissions": {"allow": ["Read"]},
                "hooks": {
                    "SessionStart": [
                        {
                            "hooks": [
                                {"type": "command", "command": "node .codebuddy/hooks/story_workbuddy_hook.js old-session", "timeout": 3},
                                {"type": "command", "command": "node user-session.js", "timeout": 9},
                            ]
                        }
                    ],
                    "PreToolUse": [
                        {"matcher": "Write", "hooks": [{"type": "command", "command": "node C:\\repo\\.codebuddy\\hooks\\STORY_WORKBUDDY_HOOK.JS old", "timeout": 4}]},
                        {"matcher": "Bash", "hooks": [{"type": "command", "command": "node user-pre.js", "timeout": 8}]},
                    ],
                    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "node user-prompt.js", "timeout": 7}]}],
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    project_template = WB / "hooks/project-hooks.json"
    disabled_template = WB / "hooks/disabled-hooks.json"
    run_merge(existing, project_template, project_output)
    project = json.loads(project_output.read_text(encoding="utf-8"))
    template = json.loads(project_template.read_text(encoding="utf-8"))
    require(project.get("model") == "user-model" and project.get("permissions") == {"allow": ["Read"]}, "project merge lost top-level user settings")
    require(sorted(managed_commands(project)) == sorted(managed_commands(template)), "project mode must register each managed hook exactly once")
    require({"node user-session.js", "node user-pre.js", "node user-prompt.js"} <= set(all_commands(project)), "project merge lost user hooks")
    run_merge(project_output, project_template, project_again)
    require(project_output.read_bytes() == project_again.read_bytes(), "project hook merge is not byte-idempotent")

    run_merge(project_output, disabled_template, plugin_output)
    plugin = json.loads(plugin_output.read_text(encoding="utf-8"))
    require(managed_commands(plugin) == [], "plugin mode must remove every project-managed registration")
    require({"node user-session.js", "node user-pre.js", "node user-prompt.js"} <= set(all_commands(plugin)), "plugin mutex merge lost user hooks")
    require(plugin.get("model") == "user-model" and plugin.get("permissions") == {"allow": ["Read"]}, "plugin mutex merge lost top-level settings")
    run_merge(plugin_output, disabled_template, plugin_again)
    require(plugin_output.read_bytes() == plugin_again.read_bytes(), "plugin mutex removal is not byte-idempotent")


def test_memory_rendering() -> None:
    template = (WB / "CODEBUDDY.md.tmpl").read_text(encoding="utf-8")
    cases = {
        "root CODEBUDDY + AGENTS": "@AGENTS.md",
        ".codebuddy CODEBUDDY + AGENTS": "@../AGENTS.md",
        "merge into AGENTS fallback": "",
        "new .codebuddy CODEBUDDY without AGENTS": "",
    }
    for label, import_line in cases.items():
        rendered = template.replace("{{OH_STORY_AGENTS_IMPORT}}", import_line)
        require("{{OH_STORY_AGENTS_IMPORT}}" not in rendered, f"{label}: unresolved memory placeholder")
        require(rendered.count("BEGIN oh-story-managed: workbuddy") == 1, f"{label}: duplicate BEGIN marker")
        require(rendered.count("END oh-story-managed: workbuddy") == 1, f"{label}: duplicate END marker")
        if import_line:
            begin = rendered.index("<!-- BEGIN oh-story-managed: workbuddy -->")
            heading = rendered.index("## 网文写作工具集", begin)
            require(import_line in rendered[begin:heading], f"{label}: wrong relative AGENTS import")
        else:
            require("@AGENTS.md" not in rendered and "@../AGENTS.md" not in rendered, f"{label}: self/shadow import residue")


def agent_frontmatter(path: Path) -> tuple[dict[str, str], str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\n(.*?)\n---\n(.*)\Z", text, re.DOTALL)
    require(match, f"agent card lacks closed frontmatter: {path}")
    assert match
    metadata: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if line and not line[0].isspace() and ":" in line:
            key, value = line.split(":", 1)
            metadata[key.strip()] = value.strip()
    return metadata, match.group(2)


def test_data_agent_pool_contract() -> None:
    base_cards = sorted((WB / "agents").glob("*.md"))
    physical_cards = sorted(DATA_AGENTS.glob("*.md"))
    logical_cards = sorted(DATA_ROLE_CARDS.glob("*.md"))
    require(len(base_cards) == 8, f"WorkBuddy base-card inventory drift: {len(base_cards)}")
    require(
        {path.stem for path in physical_cards} == {DATA_FETCHER, DATA_READONLY_RUNNER},
        f"WorkBuddy data physical pool drift: {[path.stem for path in physical_cards]}",
    )
    require(len(base_cards) + len(physical_cards) == 10, "WorkBuddy base physical pool must stay at 10")
    require(
        {path.stem for path in logical_cards} == DATA_READONLY_LOGICAL_ROLES,
        f"WorkBuddy data logical-card inventory drift: {[path.stem for path in logical_cards]}",
    )
    require(
        not (DATA_READONLY_LOGICAL_ROLES & {path.stem for path in physical_cards}),
        "readonly logical roles leaked back into the physical registry",
    )

    runner_metadata, runner_body = agent_frontmatter(DATA_AGENTS / f"{DATA_READONLY_RUNNER}.md")
    require(runner_metadata.get("name") == DATA_READONLY_RUNNER, "readonly runner name drift")
    require(runner_metadata.get("tools") == "Read, Glob, Grep", "readonly runner gained non-read tools")
    require(
        {part.strip() for part in runner_metadata.get("disallowedTools", "").split(",")}
        >= {"Write", "Edit", "Bash"},
        "readonly runner lost its write/shell deny boundary",
    )
    matches = re.findall(
        r"<!-- oh-story-logical-role-map: story-data-readonly-runner\n(\{.*?\})\n-->",
        runner_body,
        re.DOTALL,
    )
    require(len(matches) == 1, "runtime runner needs exactly one logical-role map")
    role_map = json.loads(matches[0])
    require(
        role_map == {role: DATA_READONLY_RUNNER for role in DATA_READONLY_LOGICAL_ROLES},
        f"runtime logical-role dispatch is not total/unique: {role_map}",
    )
    for required_field in ("logical_role", "logical_role_card_path", "project_abs_path", "task_contract"):
        require(f"`{required_field}`" in runner_body, f"readonly runner prompt wrapper lost {required_field}")

    role_root = DATA_ROLE_CARDS.resolve(strict=True)
    for role in sorted(DATA_READONLY_LOGICAL_ROLES):
        card = (role_root / f"{role}.md").resolve(strict=True)
        require(card.parent == role_root and card.name == f"{role}.md", f"role-card path escaped its fixed root: {card}")
        metadata, body = agent_frontmatter(card)
        require(metadata.get("name") == role, f"logical role identity drift: {card}")
        require(metadata.get("tools") == "Read, Glob, Grep", f"logical role gained non-read tools: {role}")
        require(
            {part.strip() for part in metadata.get("disallowedTools", "").split(",")}
            >= {"Write", "Edit", "Bash"},
            f"logical role lost its write/shell deny boundary: {role}",
        )
        require(f"workbuddy-role-card/{role}" in body, f"logical role-card marker drift: {role}")
        require(DATA_READONLY_RUNNER in body, f"logical role-card lost runner ownership: {role}")


def main() -> int:
    print("WorkBuddy runtime synthetic tests")
    print("=================================")
    with tempfile.TemporaryDirectory(prefix="oh-story-workbuddy-") as temporary:
        temp = Path(temporary)
        version_project = temp / "versions"
        version_project.mkdir()
        test_versions(version_project)
        print("  OK SessionStart agents_version invalid/<39/=39/>39 cases")
        test_plugin_runner_ignores_project_sentinel(temp / "plugin-sentinel")
        print("  OK plugin-only runner ignores non-WorkBuddy/stale project sentinel diagnostics")
        test_project_hook_commands_cross_shell(temp / "cross-shell")
        print("  OK four project Hooks use POSIX sh / Git for Windows Bash and accept native + /c/... paths")
        test_guarded_outer_fail_closed(temp)
        print("  OK guarded malformed-input and injected fs/root failures are fail-closed")

        test_historical_copy_discovery(temp / "historical")
        print("  OK historical 备份/归档/archive copies are excluded from active/all-book discovery")
        test_removed_target_runner_gate(temp / "removed-target")
        print("  OK stale project runner yields silently after target_cli removes workbuddy")

        hook_project = temp / "hooks"
        hook_project.mkdir()
        test_powershell(hook_project)
        print("  OK PowerShell static target fixtures, deny gate and post-write scan")
        test_commit_system_message(hook_project)
        print("  OK commit advisory top-level systemMessage")

        test_hook_mutex(temp / "mutex")
        print("  OK project registration vs plugin removal mutex and idempotence")
        test_memory_rendering()
        print("  OK CODEBUDDY/AGENTS conditional-import rendering")
        test_data_agent_pool_contract()
        print("  OK 10-card physical pool + four-role readonly runtime dispatch")

    print("\nOK: WorkBuddy runtime synthetic tests passed")
    return 0


if __name__ == "__main__":
    configure_utf8_stdio()
    try:
        raise SystemExit(main())
    except (AssertionError, json.JSONDecodeError, OSError, subprocess.CalledProcessError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
