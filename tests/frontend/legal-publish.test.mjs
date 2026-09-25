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

test('publisher keeps each document revision date consistent in metadata and body', (t) => {
  const f = fixture(t);
  const result = f.run();
  assert.equal(result.status, 0, result.stderr || result.stdout);

  const manifest = JSON.parse(readFileSync(join(f.root, 'public/legal/manifest.json'), 'utf8'));
  assert.ok(manifest.length > 0);
  // 문서별 개정(DOC_REVISIONS)만 다른 값을 갖는다 — 한 문서를 고쳤다고 나머지 7종의
  // 시행일까지 미래로 밀면 그건 거짓말이 된다.
  // 등록 위저드 동의·안내 문서 2종은 서버 동의 버전(BIOMETRIC_CONSENT_VERSION)을 따른다.
  // 2026-09-v2 · privacy-model v1.3: 등록 사진이 "얼굴 8·상반신 5·전신 5" → "얼굴 16장"으로
  // 바뀌면서 세 문서의 수집 항목 문구가 같이 바뀌었다(#298).
  // 2026-09-v3 · privacy-model v1.4: 좌·우 옆모습과 뒷모습이 더해져 "등록 사진 18장"이 됐고,
  // 동의서에 사진 확인(담당자 열람·기록)과 사용 시점(테스트컷 승인 이후) 두 항이 생겼다.
  // 2026-09-v4 · privacy-model v1.5 · license-agreement/seller-license-terms/answers v1.2(9/18):
  // 학습 가중치를 회사가 직접 학습한다는 것, 얼굴 부분 생성 GPU 서버(RunPod) 위탁, 학습 사본·
  // 가중치의 보관·파기 범위가 더해졌고, 받지 않는 사이즈·스타일과 "만료" 표기를 뺐다.
  const revised = {
    'terms-seller': { version: 'v1.2', effectiveDate: '2026-09-29' },
    'refund': { version: 'v1.2', effectiveDate: '2026-09-29' },
    'biometric-consent': { version: '2026-09-v4', effectiveDate: '2026-09-18' },
    'overseas-transfer': { version: '2026-09-v4', effectiveDate: '2026-09-18' },
    'license-agreement': { version: 'v1.2', effectiveDate: '2026-09-18' },
    'seller-license-terms': { version: 'v1.2', effectiveDate: '2026-09-18' },
    // 선택 의류 협찬 개정(2026-09-23, 오너 결정으로 예고 기간 없이 시행. 이용자 0명 단계):
    // 모델 약관 v1.2 · 처리방침 v1.6 · 법적 FAQ v1.3 · E-2 협찬 동의.
    'terms-model': { version: 'v1.3', effectiveDate: '2026-09-25' },
    'privacy-model': { version: 'v1.6', effectiveDate: '2026-09-23' },
    'answers': { version: 'v1.4', effectiveDate: '2026-09-25' },
    'sponsorship-consent': { version: '2026-09-sponsorship-v2', effectiveDate: '2026-09-25' },
    // 요청·배송 기능용 델타(02·05 v3)는 아직 초안 — 시행일 미정.
    'license-agreement-sponsorship-draft': { version: 'v3-draft', effectiveDate: null },
    'seller-license-terms-sponsorship-draft': { version: 'v3-draft.1', effectiveDate: null },
  };
  for (const { slug, version, effectiveDate } of manifest) {
    const expected = revised[slug] || { version: 'v1.1', effectiveDate: '2026-09-11' };
    assert.deepEqual({ version, effectiveDate }, expected, slug);
  }
  for (const slug of ['biometric-consent', 'overseas-transfer']) {
    assert.ok(manifest.some((item) => item.slug === slug), `${slug} 항목이 manifest 에 있어야 한다`);
  }

  const agreement = readFileSync(join(f.root, 'public/legal/license-agreement.md'), 'utf8');
  assert.match(agreement, /2026년 9월 18일부터 적용한다/);
  assert.doesNotMatch(agreement, /2026년 9월 7일부터 적용한다/);

  const datedSellerDocuments = [
    ['terms-seller.md', /2026년 9월 29일\*\*부터 시행합니다/],
    ['privacy-seller.md', /2026년 9월 11일\*\*부터 적용됩니다/],
    ['refund.md', /2026년 9월 29일\*\*부터 시행합니다/],
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
test('checked-in generated legal documents match the canonical publisher output', (t) => {
  const f = fixture(t);
  const result = f.run();
  assert.equal(result.status, 0, result.stderr || result.stdout);

  const generatedManifest = JSON.parse(
    readFileSync(join(f.root, 'public/legal/manifest.json'), 'utf8'),
  );
  for (const { slug, source: sourceFile } of generatedManifest) {
    // These two wizard documents are manually maintained public inputs, not
    // outputs of legal_publish.py. The manifest intentionally includes both.
    if (slug === 'biometric-consent' || slug === 'overseas-transfer') {
      assert.ok(existsSync(join(source, `public/legal/${slug}.md`)), slug);
      continue;
    }
    assert.equal(
      readFileSync(join(source, `public/legal/${slug}.md`), 'utf8'),
      readFileSync(join(f.root, `public/legal/${slug}.md`), 'utf8'),
      `public/legal/${slug}.md 가 정본(documents/legal/${sourceFile})과 어긋난다 — `
      + '정본만 고치고 공개본을 안 고치면 사용자가 읽는 문서는 옛 내용 그대로다. '
      + '`python3 tools/legal_publish.py` 를 돌리고 커밋하세요',
    );
  }
  assert.equal(
    readFileSync(join(source, 'public/legal/manifest.json'), 'utf8'),
    readFileSync(join(f.root, 'public/legal/manifest.json'), 'utf8'),
    'public/legal/manifest.json 이 정본과 어긋난다 — `python3 tools/legal_publish.py` 를 돌리고 커밋하세요',
  );
  assert.equal(
    readFileSync(join(source, 'public/llms.txt'), 'utf8'),
    readFileSync(join(f.root, 'public/llms.txt'), 'utf8'),
  );
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

test('published model-license terms do not retain duration-based rights or refunds', (t) => {
  const f = fixture(t);
  const result = f.run();
  assert.equal(result.status, 0, result.stderr || result.stdout);

  const sellerTerms = readFileSync(join(f.root, 'public/legal/seller-license-terms.md'), 'utf8');
  const refund = readFileSync(join(f.root, 'public/legal/refund.md'), 'utf8');
  assert.doesNotMatch(sellerTerms, /착용컷의 라이선스는 명세의 유효기간 동안 존속/);
  assert.doesNotMatch(sellerTerms, /회사의 귀책[^\n]*잔여 기간에 비례/);
  assert.doesNotMatch(refund, /회사의 귀책[^\n]*잔여 기간에 비례/);
  assert.match(sellerTerms, /기간 중 발행된 착용컷의 라이선스는 별도의 만료일 없이 존속/);
  assert.doesNotMatch(sellerTerms, /모델의 철회나 제7조의 취소가 없는 한 존속/);
  assert.match(sellerTerms, /모델이 철회해도 존속하며, 제7조에 따라 취소될 때 종료/);

  const llms = readFileSync(join(f.root, 'public/llms.txt'), 'utf8');
  assert.doesNotMatch(llms, /기발행 건은 기간 만료까지 존속/);
  assert.match(llms, /기발행 건은 철회 후에도 존속/);

  const answers = readFileSync(join(f.root, 'public/legal/answers.md'), 'utf8');
  assert.doesNotMatch(answers, /허용 품목·제외 품목·기간/);
  assert.match(answers, /허용 품목·제외 품목\)과 플랫폼 표준가/);
});


test('협찬 발행본은 게시 기한과 유지 기간만 개정하고 게시 매체와 횟수를 유지해요', t => {
  const f = fixture(t);
  const result = f.run();
  assert.equal(result.status, 0, result.stderr || result.stdout);
  for (const slug of ['terms-model', 'answers', 'sponsorship-consent', 'seller-license-terms-sponsorship-draft']) {
    const text = readFileSync(join(f.root, `public/legal/${slug}.md`), 'utf8');
    assert.match(text, /옷 수령 후 7일 이내/);
    assert.match(text, /게시일부터 90일/);
    assert.match(text, /본인 인스타그램 피드에 착용 게시물 1회/);
    assert.doesNotMatch(text, /옷 수령 후 3일 이내|게시일부터 30일/);
  }
  const manifest = JSON.parse(readFileSync(join(f.root, 'public/legal/manifest.json'), 'utf8'));
  assert.equal(manifest.find(row => row.slug === 'seller-license-terms-sponsorship-draft').revisionDate, '2026-09-25');
  assert.equal(manifest.find(row => row.slug === 'license-agreement-sponsorship-draft').revisionDate, '2026-09-22');
});
