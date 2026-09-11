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

test('publisher uses the actual price launch date in both metadata and document bodies', (t) => {
  const f = fixture(t);
  const result = f.run();
  assert.equal(result.status, 0, result.stderr || result.stdout);

  const manifest = JSON.parse(readFileSync(join(f.root, 'public/legal/manifest.json'), 'utf8'));
  assert.ok(manifest.length > 0);
  // 문서별 개정(DOC_REVISIONS)만 다른 값을 갖는다 — 한 문서를 고쳤다고 나머지 7종의
  // 시행일까지 미래로 밀면 그건 거짓말이 된다.
  const revised = { 'privacy-model': { version: 'v1.2', effectiveDate: '2026-09-12' } };
  for (const { slug, version, effectiveDate } of manifest) {
    const expected = revised[slug] || { version: 'v1.1', effectiveDate: '2026-09-11' };
    assert.deepEqual({ version, effectiveDate }, expected, slug);
  }

  const agreement = readFileSync(join(f.root, 'public/legal/license-agreement.md'), 'utf8');
  assert.match(agreement, /2026년 9월 11일부터 적용한다/);
  assert.doesNotMatch(agreement, /2026년 9월 7일부터 적용한다/);

  const datedSellerDocuments = [
    ['terms-seller.md', /2026년 9월 11일\*\*부터 시행합니다/],
    ['privacy-seller.md', /2026년 9월 11일\*\*부터 적용됩니다/],
    ['refund.md', /2026년 9월 11일\*\*부터 시행합니다/],
  ];
  for (const [file, effectiveCopy] of datedSellerDocuments) {
    assert.match(readFileSync(join(f.root, `public/legal/${file}`), 'utf8'), effectiveCopy, file);
  }
});

// ── 공개본이 정본(documents/legal)과 갈라지지 않았는가 ─────────────────────────
// 최종리뷰 I1: Task13 이 정본(documents/legal/03_…)만 고치고 실제로 /privacy 로 서빙되는
// public/legal/privacy-model.md 를 안 고쳐서, **게시된 처리방침이 "신분증 원본 이미지를
// 저장하지 않는다"고 계속 말하는 상태**로 출시될 뻔했다. 퍼블리셔가 있는데 안 돌린 것이
// 원인이라 — 커밋된 공개본이 "지금 정본으로 다시 찍은 결과"와 바이트 단위로 같은지 본다.
test('committed public/legal matches a fresh publish from documents/legal', (t) => {
  const f = fixture(t);
  const result = f.run();
  assert.equal(result.status, 0, result.stderr || result.stdout);
  const fresh = JSON.parse(readFileSync(join(f.root, 'public/legal/manifest.json'), 'utf8'));
  const committedManifest = join(source, 'public/legal/manifest.json');
  assert.equal(
    readFileSync(join(f.root, 'public/legal/manifest.json'), 'utf8'),
    readFileSync(committedManifest, 'utf8'),
    'public/legal/manifest.json 이 정본과 어긋난다 — `python3 tools/legal_publish.py` 를 돌리고 커밋하세요',
  );
  for (const { slug, source: sourceFile } of fresh) {
    assert.equal(
      readFileSync(join(f.root, `public/legal/${slug}.md`), 'utf8'),
      readFileSync(join(source, `public/legal/${slug}.md`), 'utf8'),
      `public/legal/${slug}.md 가 정본(documents/legal/${sourceFile})과 어긋난다 — `
      + '정본만 고치고 공개본을 안 고치면 사용자가 읽는 문서는 옛 내용 그대로다. '
      + '`python3 tools/legal_publish.py` 를 돌리고 커밋하세요',
    );
  }
});

test('간편인증 경로의 신분증 촬영본 고지가 **게시본**에 실제로 들어 있다', () => {
  // 위 테스트는 "정본과 같은가"만 본다 — 정본에서 문구가 통째로 빠지면 둘 다 조용히
  // 같아진다. 이 고지는 스펙 §7.1 이 출시 전제로 못박은 것이라 게시본에서 직접 확인한다.
  const published = readFileSync(join(source, 'public/legal/privacy-model.md'), 'utf8');
  assert.match(published, /간편인증 경로/, '게시된 처리방침에 간편인증 경로 고지가 없다');
  assert.match(published, /7일/, '보관 상한(7일) 고지가 없다');
  assert.doesNotMatch(
    published,
    /본인확인은 본인확인기관과 모바일 신분증 검증 서비스를 통해 이루어지며, 회사는 \*\*신분증 원본 이미지를 저장하지 않고\*\*/,
    '옛 문장("신분증 원본 이미지를 저장하지 않고")이 게시본에 그대로 남아 있다',
  );
});
