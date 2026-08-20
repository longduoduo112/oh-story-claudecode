#!/usr/bin/env python3
"""锁定中文交付正文的跨文档、多适配器和 Hook 语义。

这个门不复制检测算法，只防止发布包中各入口漂移回彼此矛盾的契约：
standalone language gate 是第一关；只机械保护明确非叙事结构；其他外语
必须经用户单独确认并精确登记；HTML 阻断；正文内跳过标记不得改变 Hook。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []


def read(relative: str) -> str:
    path = ROOT / relative
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        ERRORS.append(f"{relative}: 无法读取：{error}")
        return ""


def require(relative: str, text: str, needle: str, label: str) -> None:
    if needle not in text:
        ERRORS.append(f"{relative}: 缺少{label}：{needle}")


CORE_DOCS = (
    "skills/story-long-write/SKILL.md",
    "skills/story-long-write/references/workflow-daily.md",
    "skills/story-short-write/SKILL.md",
    "skills/story-review/SKILL.md",
    "skills/story-deslop/SKILL.md",
    "skills/story-deslop/references/language-gate-loop.md",
    "skills/story-setup/references/templates/agents/narrative-writer.md",
    "skills/story-setup/references/templates/rules/story-format.md",
    "skills/story-setup/UPGRADING.md",
)

ADAPTER_DOCS = (
    "skills/story-setup/references/templates/CLAUDE.md.tmpl",
    "skills/story-setup/references/opencode/AGENTS.md.tmpl",
    "skills/story-setup/references/codex/AGENTS.md.tmpl",
    "skills/story-setup/references/zcode/AGENTS.md.tmpl",
    "skills/story-setup/references/generic/AGENTS.md.tmpl",
    "skills/story-setup/references/openclaw/AGENTS.md.tmpl",
    "skills/story-setup/references/reasonix/AGENTS.md.tmpl",
)

ACTUAL_COMMAND_DOCS = (
    "skills/story-setup/references/codex/AGENTS.md.tmpl",
    "skills/story-setup/references/zcode/AGENTS.md.tmpl",
    "skills/story-setup/references/generic/AGENTS.md.tmpl",
    "skills/story-setup/references/openclaw/AGENTS.md.tmpl",
    "skills/story-setup/references/reasonix/AGENTS.md.tmpl",
)

HOOK_FILES = (
    "skills/story-setup/references/templates/hooks/story_hook_core.js",
    "skills/story-setup/references/codex/hooks/story_codex_hook.py",
    "skills/story-setup/references/templates/hooks/guard-outline-before-prose.sh",
    "skills/story-setup/references/templates/hooks/story_hook_cli.js",
)

PUBLIC_DOCS = (
    "README.md",
    "README_EN.md",
    "CHANGELOG.md",
)


def main() -> int:
    for relative in CORE_DOCS:
        text = read(relative)
        for needle, label in (
            ("language_gate.js", "standalone language gate"),
            (".deslop-whitelist", "精确白名单契约"),
            ("HTML", "HTML 阻断契约"),
            ("非叙事", "非叙事结构边界"),
        ):
            require(relative, text, needle, label)
        if not re.search(r"用户[^\n]{0,24}(?:单独|明确)确认|单独确认[^\n]{0,24}用户", text):
            ERRORS.append(f"{relative}: 缺少用户单独确认后才能白名单登记的契约")

    for relative in ADAPTER_DOCS:
        text = read(relative)
        for needle, label in (
            ("language_gate.js", "standalone language gate"),
            (".deslop-whitelist", "精确白名单契约"),
            ("用户单独确认", "用户确认契约"),
            ("HTML", "HTML 阻断契约"),
            ("非叙事", "非叙事结构边界"),
        ):
            require(relative, text, needle, label)

    readme = read("README.md")
    for needle, label in (
        ("language_gate.js", "standalone language gate"),
        (".deslop-whitelist", "精确白名单契约"),
        ("非叙事结构", "非叙事结构边界"),
        ("HTML", "HTML 阻断契约"),
    ):
        require("README.md", readme, needle, label)

    readme_en = read("README_EN.md")
    for needle, label in (
        ("standalone `language_gate.js`", "standalone language gate"),
        ("separate, explicit user confirmation", "explicit user confirmation"),
        ("clearly non-narrative structures", "non-narrative structure boundary"),
        ("HTML tags, comments, and entities are blocking", "HTML blocking contract"),
    ):
        require("README_EN.md", readme_en, needle, label)

    command = 'node "{当前写作 Skill 目录}/scripts/language_gate.js" "{正文文件}"'
    for relative in ACTUAL_COMMAND_DOCS:
        require(relative, read(relative), command, "可实际执行的第一关命令")

    ordered_docs = (
        "skills/story-long-write/references/workflow-daily.md",
        "skills/story-short-write/SKILL.md",
        "skills/story-deslop/SKILL.md",
        "skills/story-setup/references/templates/agents/narrative-writer.md",
        "skills/story-setup/references/generic/AGENTS.md.tmpl",
        "skills/story-setup/references/openclaw/AGENTS.md.tmpl",
        "skills/story-setup/references/reasonix/AGENTS.md.tmpl",
    )
    for relative in ordered_docs:
        text = read(relative)
        gate = text.find("language_gate.js")
        downstream = [position for token in ("check-ai-patterns.js", "check-degeneration.js") if (position := text.find(token)) >= 0]
        if gate < 0 or not downstream or gate >= min(downstream):
            ERRORS.append(f"{relative}: standalone language gate 必须在 AI/退化检查之前首次出现")

    stale_patterns = (
        r"中文正稿不接受外语白名单",
        r"\.\./story-deslop/scripts/language_gate\.js",
    )
    for relative in CORE_DOCS + ADAPTER_DOCS:
        text = read(relative)
        for pattern in stale_patterns:
            if re.search(pattern, text):
                ERRORS.append(f"{relative}: 命中已废止契约 /{pattern}/")

    for relative in HOOK_FILES:
        text = read(relative)
        if re.search(r"去味(?:：|:)跳过", text):
            ERRORS.append(f"{relative}: Hook 仍会识别正文内去味跳过标记")
        require(relative, text, "HTML", "HTML 阻断/无正文标记契约")

    for relative in PUBLIC_DOCS:
        text = read(relative)
        if re.search(r"去味(?:：|:)跳过", text):
            ERRORS.append(f"{relative}: 公开文档仍展示已废止的正文跳过标记")

    js_hook = read("skills/story-setup/references/templates/hooks/story_hook_core.js")
    py_hook = read("skills/story-setup/references/codex/hooks/story_codex_hook.py")
    for stale in (
        "[A-Z][a-z]{2,}[ \\t]+[a-z][ \\t]+\\d{1,4}",
        "(?:[A-Z][、,，/／]){1,}[A-Z]",
    ):
        if stale in js_hook or stale in py_hook:
            ERRORS.append(f"Hook 仍包含叙事缩写/型号自动遮罩：{stale}")

    setup_cards = read("skills/story-setup/references/agent-references/genre-prose-cards.md")
    if "genre-contracts/" in setup_cards or "genre-contracts.md" in setup_cards:
        ERRORS.append("story-setup agent-references/genre-prose-cards.md 仍悬空引用 long-write 自有契约")

    if ERRORS:
        print("FAIL: Chinese prose contract drift", file=sys.stderr)
        for error in ERRORS:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print("OK: Chinese prose docs, adapters, gates, Hooks, and setup mirrors share one contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
