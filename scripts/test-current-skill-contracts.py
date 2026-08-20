#!/usr/bin/env python3
"""Focused regressions for the structured current-contract validator."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
MODULE_PATH = SCRIPT_DIR / "check-current-skill-contracts.py"
SPEC = importlib.util.spec_from_file_location("current_contract_validator", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
VALIDATOR = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VALIDATOR
SPEC.loader.exec_module(VALIDATOR)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def finding_codes(findings: list[object]) -> set[str]:
    return {finding.code for finding in findings}


def repository_manifest() -> object:
    manifest, findings = VALIDATOR.load_manifest(SCRIPT_DIR / "current-contract.json")
    require(not findings and manifest is not None, "repository manifest must load")
    return manifest


def manifest_with(**overrides: object) -> object:
    """按正常加载路径构造一个改过值的当前契约，用来演练 bump。"""
    raw = json.loads((SCRIPT_DIR / "current-contract.json").read_text(encoding="utf-8"))
    raw.update(overrides)
    with tempfile.TemporaryDirectory() as tmp:
        bumped_path = Path(tmp) / "bumped.json"
        bumped_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        manifest, findings = VALIDATOR.load_manifest(bumped_path)
    require(not findings and manifest is not None, "bumped manifest must stay well-formed")
    return manifest


def flagged_paths(manifest: object, code: str) -> set[str]:
    return {
        finding.path.relative_to(REPO_ROOT).as_posix()
        for finding in VALIDATOR.validate_repository(REPO_ROOT, manifest)
        if finding.code == code and finding.path is not None
    }


def test_manifest_contract() -> None:
    manifest_path = SCRIPT_DIR / "current-contract.json"
    manifest, findings = VALIDATOR.load_manifest(manifest_path)
    require(not findings, "repository manifest should validate: {}".format(findings))
    require(manifest is not None, "repository manifest should load")
    require(not VALIDATOR.validate_repository(REPO_ROOT, manifest), "manifest and repository must agree")

    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)

        wrong_type = dict(raw)
        wrong_type["agents_version"] = "18"
        wrong_type_path = tmpdir / "wrong-type.json"
        wrong_type_path.write_text(json.dumps(wrong_type, ensure_ascii=False), encoding="utf-8")
        _, wrong_type_findings = VALIDATOR.load_manifest(wrong_type_path)
        require(
            "manifest-value-type" in finding_codes(wrong_type_findings),
            "string agents_version must be rejected",
        )

        stale = dict(raw)
        stale["topic_decision_phase"] = 4
        stale_path = tmpdir / "stale.json"
        stale_path.write_text(json.dumps(stale, ensure_ascii=False), encoding="utf-8")
        stale_manifest, stale_findings = VALIDATOR.load_manifest(stale_path)
        require(
            not stale_findings and stale_manifest is not None,
            "a well-formed manifest remains the source of truth",
        )
        require(
            "topic-decision-phase" in finding_codes(
                VALIDATOR.validate_repository(REPO_ROOT, stale_manifest)
            ),
            "repository drift from the manifest must be rejected",
        )

        malformed_sections = dict(raw)
        malformed_sections["required_outline_sections"] = [{"rule": "阶段位置"}]
        malformed_path = tmpdir / "malformed-sections.json"
        malformed_path.write_text(json.dumps(malformed_sections, ensure_ascii=False), encoding="utf-8")
        _, malformed_findings = VALIDATOR.load_manifest(malformed_path)
        require(
            "manifest-outline-type" in finding_codes(malformed_findings),
            "incomplete outline-section objects must be rejected",
        )

        duplicate_artifacts = dict(raw)
        duplicate_artifacts["primary_benchmark_artifacts"] = ["剧情/节奏.md", "剧情/节奏.md"]
        duplicate_path = tmpdir / "duplicate-artifacts.json"
        duplicate_path.write_text(json.dumps(duplicate_artifacts, ensure_ascii=False), encoding="utf-8")
        _, duplicate_findings = VALIDATOR.load_manifest(duplicate_path)
        require(
            "manifest-artifact-duplicate" in finding_codes(duplicate_findings),
            "duplicate primary artifacts must be rejected",
        )

        renamed_artifacts = dict(raw)
        renamed_artifacts["primary_benchmark_artifacts"] = [
            "剧情/主情绪.md",
            "剧情/主节奏.md",
        ]
        renamed_path = tmpdir / "renamed-artifacts.json"
        renamed_path.write_text(
            json.dumps(renamed_artifacts, ensure_ascii=False), encoding="utf-8"
        )
        renamed_manifest, renamed_findings = VALIDATOR.load_manifest(renamed_path)
        require(
            not renamed_findings and renamed_manifest is not None,
            "renamed current artifacts must remain manifest-driven",
        )
        renamed_semantic = semantic_findings(
            "- 若 `剧情/主节奏.md` 缺失，回退读取 `拆文报告.md`。",
            renamed_manifest.primary_benchmark_artifacts,
        )
        require(
            "silent-primary-artifact-fallback" in finding_codes(renamed_semantic),
            "semantic guard must follow renamed manifest artifacts",
        )


def semantic_findings(
    text: str, primary_artifacts: tuple[str, ...] | None = None
) -> list[object]:
    if primary_artifacts is None:
        primary_artifacts = repository_manifest().primary_benchmark_artifacts
    return VALIDATOR.semantic_primary_fallback_findings(
        text,
        Path("fixture.md"),
        primary_artifacts,
    )


def test_marketplace_skill_version_guard() -> None:
    """A separately bumped Skill must not leave stale Claude install metadata."""

    with tempfile.TemporaryDirectory(prefix="marketplace-skill-version-") as tmp:
        root = Path(tmp)
        skill_dir = root / "skills/story-review"
        marketplace_dir = root / ".claude-plugin"
        skill_dir.mkdir(parents=True)
        marketplace_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: story-review\nversion: 1.1.1\n---\n",
            encoding="utf-8",
        )
        marketplace = marketplace_dir / "marketplace.json"
        marketplace.write_text(
            json.dumps({"plugins": [{"name": "story-review", "version": "1.1.0"}]}),
            encoding="utf-8",
        )
        stale = VALIDATOR.marketplace_skill_version_findings(root, "story-review")
        require(
            "marketplace-skill-version" in finding_codes(stale),
            "stale story-review marketplace metadata must fail",
        )

        marketplace.write_text(
            json.dumps({"plugins": [{"name": "story-review", "version": "1.1.1"}]}),
            encoding="utf-8",
        )
        require(
            not VALIDATOR.marketplace_skill_version_findings(root, "story-review"),
            "matching story-review marketplace metadata must pass",
        )


def test_bad_fallbacks_fail() -> None:
    bad_cases = {
        "inline report fallback": "- 若 `剧情/情绪模块.md` 缺失，回退读取 `拆文报告.md`。",
        "nested summary substitution": """
1. 检查 `剧情/节奏.md`。
2. 任一主产物缺失时：
   - 使用 `章节/*_摘要.md` 代替。
""",
        "structured gap story fallback": "- `rhythm_missing: true` 时改用 `故事线.md` 补足节奏。",
    }
    for label, text in bad_cases.items():
        findings = semantic_findings(text)
        require(
            "silent-primary-artifact-fallback" in finding_codes(findings),
            "{} should fail".format(label),
        )


def test_fail_fast_prose_passes() -> None:
    good_cases = {
        "explicit不得": "- `剧情/情绪模块.md` 缺失时必须停止；不得以 `拆文报告.md`、章节摘要或故事线代替。",
        "explicit禁止 fallback": "- `rhythm_missing: true` 时返回 `missing_primary_contract`，禁止 fallback 到 `故事线.md`。",
        "normal complete branch": "- 两个主产物都存在时读取 `拆文报告.md`，仅作人类可读概览。",
        "deep-dive fallback is not primary fallback": (
            "- 先读 `剧情/情绪模块.md` 与 `剧情/节奏.md`；模块或节奏文件缺失时停止修复。"
            "匹配 `章节/*_摘要.md` 后，若同章深度拆解不存在，则回退黄金三章深度拆解。"
        ),
    }
    for label, text in good_cases.items():
        findings = semantic_findings(text)
        require(not findings, "{} should pass, got {}".format(label, findings))


def test_sibling_bullets_do_not_lend_the_missing_condition() -> None:
    """相邻条目各自是独立契约：fail-fast 兄弟条目不得把「主产物缺失」借给正确的读取条目。"""
    fail_fast = "- `剧情/节奏.md` → 缺失时停止导入，不得以 `拆文报告.md`、章节摘要或故事线代替"
    good_neighbours = {
        "benign read after a fail-fast sibling": "- 两个主产物都存在时读取 `拆文报告.md`，仅作人类可读概览。",
        "human-readable overview bullet": "- 故事线（人类可读概览）→ 从 `剧情/故事线.md` 读取；缺失时留空",
        "prose block after a fail-fast bullet": "**无损检查**（任一不过即删除 `_章节摘要汇总.md`、回退逐文件扫描）：",
    }
    for label, good in good_neighbours.items():
        findings = semantic_findings(fail_fast + "\n" + good + "\n")
        require(not findings, "{} should pass, got {}".format(label, findings))

    nested = (
        "任一主产物缺失时：\n"
        "- 先记录到追踪\n"
        "- 再确认块状态\n"
        "- 回退读取 `拆文报告.md` 拼出对标视图\n"
    )
    require(
        "silent-primary-artifact-fallback" in finding_codes(semantic_findings(nested)),
        "上级条目给出的缺失条件必须仍然拦住降级子项",
    )
    deep = "- 主产物缺失时：\n  - 导入分支：\n    - 采用 `故事线.md` 顶替。\n"
    require(
        "silent-primary-artifact-fallback" in finding_codes(semantic_findings(deep)),
        "隔了一层的上级条件也要拦住降级子项",
    )
    wrapped = "- 若 `剧情/节奏.md` 缺失，\n  则改读 `章节/*_摘要.md` 补足节奏。\n"
    require(
        "silent-primary-artifact-fallback" in finding_codes(semantic_findings(wrapped)),
        "同一条目的续行仍与条件同属一件事",
    )
    table_rows = (
        "| 条件 | 行为 |\n"
        "|---|---|\n"
        "| `剧情/节奏.md` 缺失 | 停止 Stage 6 并报 `missing_primary_contract` |\n"
        "| `章节/第1-3章_深度拆解.md` 缺失 | 对话潜台词段从拆文报告兜底 |\n"
    )
    require(
        not semantic_findings(table_rows),
        "表格里相邻行是独立记录，深度拆解兜底不是主产物降级：{}".format(
            semantic_findings(table_rows)
        ),
    )
    bad_row = (
        "| 条件 | 行为 |\n"
        "|---|---|\n"
        "| `剧情/节奏.md` 缺失 | 回退读取 `拆文报告.md` 补足节奏 |\n"
    )
    require(
        "silent-primary-artifact-fallback" in finding_codes(semantic_findings(bad_row)),
        "同一表格行内的主产物降级必须拦住",
    )


def test_undecodable_markdown_is_a_named_failure() -> None:
    """非 UTF-8 文本会让所有内容规则静默放行，必须命名报错；二进制资产照旧跳过。"""
    rule = next(
        r for r in VALIDATOR.LEGACY_RULES if r.code == "legacy-progress-branch"
    )
    legacy = "# 流程说明\n\n禁止残留：legacy_deconstruction\n"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills = root / "skills"
        skills.mkdir()
        target = skills / "流程说明.md"
        target.write_text(legacy, encoding="utf-8")
        require(
            VALIDATOR.check_absent_rule(root, rule),
            "UTF-8 的旧进度分支必须被内容规则拦住",
        )
        target.write_bytes(legacy.encode("gb18030"))
        require(
            not VALIDATOR.check_absent_rule(root, rule),
            "内容规则读不出 GBK 文件，这正是需要专门扫描的原因",
        )
        require(
            "unreadable-source-file"
            in finding_codes(VALIDATOR.undecodable_source_findings([skills])),
            "非 UTF-8 的契约文本必须是命名失败，不能静默跳过",
        )
        target.write_text(legacy, encoding="utf-16")
        require(
            "unreadable-source-file"
            in finding_codes(VALIDATOR.undecodable_source_findings([skills])),
            "UTF-16 Markdown 含 NUL，但仍是契约文本，不能伪装成二进制资产跳过",
        )
        target.write_text(legacy, encoding="utf-8")
        (skills / "封面.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe")
        # 无后缀 / 非白名单后缀的二进制（.DS_Store 之类）靠 NUL 字节识别，不能误报
        (skills / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1\xff\xfe")
        require(
            not VALIDATOR.undecodable_source_findings([skills]),
            "二进制资产不是契约文本，必须保持静默：{}".format(
                VALIDATOR.undecodable_source_findings([skills])
            ),
        )


def test_progress_schema_pins_are_repo_wide() -> None:
    """bump progress_schema_version 时，每个字面锚点都要被点名，不能只点 pipeline-ops.md。"""
    current = repository_manifest().progress_schema_version
    stale = flagged_paths(
        manifest_with(progress_schema_version=current + 1), "progress-schema-version"
    )
    for relative in (
        "skills/story-long-analyze/references/pipeline-ops.md",
        "skills/story-long-analyze/SKILL.md",
        "skills/story-import/SKILL.md",
        "skills/story-setup/UPGRADING.md",
    ):
        require(
            relative in stale,
            "{} 的 schema_version 锚点必须跟着 manifest 走，实际命中 {}".format(
                relative, sorted(stale)
            ),
        )
    require(
        "CHANGELOG.md" not in stale,
        "CHANGELOG 的历史记录不受当前值约束",
    )


def test_stale_scan_phase_reference_accepts_backticks() -> None:
    """房子风格 `story-long-scan` Phase N 与裸 token 写法都要被 stale 引用扫描抓到。"""
    current = repository_manifest().topic_decision_phase
    stale = flagged_paths(
        manifest_with(topic_decision_phase=current + 1),
        "stale-topic-decision-phase-reference",
    )
    # 长篇「先查选题决策」随 Phase 1 搬进 workflow-setup.md，扫描目标跟着内容走。
    for relative in (
        "skills/story-long-write/references/workflow-setup.md",
        "skills/story-long-analyze/SKILL.md",
    ):
        require(
            relative in stale,
            "{} 的选题决策阶段引用必须被扫到，实际命中 {}".format(relative, sorted(stale)),
        )


def test_structured_sentinel_contract() -> None:
    manifest = repository_manifest()
    scattered = """
agents_version: {agents_version}
setup_skill_version: {setup_skill_version}
说明文字中还提到了 target_cli、resolver_strategy 与 references_dir。
""".format(
        agents_version=manifest.agents_version,
        setup_skill_version=manifest.setup_skill_version,
    )
    require(
        VALIDATOR.extract_sentinel_fields(scattered) is None,
        "scattered sentinel tokens must not satisfy the deployment block",
    )
    require(
        "setup-sentinel-block"
        in finding_codes(
            VALIDATOR.sentinel_contract_findings(
                scattered, manifest, Path("fixture.md")
            )
        ),
        "missing structured sentinel block must fail",
    )

    structured = """
### Step 8：创建部署标记

- 写入以下字段：

```yaml
deployed_at: 2026-07-14T00:00:00Z
agents_version: {agents_version}
setup_skill_version: {setup_skill_version}
target_cli: codex
resolver_strategy: project-first
references_dir: .codex/skills/story-setup/references
```
""".format(
        agents_version=manifest.agents_version,
        setup_skill_version=manifest.setup_skill_version,
    )
    require(
        not VALIDATOR.sentinel_contract_findings(
            structured, manifest, Path("fixture.md")
        ),
        "well-formed structured sentinel must pass",
    )

    incomplete = structured.replace("target_cli: codex\n", "")
    require(
        "setup-sentinel-fields"
        in finding_codes(
            VALIDATOR.sentinel_contract_findings(
                incomplete, manifest, Path("fixture.md")
            )
        ),
        "missing generated sentinel fields must fail",
    )


def test_structured_outline_contract() -> None:
    manifest = repository_manifest()
    rule_names = [rule for rule, _ in manifest.required_outline_sections]
    outline_names = [outline for _, outline in manifest.required_outline_sections]

    scattered_rule = "2. **细纲必填项**\n\n" + "、".join(rule_names)
    require(
        "outline-rule-section"
        in finding_codes(
            VALIDATOR.outline_rule_contract_findings(
                scattered_rule, manifest, Path("rule.md")
            )
        ),
        "outline names scattered in prose must not satisfy structured rules",
    )
    structured_rule = (
        "2. **细纲必填项**\n"
        + "\n".join("- {}：必填".format(name) for name in rule_names)
        + "\n3. **下一条规则**\n"
    )
    require(
        not VALIDATOR.outline_rule_contract_findings(
            structured_rule, manifest, Path("rule.md")
        ),
        "structured outline rule fields must pass",
    )

    scattered_outline = "本章应包含：" + "、".join(outline_names)
    declared = VALIDATOR.extract_produced_outline_fields(scattered_outline)
    require(
        not set(outline_names).issubset(declared),
        "outline names scattered in prose must not count as declared sections",
    )
    structured_outline = "\n".join("## {}".format(name) for name in outline_names)
    require(
        set(outline_names).issubset(
            VALIDATOR.extract_produced_outline_fields(structured_outline)
        ),
        "structured outline headings must be recognized",
    )


def test_upgrading_version_contract() -> None:
    manifest = repository_manifest()
    structured = """
## 当前版本

- `setup_skill_version: {setup_skill_version}`
- `agents_version: {agents_version}`

## 下一节
""".format(
        setup_skill_version=manifest.setup_skill_version,
        agents_version=manifest.agents_version,
    )
    require(
        not VALIDATOR.upgrading_version_findings(
            structured, manifest, Path("UPGRADING.md")
        ),
        "structured current-version bullets must pass",
    )
    scattered = (
        "说明 setup_skill_version: {}，agents_version: {}，但没有当前版本字段。".format(
            manifest.setup_skill_version, manifest.agents_version
        )
    )
    require(
        "upgrading-current-version"
        in finding_codes(
            VALIDATOR.upgrading_version_findings(
                scattered, manifest, Path("UPGRADING.md")
            )
        ),
        "version strings scattered in prose must not satisfy current-version bullets",
    )


def test_deeply_nested_fallback_keeps_all_governing_ancestors() -> None:
    text = (
        "- `剧情/节奏.md` 缺失时：\n"
        "  - 导入阶段：\n"
        "    - 第六阶段：\n"
        "      - 对标视图：\n"
        "        - 回退读取 `拆文报告.md` 拼出节奏。\n"
    )
    found = VALIDATOR.semantic_primary_fallback_findings(
        text,
        Path("deeply-nested.md"),
        ("剧情/节奏.md",),
    )
    require(
        "silent-primary-artifact-fallback" in finding_codes(found),
        "深层列表的主产物缺失条件必须一路传到回退动作，不能在三层后丢失",
    )


def test_old_artifact_prose_silent_only() -> None:
    """keep C：带显式标记的旧格式大纲容忍放行，无标记的静默降级仍拦（drop A/B 不受影响）。"""
    rule = next(r for r in VALIDATOR.LEGACY_RULES if r.code == "old-artifact-prose")
    require(rule.exempt_when is not None, "old-artifact-prose must narrow to silent-only")
    flagged = [
        "旧版细纲缺这些字段不阻塞读取，未知项写 `[待补充]`。",
        "旧版细纲回退读取核心事件、情节点序列、目标情绪。",
        "旧版卷纲缺少卷契约/剧情单元卡不阻塞日更；本轮记录到 `追踪/上下文.md`。",
        "旧版细纲只核对核心事件、目标情绪、章首/章尾钩子和字数目标。",
    ]
    silent = [
        "直接改读旧版细纲当权威，不提示。",
        "早期拆文库格式直接拿来用。",
        "兼容旧结构，静默继续写作。",
    ]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills = root / "skills" / "story-long-write"
        skills.mkdir(parents=True)
        (skills / "keep-c.md").write_text("\n".join(flagged) + "\n", encoding="utf-8")
        require(
            not VALIDATOR.check_absent_rule(root, rule),
            "flagged old-outline tolerance (keep C) must pass, got {}".format(
                VALIDATOR.check_absent_rule(root, rule)
            ),
        )
        (skills / "keep-c.md").write_text("\n".join(silent) + "\n", encoding="utf-8")
        found = VALIDATOR.check_absent_rule(root, rule)
        require(
            len(found) == len(silent),
            "each silent old-format downgrade must fire, got {}".format(found),
        )


def test_story_import_keeps_self_out_of_benchmarks() -> None:
    cases = {
        "story-import-self-main-benchmark": "主对标书: {书名}\n导入当前书时至少登记自身为 `主`。\n",
        "story-import-self-benchmark-copy": (
            "把 `拆文库/{书名}/` 复制到 `{项目}/对标/{书名}/`。\n"
            "短篇复制到 `{标题}/对标/{书名}/`。\n"
        ),
        "story-import-self-benchmark-summary": "## 对标摘要：{原书名}\n",
        "story-import-self-benchmark-fields": (
            "把 `拆文报告.md` 的故事核/题材/对标字段映射进本书设定。\n"
        ),
        "story-import-import-title-benchmark-target": (
            "将 `拆文库/{导入书名}/` 整体复制到项目 `对标/`。\n"
        ),
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = root / "skills" / "story-import" / "fixture.md"
        target.parent.mkdir(parents=True)
        for code, content in cases.items():
            target.write_text(content, encoding="utf-8")
            rule = next(r for r in VALIDATOR.LEGACY_RULES if r.code == code)
            found = VALIDATOR.check_absent_rule(root, rule)
            require(found, "{} must reject imported-work benchmark leakage".format(code))

        guard_rule = next(
            r
            for r in VALIDATOR.LEGACY_RULES
            if r.code == "story-import-import-title-benchmark-target"
        )
        target.write_text(
            "不得把 `拆文库/{导入书名}/` 整体复制进 `对标/`。\n",
            encoding="utf-8",
        )
        require(
            not VALIDATOR.check_absent_rule(root, guard_rule),
            "explicit self-benchmark prohibition must remain documentable",
        )


def test_spawn_preflight_uses_agents_version_not_file_existence() -> None:
    manifest = repository_manifest()
    stale = manifest.agents_version - 1
    existence_only = """
检测到 `.claude/agents/chapter-extractor.md` 存在，所以可以 spawn。
.story-deployed:
  agents_version: {stale}
""".format(stale=stale)
    found = VALIDATOR.spawn_preflight_findings(
        existence_only, manifest, Path("story-import-fixture.md")
    )
    require(
        "spawn-agents-version-preflight" in finding_codes(found),
        "a stale agent file must not satisfy the spawn preflight",
    )

    current = manifest.agents_version
    current_contract = """
读取 `.story-deployed` 的 `agents_version: {current}`；不一致时照常按文件存在性检查并 spawn，
报告 `Notice: agents bundle 版本不匹配（项目 {{N}}，本版 {current}）` 并提示重跑 `/story-setup`。
大于 {current} 时额外提示先更新 oh-story-claudecode。
只有 agent 文件缺失、或运行时不暴露 custom agent 时才降级 solo/direct，报告 `Fallback: ... -> solo`。
""".format(current=current)
    require(
        not VALIDATOR.spawn_preflight_findings(
            current_contract, manifest, Path("current-fixture.md")
        ),
        "the current shared spawn preflight must pass",
    )

    bumped = manifest_with(agents_version=current + 1)
    stale_paths = flagged_paths(bumped, "spawn-agents-version-preflight")
    require(
        stale_paths == set(VALIDATOR.SPAWN_CAPABLE_SKILLS),
        "an agents_version bump must flag every spawn-capable Skill, got {}".format(
            sorted(stale_paths)
        ),
    )


def test_reviewed_benchmark_wording_stays_removed() -> None:
    cases = {
        "benchmark-primary-nonblocking-wording": "缺失按原流程，不阻塞。\n",
        "no-benchmark-skips-genre-card": "无对标时跳过「对标模块/节奏/题材卡/文风召回」。\n",
        "style-profile-all-inputs-required": "前置依赖：报告、摘要、原文齐全。\n",
        "context-missing-skips-all": "读取上下文（按需加载，缺失则跳过）。\n",
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for code, content in cases.items():
            rule = next(r for r in VALIDATOR.LEGACY_RULES if r.code == code)
            relative = Path(rule.relative_roots[0])
            target = root / relative
            if target.suffix:
                target.parent.mkdir(parents=True, exist_ok=True)
            else:
                target.mkdir(parents=True, exist_ok=True)
                target = target / "fixture.md"
            target.write_text(content, encoding="utf-8")
            require(
                VALIDATOR.check_absent_rule(root, rule),
                "{} must reject the reviewed stale wording".format(code),
            )


def test_p1_deletion_guards() -> None:
    rules = {rule.code: rule for rule in VALIDATOR.LEGACY_RULES}
    cases = {
        "static-long-word-floor": (
            "skills/story-long-write/SKILL.md",
            "**默认最低字数：3000 字/章。**\n",
            "长篇按细纲字数目标验收；实际字数低于目标 90% 时阻断。\n",
        ),
        "broad-chrome-cleanup-doc": (
            "skills/browser-cdp/SKILL.md",
            "卡死时执行 `pkill -9 -x 'Google Chrome'`。\n",
            "卡死时关闭已确认属于 debug profile 的 Chrome 窗口；不要终止普通 Chrome。\n",
        ),
    }
    for code, (relative_path, bad, good) in cases.items():
        rule = rules[code]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(bad, encoding="utf-8")
            require(
                finding_codes(VALIDATOR.check_absent_rule(root, rule)) == {code},
                "{} must reject its retired authority/bypass".format(code),
            )
            path.write_text(good, encoding="utf-8")
            require(
                not VALIDATOR.check_absent_rule(root, rule),
                "{} must accept the canonical contract".format(code),
            )


def test_analyze_portability_guards() -> None:
    """Stage 6 的样本路径与 Stage 0 的目录块剔除都必须留在文档里。

    两者都只在真实运行时才暴露：/tmp 绝对路径要探到 Windows 原生 python 才炸，
    目录块要原文自带目录才多切一遍章。守卫是它们唯一的回归网。
    """

    rule = next(
        r for r in VALIDATOR.LEGACY_RULES if r.code == "analyze-posix-tmp-sample-path"
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / "skills/story-long-analyze/references/style-profile-generator.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("把 3 段拼接写入 `/tmp/style-sample.txt`。\n", encoding="utf-8")
        require(
            finding_codes(VALIDATOR.check_absent_rule(root, rule))
            == {"analyze-posix-tmp-sample-path"},
            "the POSIX /tmp sample path must be rejected",
        )
        path.write_text(
            "把 3 段拼接写入 `拆文库/{书名}/_style-sample.txt`。\n", encoding="utf-8"
        )
        require(
            not VALIDATOR.check_absent_rule(root, rule),
            "a project-relative sample path must be accepted",
        )

    stage0_cases = (
        (r"先剔掉目录块", "stage0-toc-block-removal"),
        (r"落表前校验章号连续", "stage0-chapter-table-validation"),
    )
    with tempfile.TemporaryDirectory() as tmp:
        fixture = Path(tmp) / "SKILL.md"
        fixture.write_text("- grep 出全部章节行号\n", encoding="utf-8")
        for pattern, code in stage0_cases:
            require(
                finding_codes(VALIDATOR.require_pattern(fixture, pattern, code, code))
                == {code},
                "{} must fire when Stage 0 drops the rule".format(code),
            )
        fixture.write_text(
            "- **先剔掉目录块**：按行距丢弃开头的目录命中\n"
            "- 落表前校验章号连续、无重复、无跳号\n",
            encoding="utf-8",
        )
        for pattern, code in stage0_cases:
            require(
                not VALIDATOR.require_pattern(fixture, pattern, code, code),
                "{} must accept the documented Stage 0 contract".format(code),
            )


def test_rubric_parity_guard() -> None:
    """两份通用 rubric 必须同维度；两边都读不到时不能算通过。"""

    rubric = (
        "## 核心维度\n\n"
        "| 维度 | PASS | WARN | FAIL |\n"
        "|---|---|---|---|\n"
        "| 核心卖点 | a | b | c |\n"
        "| 标点节奏 | a | b | c |\n"
        "\n## 发布建议门槛\n\n"
        "| 综合情况 | Verdict |\n"
        "|---|---|\n"
        "| 无 S1/S2 | PASS |\n"
    )
    embedded = "通用网文内容 rubric：\n- 核心卖点：x\n- 标点节奏：y\n\nAI 味 fallback：\n"

    def build(root: Path, rubric_body: str, skill_body: str) -> None:
        r = root / "skills/story-review/references/quality-rubric.md"
        s = root / "skills/story-review/SKILL.md"
        r.parent.mkdir(parents=True, exist_ok=True)
        r.write_text(rubric_body, encoding="utf-8")
        s.write_text(skill_body, encoding="utf-8")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        build(root, rubric, embedded)
        require(
            not VALIDATOR.rubric_parity_findings(root),
            "matching rubric dimensions must pass",
        )
        # 发布门槛表不是维度表，不能被算进来
        table, _ = VALIDATOR.rubric_dimension_names(root)
        require(
            table == ["核心卖点", "标点节奏"],
            "only the 核心维度 table counts, got {}".format(table),
        )

        build(root, rubric.replace("| 标点节奏 |", "| 标点节奏X |", 1), embedded)
        require(
            finding_codes(VALIDATOR.rubric_parity_findings(root)) == {"rubric-dimension-drift"},
            "a dimension present only in the embedded fallback must fail",
        )

        build(root, rubric, embedded.replace("- 标点节奏：y\n", "", 1))
        require(
            finding_codes(VALIDATOR.rubric_parity_findings(root)) == {"rubric-dimension-drift"},
            "a dimension present only in the file must fail",
        )

        # 整块删掉时两边都是空列表——空集相等，必须显式拦成读取失败而不是静默通过
        build(root, rubric, "没有内置 rubric 了\n")
        require(
            finding_codes(VALIDATOR.rubric_parity_findings(root)) == {"rubric-parity-unreadable"},
            "a missing embedded rubric must not pass vacuously",
        )


def test_issue_315_333_343_prompt_contracts() -> None:
    """写作引号、Stage 6 切片真值、跨批 review 持久化必须有单一明确契约。"""

    anti_ai_paths = (
        "skills/story-deslop/references/anti-ai-writing.md",
        "skills/story-long-write/references/anti-ai-writing.md",
        "skills/story-review/references/anti-ai-writing.md",
        "skills/story-short-analyze/references/anti-ai-writing.md",
        "skills/story-setup/references/agent-references/anti-ai-writing.md",
    )
    anti_ai_copies = {
        relative: (REPO_ROOT / relative).read_text(encoding="utf-8")
        for relative in anti_ai_paths
    }
    anti_ai = anti_ai_copies["skills/story-long-write/references/anti-ai-writing.md"]
    writer_paths = (
        "skills/story-setup/references/templates/agents/narrative-writer.md",
        "skills/story-setup/references/opencode/agents/narrative-writer.md",
        "skills/story-setup/references/codex/agents/narrative-writer.toml",
    )
    writer_copies = {
        relative: (REPO_ROOT / relative).read_text(encoding="utf-8")
        for relative in writer_paths
    }
    writer = writer_copies["skills/story-setup/references/templates/agents/narrative-writer.md"]
    require(
        "普通名词" in anti_ai and "引号强调" in anti_ai,
        "#315: anti-ai reference must distinguish normal nouns from legitimate quotations",
    )
    require(
        "引号强调" in writer and "角色对话" in writer,
        "#315: narrative-writer Gate B must prevent quote emphasis without banning dialogue",
    )
    require(
        all(
            anchor in anti_ai
            for anchor in (
                "sensory-subject-mismatch",
                "霉味、潮气、声音、光线",
                "先醒过来的是霉味",
                "钻进",
                "响起",
                "渗进",
                "逐字直接引用（含跨行中文引号块）",
                "书名号及 Markdown inline code 样例豁免",
                "有意拟人",
            )
        ),
        "sensory-subject: anti-ai reference must define the advisory, inversion, physical-path, quote, and personification boundaries",
    )
    require(
        all(
            anchor in writer
            for anchor in (
                "sensory-subject-mismatch",
                "霉味/潮气/声音/光",
                "醒来/睁眼/听见/看见/闻到/感到",
                "钻进/响起/渗进",
                "只作 advisory",
                "引号内对话/逐字引用（含跨行中文引号块）",
                "书名号及 Markdown inline code 样例豁免",
                "有意拟人",
            )
        ),
        "sensory-subject: narrative-writer Gate B must preserve the same advisory and exemption boundaries",
    )
    require(
        len(set(anti_ai_copies.values())) == 1,
        "sensory-subject: all five anti-ai reference copies must remain byte-identical",
    )
    sensory_writer_lines = {}
    for relative, text in writer_copies.items():
        line = next(
            (
                candidate
                for candidate in text.splitlines()
                if "sensory-subject-mismatch" in candidate and "霉味/潮气/声音/光" in candidate
            ),
            "",
        )
        require(line, f"sensory-subject: {relative} is missing the Gate B contract")
        sensory_writer_lines[relative] = line
    require(
        len(set(sensory_writer_lines.values())) == 1,
        "sensory-subject: template/OpenCode/Codex narrative-writer Gate B lines must stay synchronized",
    )

    style = (
        REPO_ROOT / "skills/story-long-analyze/references/style-profile-generator.md"
    ).read_text(encoding="utf-8")
    require(
        "只读 `_progress.md`" in style and "章节边界" in style,
        "#333: Stage 6 must read the persisted chapter-boundary table",
    )
    for stale in ("正确 Grep 模式", "相应调整 regex", "拿到 grep 的", "用 Step 4 grep"):
        require(stale not in style, f"#333: Stage 6 still instructs a second slice via: {stale}")

    review = (REPO_ROOT / "skills/story-review/SKILL.md").read_text(encoding="utf-8")
    for anchor in (
        ".story-review/state.md",
        "上一批未解决 findings 摘要",
        "先读取 state.md",
        "原子重写 state.md",
        "同时只维护一条跨批审查",
        "征得用户确认",
        "缺失、损坏或本批超出既定范围",
    ):
        require(anchor in review, f"#343: review persistence contract missing {anchor}")


def test_chinese_prose_language_contract() -> None:
    """中文正文的生成、落盘、无 Hook 执行面必须共享同一条语言锁。"""

    writer_paths = (
        "skills/story-setup/references/templates/agents/narrative-writer.md",
        "skills/story-setup/references/opencode/agents/narrative-writer.md",
        "skills/story-setup/references/codex/agents/narrative-writer.toml",
    )
    writer_copies = {
        relative: (REPO_ROOT / relative).read_text(encoding="utf-8")
        for relative in writer_paths
    }
    writer_contract_lines: dict[str, str] = {}
    for relative, text in writer_copies.items():
        for anchor in (
            "中文正文语言锁",
            "story-globalize",
            "language_gate.js",
            ".deslop-whitelist",
            "--language=zh",
            "英文句子",
            "裸英文词",
            "用户单独确认",
            "HTML",
        ):
            require(anchor in text, f"language-lock: {relative} is missing {anchor}")
        line = next(
            (
                candidate
                for candidate in text.splitlines()
                if "输出前扫描标题行以外的所有拉丁字母段" in candidate
            ),
            "",
        )
        require(line, f"language-lock: {relative} is missing the pre-output scan")
        writer_contract_lines[relative] = line
    require(
        len(set(writer_contract_lines.values())) == 1,
        "language-lock: template/OpenCode/Codex narrative-writer lines must stay synchronized",
    )

    story_format = (
        REPO_ROOT / "skills/story-setup/references/templates/rules/story-format.md"
    ).read_text(encoding="utf-8")
    for anchor in (
        '"**/正文/**"',
        '"**/正文.md"',
        "禁止中文正文语言漂移",
        ".deslop-whitelist",
        "明确非叙事结构",
        "用户单独确认",
        "HTML 标签、注释和实体",
    ):
        require(anchor in story_format, f"language-lock: story-format is missing {anchor}")

    self_lock_templates = (
        "skills/story-setup/references/generic/AGENTS.md.tmpl",
        "skills/story-setup/references/openclaw/AGENTS.md.tmpl",
        "skills/story-setup/references/reasonix/AGENTS.md.tmpl",
    )
    for relative in self_lock_templates:
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        for anchor in (
            'node "{当前写作 Skill 目录}/scripts/language_gate.js" "{正文文件}"',
            "check-ai-patterns.js",
            "check-degeneration.js",
            "--language=zh",
            "--fail-on=blocking",
            "story-globalize",
            ".deslop-whitelist",
            "用户单独确认",
            "HTML",
        ):
            require(anchor in text, f"language-lock: {relative} is missing {anchor}")
        gate_index = text.index("language_gate.js")
        require(gate_index < text.index("check-ai-patterns.js"), f"language-lock: {relative} must run language_gate first")
        require(gate_index < text.index("check-degeneration.js"), f"language-lock: {relative} must run language_gate first")

    chinese_skill_paths = (
        "skills/story-deslop/SKILL.md",
        "skills/story-long-write/SKILL.md",
        "skills/story-short-write/SKILL.md",
        "skills/story-review/SKILL.md",
    )
    for relative in chinese_skill_paths:
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        for anchor in (
            "language_gate.js",
            "check-degeneration.js",
            "--language=zh",
            "--fail-on=blocking",
            ".deslop-whitelist",
            "用户单独确认",
            "HTML",
        ):
            require(anchor in text, f"language-lock: {relative} is missing {anchor}")

    router = (REPO_ROOT / "skills/story/SKILL.md").read_text(encoding="utf-8")
    for anchor in ("story-globalize", "英文小说", "海外", "停止"):
        require(anchor in router, f"language-lock: story router is missing {anchor}")


def test_issue_351_large_book_and_extractor_contracts() -> None:
    """超长篇处理批次与 chapter-extractor 格式必须在主流调用面保持一致。"""

    analyze = (REPO_ROOT / "skills/story-long-analyze/SKILL.md").read_text(encoding="utf-8")
    for anchor in (
        "[情节点格式要求]",
        "[输出前自检]",
        "不依赖项目里已部署的 agent 文件版本",
        "10-20 章/批",
        "≤8K tokens",
        "主线程不逐章读原始摘要",
    ):
        require(anchor in analyze, f"#351: long-analyze spawn/batch contract missing {anchor}")

    material = (
        REPO_ROOT / "skills/story-long-analyze/references/material-decomposition.md"
    ).read_text(encoding="utf-8")
    for anchor in (
        "语义分块",
        "处理批次",
        "10-20 章/批",
        "每份 ≤8K tokens",
        "两两/分组合并",
        "进度与按批恢复",
        "主线程在并行模式下不读",
    ):
        require(anchor in material, f"#351: large-book decomposition contract missing {anchor}")
    require(
        "回退原「扫描全部章节摘要」逐文件方式" not in material,
        "#351: large books must not silently fall back to main-thread per-chapter scans",
    )

    output = (
        REPO_ROOT / "skills/story-long-analyze/references/output-templates.md"
    ).read_text(encoding="utf-8")
    extractor_paths = (
        "skills/story-setup/references/templates/agents/chapter-extractor.md",
        "skills/story-setup/references/opencode/agents/chapter-extractor.md",
        "skills/story-setup/references/codex/agents/chapter-extractor.toml",
    )
    for anchor in (
        "`{}` 是占位标记",
        "`主题标签` 只填一个值",
        "空字段统一写“无”",
        "每个情节点后紧跟",
    ):
        require(anchor in output, f"#351: serial output template missing {anchor}")
    require("落盘文本无 `{`/`}`" in output, "#351: serial output self-check missing")

    for relative in extractor_paths:
        extractor = (REPO_ROOT / relative).read_text(encoding="utf-8")
        for anchor in (
            "`{}` 是占位标记",
            "主题标签` 只填一个值",
            "空字段统一写“无”",
            "每个情节点后紧跟",
            "输出前自检",
        ):
            require(anchor in extractor, f"#351: {relative} missing {anchor}")


def chapter_json_fixture() -> dict[str, object]:
    """Return a minimal but fully valid deterministic Stage 2 payload."""

    plot_points = []
    for number in range(1, 11):
        plot_points.append(
            {
                "id": f"P{number}",
                "title": f"节点{number}",
                "event": f"林川完成第{number}步并确认结果。",
                "type": "行动",
                "characters": ["林川"],
                "location": "古井边" if number == 1 else None,
                "item": "药方" if number == 1 else None,
                "time": None,
                "quote": f"关键原句{number}" if number <= 8 else None,
                "quote_locator": None,
                "themes": ["成长"],
                "tone": "紧张",
            }
        )
    return {
        "chapter_number": 1,
        "title": "古井递方",
        # 100 个 Unicode code point，UTF-8 则是 300 bytes：防止实现误按字节计数。
        "summary": "药" * 100,
        "key_events": ["林川来到古井边", "古井递出药方"],
        "key_information_expansion": [
            {
                "key_information": "古井会递出药方",
                "expansion": "先写异响，再用人物动作确认药方存在",
                "technique": "延迟揭示",
                "reader_effect": "好奇",
                "reuse_note": "保留信息延迟链，替换人物、场景和道具",
            }
        ],
        "chapter_formula": {
            "emotion_flow": {
                "start": "压抑",
                "build": "疑惑",
                "turn": "紧张",
                "close": "期待",
            },
            "rhythm_ratio": {
                "slow_setup": "25%",
                "fast_conflict": "25%",
                "payoff": "25%",
                "hook_space": "25%",
            },
            "structure_formula": ["发现异响", "接近古井", "取得药方"],
            "core_technique": "用具体道具承载章尾悬念",
            "hook_and_foreshadowing": "章尾留下药方来源与用途的疑问",
        },
        "characters": [
            {
                "name": "林川",
                "importance": "major",
                "aliases": [],
                "performance": "听见井中异响后靠近查看，发现石缝中露出的药方。",
            }
        ],
        "plot_points": plot_points,
    }


def test_chapter_json_renderer_contract() -> None:
    """JSON 正常链路可执行，任一反例失败时不碰旧 Markdown。"""

    renderer = REPO_ROOT / "skills/story-long-analyze/scripts/render_chapter_summary.py"
    require(renderer.is_file(), "chapter JSON renderer must exist")

    def invoke(input_path: Path, output_path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(renderer),
                "--input",
                str(input_path),
                "--output",
                str(output_path),
                *extra,
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    with tempfile.TemporaryDirectory(prefix="chapter-json-renderer-") as tmp:
        root = Path(tmp)
        input_path = root / "chapter.json"
        output_path = root / "chapter.md"
        valid = chapter_json_fixture()
        input_path.write_text(json.dumps(valid, ensure_ascii=False), encoding="utf-8")

        result = invoke(
            input_path,
            output_path,
            "--expect-chapter-number",
            "1",
            "--expect-title",
            "古井递方",
        )
        require(result.returncode == 0, result.stdout + result.stderr)
        markdown = output_path.read_text(encoding="utf-8")
        require(markdown.startswith("## 第1章 古井递方\n"), "renderer must emit canonical heading")
        require(markdown.count("\nP") == 10, "renderer must emit exactly ten plot-point lines")
        require(
            markdown.count("主题标签成长 | 基调：紧张") == 10,
            "renderer must deterministically project one theme and one tone per point",
        )
        first_render = output_path.read_bytes()
        second = invoke(input_path, output_path)
        require(second.returncode == 0, second.stdout + second.stderr)
        require(output_path.read_bytes() == first_render, "renderer output must be deterministic")

        output_path.write_text("SENTINEL\n", encoding="utf-8")
        checked = invoke(input_path, output_path, "--check-only")
        require(checked.returncode == 0, checked.stdout + checked.stderr)
        require(
            output_path.read_text(encoding="utf-8") == "SENTINEL\n",
            "--check-only must never touch --output",
        )

        invalid_cases: list[tuple[str, dict[str, object], str]] = []

        short_summary = chapter_json_fixture()
        short_summary["summary"] = "药" * 99
        invalid_cases.append(("summary-unicode-length", short_summary, "at least 100 Unicode"))

        non_contiguous = chapter_json_fixture()
        non_contiguous["plot_points"][4]["id"] = "P6"  # type: ignore[index]
        invalid_cases.append(("plot-id-gap", non_contiguous, "plot IDs must be continuous"))

        multi_theme = chapter_json_fixture()
        multi_theme["plot_points"][0]["themes"] = ["成长", "悬念"]  # type: ignore[index]
        invalid_cases.append(("multiple-themes", multi_theme, "at most 1 item"))

        too_many_quotes = chapter_json_fixture()
        too_many_quotes["plot_points"][8]["quote"] = "第九条引用"  # type: ignore[index]
        invalid_cases.append(("nine-quotes", too_many_quotes, "at most 8 points"))

        long_quote = chapter_json_fixture()
        long_quote["plot_points"][0]["quote"] = "字" * 401  # type: ignore[index]
        invalid_cases.append(("quote-too-long", long_quote, "at most 400 Unicode"))

        unknown_enum = chapter_json_fixture()
        unknown_enum["plot_points"][0]["tone"] = "激动"  # type: ignore[index]
        invalid_cases.append(("unknown-tone", unknown_enum, "must be one of"))

        extra_key = chapter_json_fixture()
        extra_key["model_note"] = "should fail"
        invalid_cases.append(("extra-key", extra_key, "unexpected keys"))

        bad_ratio = chapter_json_fixture()
        bad_ratio["chapter_formula"]["rhythm_ratio"]["hook_space"] = "24%"  # type: ignore[index]
        invalid_cases.append(("rhythm-ratio-sum", bad_ratio, "must sum to 100%"))

        quote_and_locator = chapter_json_fixture()
        quote_and_locator["plot_points"][0]["quote_locator"] = "可回查关键原句"  # type: ignore[index]
        invalid_cases.append(
            ("quote-and-locator", quote_and_locator, "may set quote or quote_locator, not both")
        )

        not_a_number = chapter_json_fixture()
        not_a_number["summary"] = float("nan")
        invalid_cases.append(("nan", not_a_number, "non-finite JSON number"))

        infinity = chapter_json_fixture()
        infinity["summary"] = float("inf")
        invalid_cases.append(("infinity", infinity, "non-finite JSON number"))

        for label, payload, error_fragment in invalid_cases:
            input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            output_path.write_text("SENTINEL\n", encoding="utf-8")
            failed = invoke(input_path, output_path)
            require(failed.returncode != 0, f"{label}: invalid fixture unexpectedly passed")
            require(error_fragment in failed.stderr, f"{label}: wrong failure: {failed.stderr}")
            require(
                output_path.read_text(encoding="utf-8") == "SENTINEL\n",
                f"{label}: validation failure touched the existing output",
            )

        input_path.write_text(
            json.dumps(chapter_json_fixture(), ensure_ascii=False), encoding="utf-8"
        )
        for label, mismatch_args, error_fragment in (
            (
                "chapter-number-mismatch",
                ("--expect-chapter-number", "2"),
                "must equal expected chapter 2",
            ),
            (
                "chapter-title-mismatch",
                ("--expect-title", "错误章名"),
                "does not match the expected chapter title",
            ),
        ):
            output_path.write_text("SENTINEL\n", encoding="utf-8")
            mismatch = invoke(input_path, output_path, *mismatch_args)
            require(mismatch.returncode != 0, f"{label}: mismatch unexpectedly passed")
            require(error_fragment in mismatch.stderr, f"{label}: wrong failure: {mismatch.stderr}")
            require(
                output_path.read_text(encoding="utf-8") == "SENTINEL\n",
                f"{label}: mismatch touched the existing output",
            )

        valid_text = json.dumps(chapter_json_fixture(), ensure_ascii=False)
        duplicate_key = valid_text.replace(
            '{"chapter_number": 1,',
            '{"chapter_number": 1, "chapter_number": 1,',
            1,
        )
        input_path.write_text(duplicate_key, encoding="utf-8")
        output_path.write_text("SENTINEL\n", encoding="utf-8")
        duplicate_failed = invoke(input_path, output_path)
        require(duplicate_failed.returncode != 0, "duplicate JSON key unexpectedly passed")
        require("duplicate JSON key" in duplicate_failed.stderr, duplicate_failed.stderr)
        require(
            output_path.read_text(encoding="utf-8") == "SENTINEL\n",
            "duplicate JSON key touched the existing output",
        )

        fenced = "```json\n" + json.dumps(chapter_json_fixture(), ensure_ascii=False) + "\n```\n"
        input_path.write_text(fenced, encoding="utf-8")
        output_path.write_text("SENTINEL\n", encoding="utf-8")
        failed_fence = invoke(input_path, output_path)
        require(failed_fence.returncode != 0, "fenced model prose must not be accepted as JSON")
        require(
            output_path.read_text(encoding="utf-8") == "SENTINEL\n",
            "parse failure touched the existing output",
        )


def main() -> int:
    test_manifest_contract()
    test_marketplace_skill_version_guard()
    test_bad_fallbacks_fail()
    test_fail_fast_prose_passes()
    test_sibling_bullets_do_not_lend_the_missing_condition()
    test_undecodable_markdown_is_a_named_failure()
    test_progress_schema_pins_are_repo_wide()
    test_deeply_nested_fallback_keeps_all_governing_ancestors()
    test_stale_scan_phase_reference_accepts_backticks()
    test_old_artifact_prose_silent_only()
    test_story_import_keeps_self_out_of_benchmarks()
    test_spawn_preflight_uses_agents_version_not_file_existence()
    test_reviewed_benchmark_wording_stays_removed()
    test_p1_deletion_guards()
    test_analyze_portability_guards()
    test_rubric_parity_guard()
    test_issue_315_333_343_prompt_contracts()
    test_chinese_prose_language_contract()
    test_issue_351_large_book_and_extractor_contracts()
    test_chapter_json_renderer_contract()
    test_structured_sentinel_contract()
    test_structured_outline_contract()
    test_upgrading_version_contract()
    print("OK: current-contract manifest, structure, and fallback regressions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
