import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, cpSync, readFileSync, writeFileSync, existsSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const source = fileURLToPath(new URL('../../', import.meta.url));
function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), 'wearless-legal-export-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  mkdirSync(join(root, 'tools'));
  mkdirSync(join(root, 'src/lib'), { recursive: true });
  cpSync(join(source, 'tools/legal_publish.py'), join(root, 'tools/legal_publish.py'));
  cpSync(join(source, 'documents/legal'), join(root, 'documents/legal'), { recursive: true });
  const companyPath = join(source, 'src/lib/companyInfo.json');
  cpSync(companyPath, join(root, 'src/lib/companyInfo.json'));
  const landing = join(root, 'landing');
  mkdirSync(landing);
  writeFileSync(join(landing, 'package.json'), '{}');
  return { root, landing, run: () => spawnSync('python3', [join(root, 'tools/legal_publish.py'), '--landing-root', landing], { encoding: 'utf8' }) };
}

test('publisher exports only Wearless landing documents with canonical root-domain links', (t) => {
  const f = fixture(t);
  const result = f.run();
  assert.equal(result.status, 0, result.stderr || result.stdout);
  const manifestPath = join(f.landing, 'content/legal/manifest.json');
  assert.ok(existsSync(manifestPath), 'landing manifest must be exported');
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
  assert.deepEqual(manifest.map(({ path }) => path), ['/terms', '/privacy', '/refund', '/model-license-terms']);
  for (const doc of manifest) {
    assert.equal(doc.canonicalUrl, `https://wearless.kr${doc.path}`);
    assert.equal(readFileSync(join(f.landing, `content/legal/${doc.slug}.md`), 'utf8'), readFileSync(join(f.root, `public/legal/${doc.slug}.md`), 'utf8'));
    assert.equal(readFileSync(join(f.landing, `content/legal/${doc.slug}.md`), 'utf8').endsWith('\n\n'), false, doc.slug);
  }
  const shared = readFileSync(join(f.landing, 'content/legal/seller-license-terms.md'), 'utf8');
  assert.match(shared, /\]\(https:\/\/wearless\.kr\/refund\)/);
  assert.doesNotMatch(shared, /https:\/\/ai\.wearless\.kr\/(?:terms|refund|privacy)/);
  assert.ok(!existsSync(join(f.landing, 'content/legal/terms-model.md')), 'FaceMarket terms stay on FaceMarket');
  const company = JSON.parse(readFileSync(join(f.landing, 'content/legal/company-info.json'), 'utf8'));
  assert.deepEqual(company, JSON.parse(readFileSync(join(f.root, 'src/lib/companyInfo.json'), 'utf8')));
});

test('publisher does not export unresolved internal placeholders into landing content', (t) => {
  const f = fixture(t);
  const path = join(f.root, 'documents/legal/10_wearless_terms_of_service_v1.md');
  writeFileSync(path, readFileSync(path, 'utf8').replace(/(## 제1조[^\n]*\n)/, '$1\n[담당자 미확정]\n'));
  const result = f.run();
  assert.notEqual(result.status, 0);
  assert.ok(!existsSync(join(f.landing, 'content/legal/manifest.json')));
  assert.match(result.stdout, /담당자 미확정/);
});
