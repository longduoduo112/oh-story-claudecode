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

function markProtected(text) {
  const protectedUnits = new Uint8Array(text.length);
  for (const pattern of PROTECTED_PATTERNS) {
    pattern.lastIndex = 0;
    for (const match of text.matchAll(pattern)) {
      const start = match.index;
      const end = start + match[0].length;
      protectedUnits.fill(1, start, end);
    }
  }
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

function scan(text) {
  const protectedUnits = markProtected(text);
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
      if (protectedUnits[end] || !isBridge(next)) break;
      end += next.length;
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

  const findings = scan(text);
  const result = {
    status: findings.length === 0 ? 'passed' : 'rejected',
    file: inputPath,
    findings,
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
