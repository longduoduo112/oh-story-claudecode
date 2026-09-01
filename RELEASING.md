# 发版手册

本文面向 oh-story 维护者。用户的正式安装和更新只能使用这个 GitHub Release 固定资产 URL：

```text
https://github.com/qin1473692580-ux/oh-story-claudecode/releases/latest/download/oh-story-release.zip
```

裸仓库、分支压缩包和浮动的 `main` 只供开发测试（dev-only），不是用户发行渠道。

`v0.7.6` 是首次按本流程发布固定资产的版本；只有对应 Release 正式公开后，上面的固定 URL 才生效。不要只合并新安装说明而不完成对应 Release。

## 三条版本轴

| 版本轴 | 当前权威 | 用途 | 何时变更 |
|---|---|---|---|
| 产品 SemVer | `skills/story/VERSION`，当前 `0.8.0` | GitHub Release、安装包和各 plugin manifest 的公开版本 | 每次正式发版；用 `scripts/manage-version.py` 同步所有公开版本面 |
| `setup_skill_version` | `scripts/current-contract.json`，当前 `1.2.22` | `story-setup` 部署流程/哨兵协议的版本 | 只在该部署协议本身需要新版识别时改 |
| `agents_version` | `scripts/current-contract.json`，当前 `39` | 已部署 hooks / agents / rules / references 是否过期的唯一运行时权威 | 只在部署包行为变更、需要用户重跑 `story-setup` 时改 |

三条轴互相独立。发一个产品 patch 不代表必须改 `setup_skill_version` 或 `agents_version`；只改文档/打包管道时不要顺手 bump 后两者。

## SemVer 政策

- 公开版本只使用稳定 `X.Y.Z`，不把 prerelease/build metadata 写入六个公开版本面。
- PATCH：兼容的修复、规则校正、文档或发行管道修复。
- MINOR：向后兼容的新能力、新 skill 或显著新流程。`0.x` 阶段若引入用户可见的破坏性变更，至少升 MINOR 并在 `CHANGELOG.md` 标红迁移。
- MAJOR：进入稳定主版后的破坏性改动。
- dev 包由构建器在内存中派生 `X.Y.Z-dev.<UTC>+g<SHA>`，不回写源树，也不占用公开版本号。

`v0.7.6` 及更早版本已经是历史发布身份，不得移动、重打或用新资产覆盖。本手册当前对应 **`v0.8.0`** 候选；后续版本继续按上述 SemVer 规则递增。

## 发版前准备

1. 从当前 `main` 准备发版候选提交，确保工作树干净。下一版执行：

   ```bash
   python3 scripts/manage-version.py set 0.8.0
   ```

   该命令只同步六个公开产品版本面（含 `.codebuddy-plugin/plugin.json`），不改 `setup_skill_version` 或 `agents_version`。

2. 在 `CHANGELOG.md` 新增顶层 `v0.8.0` 条目，并检查版本一致性：

   ```bash
   python3 scripts/manage-version.py check --require-changelog
   ```

   当前未发布候选新增 TRAE Code 与 WorkBuddy / CodeBuddy Code 原生适配。中文主包的 TRAE 名册为 13 张（8 通用 + 5 个数据分析精确角色），WorkBuddy 名册为 10 张（8 通用 + `story-data-fetcher` + `story-data-readonly-runner`）；19 张只是 WorkBuddy 平台容量边界。`setup_skill_version` 提升至 `1.2.22`、`agents_version` 提升至 `39`，须同步 `scripts/current-contract.json`、`SKILL.md`、`UPGRADING.md`、hook 与契约锚点。WorkBuddy manifest 还必须用真实 `codebuddy plugin validate .` 通过；新会话中必须实际调用基础 Agent、fetcher 和 readonly pooled runner 的一个逻辑角色，设置页显示为 enabled 不能替代 Task registry 调度证据。提交候选后用下列门禁确认；门禁会按实际 diff 判断，不要求无关发版乱 bump：

   ```bash
   python3 scripts/check-release-contract-bumps.py --base-tag v0.7.10
   ```

3. 提交发版候选改动后，在还未 push 的该精确 commit 上构建 dev 包：

   ```bash
   python3 scripts/package-channel.py dev
   ```

   这是本地标准入口：它先运行唯一统一 gate `bash scripts/run-quality-gate.sh`，然后调用 `scripts/build-package.py dev` 写入 `dist/dev/`。不要拿手挑的部分测试代替统一 gate。确认 manifest 中 `source_dirty` 为 `false`、`source_sha` 等于当前 `HEAD`；否则不是可发布候选。

4. 必须对 dev zip 执行一次真实的校验、解包和本地安装 smoke。下列是 POSIX 示例，全部在临时目录中操作：

   ```bash
   (cd dist/dev && shasum -a 256 -c SHA256SUMS)
   DEV_ZIP="$(find "$PWD/dist/dev" -maxdepth 1 -type f -name 'oh-story-*-dev.*.zip' -print | sort | tail -n 1)"
   test -n "$DEV_ZIP"
   SMOKE_ROOT="$(mktemp -d)"
   mkdir -p "$SMOKE_ROOT/unpacked" "$SMOKE_ROOT/project"
   unzip -q "$DEV_ZIP" -d "$SMOKE_ROOT/unpacked"
   PACKAGE_ROOT="$(find "$SMOKE_ROOT/unpacked" -mindepth 1 -maxdepth 1 -type d -name 'oh-story-*' -print -quit)"
   test -f "$PACKAGE_ROOT/skills/story/SKILL.md"
   (cd "$SMOKE_ROOT/project" && npx skills add "$PACKAGE_ROOT" -y)
   ```

   这条本地路径安装只是 dev-only 发版 smoke。还需在至少一个主力 CLI 中确认 `story` 可发现，并运行一次 `story-setup` 的 dry-run/临时项目部署。

## `main` 与 GitHub Actions 发布

1. 本地 dev 包通过后，把同一发版候选提交合入并 push 到 `main`。不要从功能分支或任意 SHA 发布。
2. 在 Actions 中等待 `.github/workflows/package-dev.yml`（显示名 **Dev package**）、`.github/workflows/cross-platform.yml`（显示名 **Cross-platform smoke test**）和 `.github/workflows/cli-compat.yml`（显示名 **Agent CLI compatibility**）都对该 `main` 精确 commit 变绿。发布期间如果 `main` 又前进，必须对新 HEAD 重新等待三份证据。
3. 在 GitHub 仓库 **Settings → General → Releases** 开启官方 [**Immutable releases**](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases)，并在 dispatch 前从设置页或管理 API 回读确认。默认 `GITHUB_TOKEN` 没有 Administration (read) 权限，release workflow 无法可靠查询该开关，所以这是人工强制前置，不是 workflow 自动 gate。
4. 打开 `.github/workflows/release.yml`（显示名 **Release package**），从 **`main`** 手动运行 `workflow_dispatch`：
   - `version`: `0.8.0`
   - `publish_clawhub`: `false`
5. release workflow 必须对输入版本、`main` HEAD、`CHANGELOG.md`、三条版本轴增量、同 commit 的三份绿色 CI 证据和统一 gate 全部 fail-closed；随后用 release channel 构建，创建 **annotated tag** `v0.8.0`，并创建 **draft GitHub Release**。不使用 lightweight tag，不自动公开未复核的包。Immutable releases 的开关仍以上一步人工确认为准。

## Draft 复核与公开

draft 必须至少包含以下五个资产：

| 资产 | 用途 |
|---|---|
| `oh-story-release.zip` | 用户固定 URL 使用的稳定资产名，字节应与当版 versioned zip 相同 |
| `oh-story-0.8.0.zip` | 可追溯的版本化 zip |
| `oh-story-0.8.0.tar.gz` | 可追溯的版本化 tarball |
| `oh-story-0.8.0.manifest.json` | 包版本、源 commit、合约版本、内容指纹和文件清单 |
| `SHA256SUMS` | 稳定别名、版本化 zip/tar 与 manifest 的 SHA-256 校验值 |

发布人下载 draft 资产后复核：

```bash
VERIFY_DIR="$(mktemp -d)"
gh release download v0.8.0 --dir "$VERIFY_DIR"
(cd "$VERIFY_DIR" && shasum -a 256 -c SHA256SUMS)
cmp "$VERIFY_DIR/oh-story-release.zip" "$VERIFY_DIR/oh-story-0.8.0.zip"
python3 -m json.tool "$VERIFY_DIR/oh-story-0.8.0.manifest.json" >/dev/null
git fetch --tags
test "$(git cat-file -t v0.8.0)" = tag
gh release view v0.8.0 --json isDraft,tagName,targetCommitish
```

另外确认 manifest 的 `version` 为 `0.8.0`、`source_dirty` 为 `false`、`source_sha` 等于已通过 `package-dev` 的 `main` commit，压缩包只有一个根目录。用下载的 versioned zip 再做一次临时目录安装 smoke，通过后才手动把 draft 转为 published。公开后最后验证 GitHub 的 immutable attestation 与资产：

```bash
gh release verify v0.8.0
gh release verify-asset v0.8.0 "$VERIFY_DIR/oh-story-0.8.0.zip"
gh release verify-asset v0.8.0 "$VERIFY_DIR/oh-story-release.zip"
```

然后确认固定 URL 可下载，且包内版本为 `0.8.0`。`gh release verify*` 不替代 `SHA256SUMS` 和 manifest 复核，两组校验都要通过。

## 不可变与失败处理

- tag、draft 或任一 release 资产一旦创建，该版本号就已占用。禁止 force-move tag、删除后重建同名 tag、覆盖同名资产或把重构建包塞回旧 Release。
- GitHub 官方 Immutable releases 是公开后的平台级保护；workflow 在 draft 阶段也必须自己禁止复用标签、复用 draft 或 `--clobber`，不得把平台开关当成可变 draft 的豁免。
- workflow 应在发现 tag / Release / 资产同名冲突时直接失败，而不是用 overwrite 参数继续。
- 如果只在外部对象创建之前的 gate 阶段失败，可修复后重新产生同一候选版；一旦进入 tag/draft/assets 阶段后失败，保留现场，修复必须走新的 PATCH（`0.8.0` 之后是 `0.8.1`），不修改旧资产。
- 发现公开包有问题时，在 Release notes 标出已知问题/建议升级版本，然后走新 PATCH；不使用“原地修包”。

## ClawHub（可选）

`Release package` 的 `publish_clawhub` 默认为 `false`，GitHub Release 不依赖 ClawHub 成功。当前仓库还没有配置 `CLAWHUB_TOKEN` secret，所以发版时必须保持 `publish_clawhub=false`；不要为了让主发布变绿而伪造 token 或降低校验。

后续只有在维护者确认 ClawHub 帐号、为仓库 Actions 配置有效 `CLAWHUB_TOKEN`，并先完成 dry-run 后，才可将该布尔输入设为 `true`。ClawHub 依旧是可选的二次发布渠道，不得改变 GitHub Release 的 checksum、manifest 或不可变资产。
