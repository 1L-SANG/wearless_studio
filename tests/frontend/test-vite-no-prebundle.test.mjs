import test from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const thisFile = fileURLToPath(import.meta.url);
const frontendDir = dirname(thisFile);

function mjsFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return mjsFiles(path);
    return entry.isFile() && path.endsWith('.mjs') && path !== thisFile ? [path] : [];
  });
}

test('Vite frontend test servers disable dependency discovery for every call', () => {
  const missing = [];
  for (const path of mjsFiles(frontendDir)) {
    const source = readFileSync(path, 'utf8');
    if (!/\bfrom\s*['"]vite['"]|\bimport\s*\(\s*['"]vite['"]\s*\)/.test(source)) continue;

    // Mask comments and strings so their braces and option text cannot affect the check.
    const code = source.replace(
      /\/\/[^\n]*|\/\*[\s\S]*?\*\/|'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|`(?:\\.|[^`\\])*`/g,
      (match) => match.replace(/[^\n]/g, ' '),
    );
    for (const call of code.matchAll(/\bcreateServer\s*\(\s*\{/g)) {
      const start = call.index + call[0].length - 1;
      let depth = 1;
      let end = start + 1;
      for (; end < code.length && depth > 0; end += 1) {
        if (code[end] === '{') depth += 1;
        if (code[end] === '}') depth -= 1;
      }
      const config = code.slice(start, end);
      if (!/\boptimizeDeps\s*:\s*\{[^}]*\bnoDiscovery\s*:\s*true\b/.test(config)) {
        const line = source.slice(0, call.index).split('\n').length;
        missing.push(`tests/frontend/${relative(frontendDir, path)}:${line}`);
      }
    }
  }
  assert.equal(missing.length, 0,
    `Vite 테스트 서버에 optimizeDeps: { noDiscovery: true, include: [] }를 넣어 주세요. 빠지면 node_modules/.vite에 임시 폴더가 쌓입니다. ${missing.join(', ')}`);
});
