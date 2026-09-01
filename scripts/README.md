# scripts/ —— 仓库开发脚本索引

这些是开发本仓库（skill 套件本体）用的**发行 / 守卫 / 测试 / 代码生成**脚本，**不是** skill 运行时脚本（运行时脚本在各 skill 自己的 `scripts/` 下，如 `story-deslop/scripts/check-ai-patterns.js`，跨 skill 字节同步）。

- 绝大多数由 CI 自动跑。提交前统一入口是 `bash scripts/run-quality-gate.sh`；发版顺序见 [RELEASING.md](../RELEASING.md)。
- **改名 / 移动任一脚本**，要同步改 `.github/workflows/*.yml`、`CONTRIBUTING.md`、本文件，以及调用它的兄弟脚本（见下方「何时跑」里的调用关系）。

## 质量门与发行

| 脚本 | 做什么 | 何时跑 |
|---|---|---|
| `run-quality-gate.sh` | 顺序运行版本、打包、skill 契约、各 adapter、编码与 Dashboard 的统一必过 gate | 提交前；dev/release 打包前；CI |
| `manage-version.py` | `check` 校验六个公开产品版本面（含 CodeBuddy plugin manifest），`set X.Y.Z` 一次同步它们；不触碰 `setup_skill_version` / `agents_version` | 发版准备；统一 gate |
| `check-release-contract-bumps.py` | 对比上个 release tag；story-setup 本体或部署 payload 改变时强制提升对应合约版本，并禁止版本回退 | Release package preflight |
| `package-channel.py` | 发行总入口：`dev` 先跑统一 gate 再构建；`release` 额外要求当前 HEAD 的干净 dev manifest、正确 annotated tag 与 changelog | 本地 dev/release 候选验证；CI |
| `build-package.py` | 用可复现时间戳构建单根 zip/tar.gz，排除本地/敏感产物，输出 manifest 与 `SHA256SUMS`；dev 版本只在内存副本改写 | 由 `package-channel.py` 调用；构建器定向调试 |
| `verify-package.py` | 校验 manifest/checksum、ZIP 安全、单根目录和六个版本面；可在临时目录用固定版 `skills` CLI 做 skill 发现 smoke | dev/release 构建后 |

相关回归是 `test-manage-version.py`、`test-release-contract-bumps.py`、`test-package-channel.py`、`test-build-package.py` 和 `test-verify-package.py`。用户稳定安装包不从 `main` 直出：GitHub Actions 先用 `.github/workflows/package-dev.yml` 为精确 commit 产生 dev 证据，再由 `.github/workflows/release.yml` 手动创建不可覆盖的 draft Release。

## 静态守卫（check-*）

| 脚本 | 检查什么 | 何时跑 |
|---|---|---|
| `static-check.sh` + `static-check.py` | 结构化验证 frontmatter、Markdown 路径/锚点、Agent 引用、references 可达性；除基础组件 `browser-cdp` 外禁止跨 Skill 文件引用 | CI |
| `skill-numbering.py check` | 工作流 Step/Phase/Stage 编号策略、引用绑定、SKILL.md 裸编号/子步骤小数守卫 | CI；改工作流结构后 |
| `check-current-skill-contracts.sh` + `.py` + `current-contract.json` | 从结构化 manifest 校验当前版本、Phase、schema、主产物与细纲契约；保留 legacy/path 守卫并拦截缺主产物后的静默替代 | CI |
| `check-shared-files.sh` | 调 `sync-shared-assets.py check` 验 manifest 声明的 runtime 副本，再验共享 reference 字节一致（排除平台专属 Agent/Command 适配文件） | CI |
| `check-story-setup-deployment.sh` | story-setup 部署/运行时回归（慢，>2min） | CI |
| `check-hook-regex-sync.sh` | `detect-story-gaps.sh` 伏笔状态检测行为 | CI |
| `check-hook-locale-safety.sh` | 部署 hook 在 Windows 中文 GBK 区域的字节安全 | CI |
| `check-python-invocation.sh` | 技能文档禁止裸调 `python3`（须 python3→python→py 探测） | CI |
| `check-claude-adapter.sh` | Claude marketplace 与 18 个 skill 的一一映射；可选真实 CLI strict validate | CI（静态）；`CLAUDE_REAL_CHECK=1`（真实 CLI） |
| `check-opencode-adapter.sh` | OpenCode 适配层同步 + commands/agents/config 结构 + plugin 行为回归 | CI + sync CI（调 sync-opencode.py） |
| `check-openclaw-skills.sh` | OpenClaw AgentSkills/frontmatter 兼容性 | CI |
| `check-codex-adapter.sh` | Codex 适配层：repo skills symlink、agent TOML、hooks 与跨平台 launcher | CI（调 generate-codex-agents.py 验生成确定性） |
| `check-zcode-adapter.sh` | ZCode plugin/marketplace、Skills/Commands/Hooks 与部署锚点 | CI |
| `check-workbuddy-adapter.py` | WorkBuddy / CodeBuddy 双模式：规范 plugin manifest、命名空间、中文主包 10 张物理 Agent 卡、project/plugin Hook 互斥、memory 防遮蔽、PowerShell 边界、包边界与生成确定性 | CI（调 `generate-workbuddy-adapter.py --check`） |
| `check-reasonix-adapter.sh` | Reasonix plugin manifest（schema、18 Skills、版本与 skills/story/VERSION 同步） | CI |

## 测试回归（test-*）

| 脚本 | 测什么 | 何时跑 |
|---|---|---|
| `test-ai-patterns.sh` | 确定性 AI 句式检测器 `check-ai-patterns.js` 回归 | CI |
| `test-degeneration.sh` | 模型退化检测器 `check-degeneration.js` 回归 | CI |
| `test-prose-net-parity.sh` | 正文兜底「轻量确定性网」Claude/OpenCode/Codex/ZCode parity | CI（调 check-hook-regex-sync） |
| `test-prose-backstop-hook.sh` | `check-prose-after-write.sh` 回归 | CI |
| `test-story-continuity.sh` | `detect-story-gaps.sh` 跨批连续性兜底回归 | CI |
| `test-tracking-workflow-contracts.py` | 文件优先追踪契约：唯一事务写入口、续写状态卡（固定 7 栏）、导入基线、作者/读者时间线隔离、旧结构清零 | CI |
| `test-tracking-commit.py` | 单权威追踪行为：state 最后提交、失败同事务重跑、派生一致性、修订语义、导入截止章 | CI |
| `test-outline-forecast.py` | 分支推演隔离、追踪修订陈旧判定、用户选择凭证与禁止自动写回 | CI |
| `test-chapter-candidate.py` | 精确一章许可、候选接纳、正文摘要失效、追踪闭环与 doctor | CI |
| `test-voice-profile.py` | 已接纳正文声音画像、回执/旧章授权、双向漂移 advisory、摘要陈旧与盲测包 | CI |
| `test-cold-read-ledger.py` | 顺序冷读游标、追加式问题事件与未解决 S1/S2 阻断 | CI |
| `test-codex-hooks.sh` | Codex hook 合成 stdin/stdout 契约 | CI |
| `test-static-check.py` | 真 frontmatter block、精确路径/锚点、跨 Skill 引用、fence、死 reference、Agent 与章节链接 fixture | CI |
| `test-current-skill-contracts.py` | current-contract manifest 类型/固定值与主产物 fail-fast 语义 fixture | CI |
| `test-revision-hook-gate.js` | 旧正文/既有大纲设定修改的活动清单、计划内写入、日更暂停与摘要审批失效门禁 | CI |
| `test-revision-codex-gate.py` | Codex Python adapter 与共享修改门禁的计划/审批生命周期回归 | CI |
| `test-shared-assets.py` | 共享资产 manifest 的 drift、sync、路径越界、basename 单一 owner 与未登记重复检测 | CI |
| `test-manage-version.py` | 产品 SemVer 五处同步、tag/changelog 校验与非稳定版拒绝 | 统一 gate |
| `test-package-channel.py` | release 只接受同 HEAD、干净且 checksum 有效的 dev 证据 | 统一 gate |
| `test-build-package.py` | dev/release 清单、单根包、版本改写、不可变输出、manifest/checksum 与 tag/干净度门禁 | 统一 gate |
| `test-verify-package.py` | 安装包路径安全、manifest/checksum、五版本面与 skills CLI 发现 smoke 的离线 fixture | 统一 gate |
| `test-release-contract-bumps.py` | story-setup 与部署 payload 改动必须提升独立版本轴、且版本不可回退 | 统一 gate |
| `test-normalize-punctuation.js` | 标点归一化的只读检查、frontmatter/fence、CRLF、引号模式与幂等性 | CI |
| `test-scan-runtime.js` | CDP argv 边界/报错/JSON 契约与 7 个 scraper 无副作用 import | CI |
| `test-opencode-plugin.mjs` | 直接执行 OpenCode TypeScript plugin，验大纲守卫、Bash 绕过、写后检查与 compact 恢复 | 被 `check-opencode-adapter.sh` 调用 |
| `test-codex-cli-e2e.sh` | 隔离 HOME 后用真实 Codex CLI 检查 repo 18 个 skill 的发现结果 | CLI compatibility CI；需已安装 `codex` |
| `test-zcode-hooks.sh` | ZCode 严格 JSON Hook、正文守卫与连续性回归 | CI |
| `test-workbuddy-runtime.py` | WorkBuddy SessionStart 版本门、Bash/PowerShell 中文正文写目标、顶层 `systemMessage`、project/plugin Hook 互斥、pooled runner 角色映射与 CODEBUDDY/AGENTS 条件导入 | CI |
| `test-charcount-portable.sh` | 跨平台字符统计命令在三平台 + Windows 的正确性 | CI（调 check-python-invocation） |
| `test-hook-encoding-portable.sh` | 部署 hook 在 Windows 中文系统的编码健壮性 | CI |
| `test-opencode-cli-e2e.sh` | 真实 OpenCode CLI 加载 smoke（repo skills 发现 / 18 commands / 8 agents / plugin） | CLI compatibility CI；需已安装 `opencode` |
| `test-skill-numbering.sh` | Step 重排级联安全、锚点 fail-closed、代码块引用、验证零写入/提交回滚、dry-run/write/幂等性 | Linux / Windows Git Bash / macOS CI |

## 代码生成 / 同步

| 脚本 | 干什么 | 何时跑 |
|---|---|---|
| `sync-opencode.py` | 从 Claude agent 模板 + `CLAUDE.md.tmpl` 生成 `opencode/agents/` 与 `AGENTS.md.tmpl`；`--check` 只读验同步 | 改 agent 模板后手动跑；sync CI + 被 check-opencode-adapter 调 |
| `generate-codex-agents.py` | 从 Claude agent 模板生成 Codex `.toml` agents | 改 agent 模板后手动跑；被 check-codex-adapter 调验确定性 |
| `generate-codex-hooks.py` | 从 6 个 event 清单生成 `hooks.json`，POSIX/Windows 共用 launcher 负责解释器探测 | 改 Codex hook 注册后；被 check-codex-adapter 调验确定性 |
| `generate-workbuddy-adapter.py` | 从受审 TRAE 角色/命令与共享 core 生成 WorkBuddy Commands、Agents 和 runner，并注入 CodeBuddy 专属版本门、输出字段与 PowerShell 解析 | 改 WorkBuddy adapter 或源角色后；被 check-workbuddy-adapter 调验确定性 |
| `shared-assets.json` + `sync-shared-assets.py` | 为必须随 skill 独立部署的重复 runtime 脚本指定唯一源和目标 | 改共享 runtime 后跑 `sync`；CI 跑 `check` |

> 改了 `skills/story-setup/references/templates/agents/*.md` 或 `CLAUDE.md.tmpl`，必须重跑这两个生成脚本并提交结果，否则适配层 CI 红。详见 [CONTRIBUTING.md](../CONTRIBUTING.md)「OpenCode 模板同步」「Codex 适配维护」。

## 工作流编号维护

`skill-numbering.py` 默认扫描 canonical `skills/**/*.md`，用于阻止迭代插入把工作流编号累积成 `Step 1.3`、`Phase 2.5` 一类小数标签。

```bash
python3 scripts/skill-numbering.py audit          # 只读盘点；发现问题仍退出 0
python3 scripts/skill-numbering.py check          # CI 守卫；发现问题退出非 0
python3 scripts/skill-numbering.py fix --dry-run  # 先看完整 diff，不落盘
python3 scripts/skill-numbering.py fix --write    # 校验通过后一次性落盘
bash scripts/test-skill-numbering.sh              # 隔离 fixture 回归
```

维护策略：

- 只有形如 `### Step N` 的**显式 Step 标题**会自动重排；分组键是「文件 + 标题层级 + 最近父标题」，每组从 1 连续编号。
- 标题与可唯一绑定的 `Step N` 引用基于旧文本同时换号，包含 fenced code block 内的命令/示例引用，避免 `1.5 → 2` 后又被 `2 → 3` 二次级联。
- fractional Step 引用找不到本文件标题，或一个旧标签可能映射到多个新标签时，`fix` 会在任何写入前失败。多文件写入先全量校验/暂存并带回滚，不接受半套结果。
- 标题改号会改变 GitHub Markdown anchor；只要仓库内存在指向旧 anchor 的同文件或跨文件链接，`fix` 就在写入前 fail-closed，并报告每个 fragment，要求先显式更新链接后再重试。局部路径模式同样扫描仓库内入站链接。
- `Step N.M` / `Phase N.M` / `Stage N.M`、直接 `skills/*/SKILL.md` 中的裸小数标题及 bullet 小数子步骤由 `check` 报错，但不做猜测式自动修改。
- `references/` 手册本身的 `3.1` 章节/列表编号不属于工作流标签，不检查、不改写。如果管道 ID 需要插入中间阶段，使用语义名称或 `Stage 2A`，不用小数。
- 可在命令末尾传文件或目录做局部审计，例如 `... audit skills/story-cover/SKILL.md`；合入前仍须跑默认全量 `check`。
