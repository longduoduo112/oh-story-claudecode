#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PYBIN=""
for candidate in python3 python py; do
  if "$candidate" -c "" >/dev/null 2>&1; then
    PYBIN="$candidate"
    break
  fi
done
if [ -z "$PYBIN" ]; then
  echo "Error: Python 3 is required for the TRAE runtime routing check" >&2
  exit 1
fi

"$PYBIN" - <<'PY'
from pathlib import Path
import re

root = Path.cwd()

agent_runtime_skills = [
    "skills/story/SKILL.md",
    "skills/story-long-analyze/SKILL.md",
    "skills/story-long-write/SKILL.md",
    "skills/story-short-write/SKILL.md",
    "skills/story-import/SKILL.md",
    "skills/story-deslop/SKILL.md",
    "skills/story-review/SKILL.md",
    "skills/story-explore/SKILL.md",
    "skills/story-research/SKILL.md",
    "skills/story-data-analyze/SKILL.md",
]

all_story_skills = sorted(
    path for path in (root / "skills").glob("*/SKILL.md")
    if path.parent.name == "browser-cdp" or path.parent.name.startswith("story")
)

errors: list[str] = []
for rel in agent_runtime_skills:
    path = root / rel
    text = path.read_text(encoding="utf-8")
    if ".trae/agents" not in text:
        errors.append(f"{rel}: missing TRAE project-agent detection")
    if "TRAE Code" not in text:
        errors.append(f"{rel}: missing explicit TRAE runtime semantics")
    if re.search(r"内置\s*`?Agent`?\s*工具", text):
        errors.append(
            f"{rel}: calls TRAE's built-in Agent an Agent tool; it is the host agent "
            "that invokes Subagents, not a Claude-style tool API"
        )
    if not re.search(r"内置\s*(?:\*\*|`)?Agent(?:\*\*|`)?\s*智能体", text):
        errors.append(f"{rel}: missing built-in Agent host requirement for TRAE Subagents")

for path in all_story_skills:
    rel = path.relative_to(root)
    text = path.read_text(encoding="utf-8")
    if "或检测到 `.zcode/`" in text:
        errors.append(
            f"{rel}: mere .zcode/ coexistence disables other runtimes; fallback must require "
            "that the current runtime is ZCode"
        )
    lines = text.splitlines()
    for lineno, line in enumerate(lines, 1):
        nearby = "\n".join(lines[max(0, lineno - 3):lineno])
        platform_scoped_example = (
            "WorkBuddy" in line
            or ("Claude" in line and "TRAE" in line)
            or (
                "只是 Claude" in nearby
                and "TRAE Code" in nearby
                and "不传 `subagent_type`" in nearby
            )
        )
        if re.search(r"spawn\s+`Agent\(subagent_type", line) and not platform_scoped_example:
            errors.append(
                f"{rel}:{lineno}: active Claude-only Agent(subagent_type) invocation; "
                "use a platform-neutral call contract and map TRAE to a named Subagent"
            )
        if (
            rel.as_posix() in {
                "skills/story-long-write/SKILL.md",
                "skills/story-short-write/SKILL.md",
            }
            and "Agent(subagent_type" in line
            and not re.search(r"spawn\s+`Agent\(subagent_type", line)
            and not platform_scoped_example
        ):
            errors.append(
                f"{rel}:{lineno}: call-shaped Agent(subagent_type) example remains in a shared "
                "writing workflow; express the task platform-neutrally, then map TRAE to the "
                "built-in Agent host and WorkBuddy/Claude to their documented invocation"
            )
        if ("AskUserQuestion" in line or "WebFetch" in line) and "TRAE" not in line and "Claude" in line:
            errors.append(
                f"{rel}:{lineno}: Claude-only capability is not paired with TRAE behavior on the same instruction"
            )

review = (root / "skills/story-review/SKILL.md").read_text(encoding="utf-8")
for required in (
    "TRAE Code agent（`.trae/agents/`）",
    "`tools` / `disallowedTools` 如存在必须是逗号分隔字符串",
):
    if required not in review:
        errors.append(f"skills/story-review/SKILL.md: missing TRAE validation clause {required!r}")

long_analyze = (root / "skills/story-long-analyze/SKILL.md").read_text(encoding="utf-8")
if "不得把 `subagent_type` 或 `model` 当成 TRAE 参数" not in long_analyze:
    errors.append("skills/story-long-analyze/SKILL.md: missing TRAE chapter-extractor invocation mapping")

data_skill = (root / "skills/story-data-analyze/SKILL.md").read_text(encoding="utf-8")
for role in (
    "story-data-fetcher",
    "story-data-metrics-analyst",
    "story-data-method-validator",
    "story-data-text-improvement-planner",
    "story-data-supervisor",
):
    if role not in data_skill:
        errors.append(f"skills/story-data-analyze/SKILL.md: missing data role {role}")

allowed_tools = {
    "Bash", "Edit", "Glob", "Grep", "Read", "Skill", "TodoWrite",
    "WebFetch", "WebSearch", "Write", "LSP",
}
agent_dir = root / "skills/story-data-analyze/agents/trae"
agent_paths = sorted(agent_dir.glob("*.md"))
expected_data_agents = {
    "story-data-fetcher",
    "story-data-metrics-analyst",
    "story-data-method-validator",
    "story-data-text-improvement-planner",
    "story-data-supervisor",
}
actual_data_agents = {path.stem for path in agent_paths}
if actual_data_agents != expected_data_agents:
    errors.append(
        f"{agent_dir.relative_to(root)}: exact TRAE data roster drift; "
        f"missing={sorted(expected_data_agents - actual_data_agents)}, "
        f"extra={sorted(actual_data_agents - expected_data_agents)}"
    )

role_contract_markers = {
    "story-data-fetcher": ("handoff.raw_capture", "handoff.sample_gate_observation", "UNATTESTED_PROCEDURAL"),
    "story-data-metrics-analyst": ("handoff.analysis", "change_assessments", "sample_size_qualified"),
    "story-data-method-validator": ("handoff.validation", "independent_recalculation", "PASS|RETURN|BLOCKED"),
    "story-data-text-improvement-planner": ("handoff.text_diagnosis", "single_variable=true", "automatic_execution=false"),
    "story-data-supervisor": ("handoff.supervision", "G1–G8", "novel_edits_made=false"),
}

for path in agent_paths:
    rel = path.relative_to(root)
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\n(.*?)\n---\n(.+)\Z", text, re.S)
    if not match:
        errors.append(f"{rel}: missing closed YAML frontmatter")
        continue
    raw_frontmatter, body = match.groups()
    meta: dict[str, str] = {}
    for line in raw_frontmatter.splitlines():
        if not line.strip():
            continue
        if ":" not in line:
            errors.append(f"{rel}: malformed frontmatter line {line!r}")
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip()

    if meta.get("name") != path.stem:
        errors.append(f"{rel}: name must equal filename stem")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,48}[A-Za-z0-9]|[A-Za-z]", path.stem):
        errors.append(f"{rel}: name violates TRAE's 50-character Subagent identifier contract")
    if not meta.get("description"):
        errors.append(f"{rel}: missing description")
    if not body.strip():
        errors.append(f"{rel}: empty role body")

    for field in ("tools", "disallowedTools"):
        value = meta.get(field, "")
        if not value:
            errors.append(f"{rel}: missing {field} comma string")
            continue
        if value.startswith("[") or value.startswith("{"):
            errors.append(f"{rel}: {field} must be a comma string, not YAML collection")
            continue
        names = [part.strip() for part in value.split(",") if part.strip()]
        unknown = sorted(set(names) - allowed_tools)
        if unknown:
            errors.append(f"{rel}: unsupported TRAE tools in {field}: {', '.join(unknown)}")

    parsed_tools = {part.strip() for part in meta.get("tools", "").split(",") if part.strip()}
    parsed_denied = {part.strip() for part in meta.get("disallowedTools", "").split(",") if part.strip()}
    if path.stem == "story-data-fetcher":
        if parsed_tools != {"Bash", "Read", "Glob", "Grep"}:
            errors.append(f"{rel}: fetcher tools must be Bash, Read, Glob, Grep")
        if not {"Write", "Edit", "WebFetch", "WebSearch"}.issubset(parsed_denied):
            errors.append(f"{rel}: fetcher must deny direct writes and ad-hoc web tools")
    else:
        if parsed_tools != {"Read", "Glob", "Grep"}:
            errors.append(f"{rel}: read-only role tools must be Read, Glob, Grep")
        if not {"Write", "Edit", "Bash"}.issubset(parsed_denied):
            errors.append(f"{rel}: read-only role must deny Write, Edit, Bash")

    unsupported_fields = sorted(set(meta) & {"model", "maxTurns", "memory", "skills", "permission", "mode"})
    if unsupported_fields:
        errors.append(f"{rel}: unsupported TRAE frontmatter fields: {', '.join(unsupported_fields)}")
    if ".claude/skills" in text:
        errors.append(f"{rel}: hard-coded Claude skill root")
    if re.search(r"内置\s*`?Agent`?\s*工具", text):
        errors.append(f"{rel}: calls TRAE's built-in Agent host a tool")
    for marker in role_contract_markers.get(path.stem, ()):
        if marker not in body:
            errors.append(f"{rel}: missing role-contract marker {marker!r}")

# Claude-only tool names may remain in compatibility explanations, but must never
# remain as the active imperative in runtime skills.
for path in (root / "skills").glob("**/*.md"):
    if "story-setup" in path.parts:
        continue
    text = path.read_text(encoding="utf-8")
    if ".claude/skills" in text and ".trae/skills" not in text:
        errors.append(f"{path.relative_to(root)}: Claude skill root is not paired with a TRAE/native resolution path")
    for bad in ("用 AskUserQuestion", "用 WebFetch"):
        if bad in text:
            errors.append(f"{path.relative_to(root)}: active Claude-only instruction remains: {bad}")

if errors:
    print("TRAE runtime routing check failed:")
    for error in errors:
        print(f"  - {error}")
    raise SystemExit(1)

print(
    f"OK: {len(all_story_skills)} story skills "
    f"({len(agent_runtime_skills)} agent-routing contracts) and "
    f"{len(agent_paths)} TRAE data agents validated"
)
PY
