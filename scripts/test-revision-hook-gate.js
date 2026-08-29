#!/usr/bin/env node
"use strict"

const assert = require("node:assert")
const crypto = require("node:crypto")
const fs = require("node:fs")
const os = require("node:os")
const path = require("node:path")

const core = require("../skills/story-setup/references/templates/hooks/story_hook_core.js")

const root = fs.mkdtempSync(path.join(os.tmpdir(), "story-revision-hook-"))
try {
  const book = path.join(root, "测试书")
  const oldChapter = path.join(book, "正文", "第2章_旧章.md")
  const nextChapter = path.join(book, "正文", "第3章_新章.md")
  const setting = path.join(book, "设定", "角色", "甲.md")
  const impactDir = path.join(book, "追踪", "修改影响")
  fs.mkdirSync(path.dirname(oldChapter), { recursive: true })
  fs.mkdirSync(path.dirname(setting), { recursive: true })
  fs.mkdirSync(impactDir, { recursive: true })
  fs.writeFileSync(oldChapter, "旧章\n")
  fs.writeFileSync(setting, "旧设定\n")
  fs.writeFileSync(
    path.join(book, "追踪", "_tracking-state.json"),
    JSON.stringify({ schema_version: 5, state_revision: 9, last_committed_chapter: 2 }),
  )

  assert.match(core.revisionBlockReason(root, oldChapter), /修改旧内容被拦截/)
  assert.strictEqual(core.revisionBlockReason(root, nextChapter), null)

  const manifestPath = path.join(impactDir, "active.json")
  const manifest = {
    schema_version: 1,
    change_id: "rev-002",
    changed_files: ["正文/第2章_旧章.md"],
  }
  fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2) + "\n")
  assert.strictEqual(core.revisionBlockReason(root, oldChapter), null)
  assert.match(core.revisionBlockReason(root, nextChapter), /计划外修改被拦截/)
  assert.match(core.revisionBlockReason(root, setting), /计划外修改被拦截/)

  const bytes = fs.readFileSync(manifestPath)
  fs.writeFileSync(path.join(impactDir, "active.approved.json"), JSON.stringify({
    schema_version: 1,
    status: "PASS",
    change_id: manifest.change_id,
    manifest_sha256: crypto.createHash("sha256").update(bytes).digest("hex"),
  }))
  assert.match(core.revisionBlockReason(root, oldChapter), /已验收关闭/)
  assert.strictEqual(core.revisionBlockReason(root, nextChapter), null)

  fs.appendFileSync(manifestPath, "\n")
  assert.strictEqual(core.revisionBlockReason(root, oldChapter), null)
  assert.match(core.revisionBlockReason(root, nextChapter), /计划外修改被拦截/)

  fs.writeFileSync(manifestPath, "{broken")
  assert.match(core.revisionBlockReason(root, oldChapter), /无法解析/)
  console.log("OK: revision hook gate blocks unplanned old-content edits and stale approvals")
} finally {
  fs.rmSync(root, { recursive: true, force: true })
}
