#!/usr/bin/env node

/**
 * Hard gate for Chinese prose.
 *
 * The gate reports foreign-language fragments and exits non-zero. It never
 * rewrites prose and never creates whitelist entries. The narrative writer is
 * responsible for revising the reported sentences and running the gate again.
 */

const fs = require('fs');
const path = require('path');

const PROTECTED_PATTERNS = [
  /```[\s\S]*?```/g,
  /`[^`\n]+`/g,
  /https?:\/\/[^\s<>()]+/gi,
  /\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b/g,
  /(?:^|[\s（(《“"'])[^\s]+\.(?:md|txt|json|ya?ml|toml|js|mjs|cjs|ts|tsx|jsx|py|sh|html|css)(?=$|[\s）)》”"'，。！？；：,.!?;:])/gim,
];

function usage() {
  console.error('Usage: node language_gate.js [--json] <chapter-file>');
}

function loadWhitelist(inputPath) {
  let directory = path.dirname(inputPath);
  while (true) {
    const candidate = path.join(directory, '.deslop-whitelist');
    if (fs.existsSync(candidate)) {
      const entries = fs.readFileSync(candidate, 'utf8')
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter((line) => line && !line.startsWith('#'));
      return { path: candidate, entries: [...new Set(entries)] };
    }
    const parent = path.dirname(directory);
    if (parent === directory) return { path: null, entries: [] };
    directory = parent;
  }
}

function isForeignAt(text, index) {
  if (index < 0 || index >= text.length) return false;
  return isForeignLetter(String.fromCodePoint(text.codePointAt(index)));
}

function isWhitelistTokenAt(text, index) {
  if (index < 0 || index >= text.length) return false;
  const char = String.fromCodePoint(text.codePointAt(index));
  return isForeignLetter(char) || /[0-9_.+/#-]/.test(char);
}

function markWhitelistEntries(text, protectedUnits, entries) {
  for (const entry of entries) {
    let offset = 0;
    while (offset <= text.length - entry.length) {
      const start = text.indexOf(entry, offset);
      if (start < 0) break;
      const end = start + entry.length;
      const beforeForeign = isWhitelistTokenAt(text, start - 1);
      const afterForeign = isWhitelistTokenAt(text, end);
      // 多词短句只保护完整短句；“Open the door”不能子串豁免
      // “Open the door now”。单 token 同样不得子串豁免 AidenX。
      const followedByForeignWord = /\s/.test(text[end] || '')
        && isForeignAt(text, end + ((text.slice(end).match(/^\s+/) || [''])[0].length));
      if (!beforeForeign && !afterForeign && !followedByForeignWord) {
        protectedUnits.fill(1, start, end);
      }
      offset = start + Math.max(entry.length, 1);
    }
  }
}

function markProtected(text, whitelistEntries) {
  const protectedUnits = new Uint8Array(text.length);
  for (const pattern of PROTECTED_PATTERNS) {
    pattern.lastIndex = 0;
    for (const match of text.matchAll(pattern)) {
      const start = match.index;
      const end = start + match[0].length;
      protectedUnits.fill(1, start, end);
    }
  }
  markWhitelistEntries(text, protectedUnits, whitelistEntries);
  return protectedUnits;
}

function isForeignLetter(char) {
  const normalized = char.normalize('NFKC');
  if (/[A-Za-z]/.test(normalized)) return true;
  return /\p{Script=Latin}|\p{Script=Greek}|\p{Script=Cyrillic}/u.test(char);
}

function isBridge(char) {
  return isForeignLetter(char) || /[0-9_.+/#-]/.test(char);
}

function lineAndColumn(text, index) {
  const prefix = text.slice(0, index);
  const line = prefix.split('\n').length;
  const lastBreak = prefix.lastIndexOf('\n');
  return { line, column: index - lastBreak };
}

function lineContext(text, index) {
  const start = text.lastIndexOf('\n', index - 1) + 1;
  const nextBreak = text.indexOf('\n', index);
  const end = nextBreak === -1 ? text.length : nextBreak;
  return text.slice(start, end).trim();
}

function scan(text, whitelistEntries = []) {
  const protectedUnits = markProtected(text, whitelistEntries);
  const findings = [];
  let index = 0;

  while (index < text.length) {
    const char = String.fromCodePoint(text.codePointAt(index));
    const width = char.length;
    if (protectedUnits[index] || !isForeignLetter(char)) {
      index += width;
      continue;
    }

    const start = index;
    let end = index + width;
    while (end < text.length) {
      const next = String.fromCodePoint(text.codePointAt(end));
      if (protectedUnits[end]) break;
      if (isBridge(next)) {
        end += next.length;
        continue;
      }
      if (/\s/.test(next)) {
        let cursor = end + next.length;
        while (cursor < text.length) {
          const whitespace = String.fromCodePoint(text.codePointAt(cursor));
          if (!/\s/.test(whitespace)) break;
          cursor += whitespace.length;
        }
        if (cursor < text.length && !protectedUnits[cursor]) {
          const following = String.fromCodePoint(text.codePointAt(cursor));
          if (isForeignLetter(following)) {
            end = cursor;
            continue;
          }
        }
      }
      break;
    }

    const position = lineAndColumn(text, start);
    findings.push({
      severity: 'blocking',
      type: 'mixed-language',
      text: text.slice(start, end),
      line: position.line,
      column: position.column,
      context: lineContext(text, start),
      action: 'return_to_narrative_writer',
    });
    index = end;
  }

  return findings;
}

function main() {
  const args = process.argv.slice(2);
  const jsonOutput = args.includes('--json');
  const positional = args.filter((arg) => !arg.startsWith('--'));
  if (positional.length !== 1) {
    usage();
    process.exit(3);
  }

  const inputPath = path.resolve(positional[0]);
  let text;
  try {
    text = fs.readFileSync(inputPath, 'utf8');
  } catch (error) {
    console.error(`language_gate: ${error.message}`);
    process.exit(3);
  }

  const whitelist = loadWhitelist(inputPath);
  const findings = scan(text, whitelist.entries);
  const result = {
    status: findings.length === 0 ? 'passed' : 'rejected',
    file: inputPath,
    findings,
    whitelist_file: whitelist.path,
    next_action: findings.length === 0
      ? 'continue_workflow'
      : 'revise_reported_sentences_and_rerun_gate',
  };

  if (jsonOutput) {
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  } else if (findings.length === 0) {
    console.log(`PASS: no mixed-language fragments in ${inputPath}`);
  } else {
    console.error(`REJECTED: ${findings.length} mixed-language fragment(s) in ${inputPath}`);
    for (const item of findings) {
      console.error(`  ${item.line}:${item.column} [${item.text}] ${item.context}`);
    }
    console.error('Return this draft to the narrative writer, revise it, then rerun the gate.');
  }

  process.exit(findings.length === 0 ? 0 : 2);
}

main();
