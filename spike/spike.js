#!/usr/bin/env node
// AI 품질 스파이크 하네스 (backend_integration_plan.md §10 0단계 — 일회용, src/와 무관)
//
// 사용법:  node spike/spike.js <상품사진폴더> [옵션]
//   --scenario mannequin|cut|swap|base   기본 mannequin (AG-04). cut = AG-06.
//                              swap = 베이스 마네킹컷에 의류만 교체 (구도 고정 검증)
//                              base = 빈 마네킹 베이스 생성 (사진 폴더 불필요)
//   --base <이미지>             swap 필수: 베이스 마네킹컷 파일
//   --type <의류타입>           기본 '상의'
//   --fit slim|regular|loose   기본 regular
//   --cut-type styling|horizon|product   scenario=cut일 때만. 기본 styling
//   --n <개수>                 생성 횟수. 기본 2 (mannequin이면 A/B 후보)
//   --gender female|male       base 마네킹 성별. 기본 female
//   --res 1K|2K|4K             출력 해상도. 기본 1K (대문자 K 필수)
//   --provider gemini|openai   기본 gemini. openai = gpt-image-2 (현재 base 생성만 지원)
//   --model <모델명>            기본: SPIKE_MODEL > MODEL_ROUTING_IMAGE_HIGH > gemini-3-pro-image
//                              (openai면 OPENAI_IMAGE_MODEL > gpt-image-2)
//   --dry-run                  키가 있어도 호출 없이 요청 미리보기만 생성
//
// 키가 없으면 자동으로 dry-run. 키는 spike/.env 또는 셸 환경변수(GEMINI_API_KEY / OPENAI_API_KEY).
// 결과: spike/runs/<타임스탬프>/report.html (원본↔결과 비교 + 품질 체크리스트)

import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { parseArgs } from 'node:util';
import { fileURLToPath } from 'node:url';

const SPIKE_DIR = path.dirname(fileURLToPath(import.meta.url));
// gemini-3-pro-image standard 단가 (2026-06-12 공식 가격표) · 참고: 3.1-flash-image 1K는 $0.067
const USD_BY_RES = { '1K': 0.134, '2K': 0.134, '4K': 0.24 };

// ---------- 설정 ----------

function loadDotEnv(file) {
  if (!fs.existsSync(file)) return {};
  const out = {};
  for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
    if (line.trim().startsWith('#')) continue;
    const m = line.match(/^\s*([A-Za-z0-9_]+)\s*=\s*(.*?)\s*$/);
    if (m) out[m[1]] = m[2];
  }
  return out;
}

const env = { ...loadDotEnv(path.join(SPIKE_DIR, '.env')), ...process.env };

const { values: opts, positionals } = parseArgs({
  allowPositionals: true,
  options: {
    scenario: { type: 'string', default: 'mannequin' },
    type: { type: 'string', default: '상의' },
    fit: { type: 'string', default: 'regular' },
    'cut-type': { type: 'string', default: 'styling' },
    base: { type: 'string' },
    gender: { type: 'string', default: 'female' },
    res: { type: 'string', default: '1K' },
    provider: { type: 'string', default: 'gemini' },
    n: { type: 'string', default: '2' },
    model: { type: 'string' },
    'dry-run': { type: 'boolean', default: false },
  },
});

const photoDir = positionals[0];
if (!photoDir && opts.scenario !== 'base') {
  console.error('사용법: node spike/spike.js <상품사진폴더> [--scenario mannequin|cut|swap|base] [--dry-run]');
  process.exit(1);
}

const config = {
  scenario: opts.scenario,
  clothingType: opts.type,
  fit: opts.fit,
  cutType: opts['cut-type'],
  base: opts.base,
  gender: opts.gender,
  res: opts.res,
  provider: opts.provider,
  n: parseInt(opts.n, 10),
  model:
    opts.provider === 'openai'
      ? opts.model || env.OPENAI_IMAGE_MODEL || 'gpt-image-2'
      : opts.model || env.SPIKE_MODEL || env.MODEL_ROUTING_IMAGE_HIGH || 'gemini-3-pro-image',
  dryRun: opts['dry-run'] || !(opts.provider === 'openai' ? env.OPENAI_API_KEY : env.GEMINI_API_KEY),
  // 인증 경로: Vertex(GCP 프로젝트 키, AQ.* 형식) vs AI Studio(AIza* 키).
  // VERTEX_PROJECT 가 있으면 Vertex 엔드포인트를 쓴다. 둘 다 키는 GEMINI_API_KEY.
  vertexProject: env.VERTEX_PROJECT || '',
  vertexLocation: env.VERTEX_LOCATION || 'global',
};

if (!['mannequin', 'cut', 'swap', 'base'].includes(config.scenario)) {
  console.error(`--scenario은 mannequin·cut·swap·base 중 하나: ${config.scenario}`);
  process.exit(1);
}
if (config.scenario === 'swap' && !config.base) {
  console.error('swap 시나리오는 --base <베이스 마네킹컷 이미지>가 필요합니다.');
  process.exit(1);
}
if (!USD_BY_RES[config.res]) {
  console.error(`--res는 1K|2K|4K (대문자 K): ${config.res}`);
  process.exit(1);
}
if (!['gemini', 'openai'].includes(config.provider)) {
  console.error(`--provider는 gemini 또는 openai: ${config.provider}`);
  process.exit(1);
}
if (config.provider === 'openai' && config.scenario !== 'base') {
  console.error('openai provider는 현재 base 생성만 지원합니다 (스왑·마네킹은 Gemini가 정본 — ai_pipeline_spec).');
  process.exit(1);
}

// ---------- 입력 사진 ----------

const MIME = { '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp' };

function loadPhotos(dir) {
  if (!fs.existsSync(dir) || !fs.statSync(dir).isDirectory()) {
    console.error(`사진 폴더가 없습니다: ${dir}`);
    process.exit(1);
  }
  const files = fs.readdirSync(dir).filter((f) => MIME[path.extname(f).toLowerCase()]).sort();
  if (files.length === 0) {
    console.error(`폴더에 이미지(jpg/png/webp)가 없습니다: ${dir}`);
    process.exit(1);
  }
  if (files.length > 8) console.warn(`이미지 ${files.length}장 — 앞 8장만 사용합니다.`);
  return files.slice(0, 8).map((f) => loadImage(path.join(dir, f)));
}

function loadImage(file) {
  const mime = MIME[path.extname(file).toLowerCase()];
  if (!mime || !fs.existsSync(file)) {
    console.error(`이미지 파일이 아니거나 없습니다: ${file}`);
    process.exit(1);
  }
  const buf = fs.readFileSync(file);
  return { name: path.basename(file), srcPath: file, mime, base64: buf.toString('base64'), bytes: buf.length };
}

// ---------- 프롬프트 (실험 대상 — 여기를 고쳐가며 품질을 검증한다) ----------

// AG-04 핵심 제약: 의류 구조·디테일·컬러 보존 최우선 · 무지 배경 마네킹 착용 · 모델 얼굴 없음
function buildMannequinPrompt({ clothingType, fit, candidate }) {
  const variation =
    candidate === 'B'
      ? '\nVariation B: render an alternative styling interpretation of the same fit (e.g., sleeves or hem arranged differently), while keeping the garment itself identical.'
      : '';
  return `You are generating a professional e-commerce catalog image for a fashion product.

INPUT: the attached photos show one garment (type: ${clothingType}) from multiple angles.

TASK: generate a single photorealistic image of this exact garment worn by a featureless display mannequin.

HARD CONSTRAINTS, in priority order:
1. Garment identity is paramount: preserve the exact structure, seams, stitching, details, pattern, and color shown in the input photos. Do not redesign, restyle, or recolor anything.
2. Display mannequin only — no human model, no face, no skin texture, no hair.
3. Plain seamless light-neutral studio background. No props.
4. Render the garment with a ${fit} fit on the mannequin.
5. Full garment visible, front-facing, centered, vertical catalog framing.${variation}`;
}

// AG-06 핵심 제약: 상품 동일성 보존 최우선 · 마네킹컷 핏·실루엣 기준 · product 컷은 모델 없음
function buildCutPrompt({ clothingType, cutType }) {
  const scene = {
    styling:
      'a styled editorial shot: the garment worn by a model, face out of frame or cropped above the chin, natural pose, simple lifestyle setting',
    horizon:
      'a full-body studio shot on an infinity-cove (horizon) background: the garment worn by a model, face out of frame, soft even lighting',
    product:
      'a product-only shot with NO model: choose ghost-mannequin, hanger, or flat-lay presentation, plain background',
  }[cutType];
  return `You are generating a professional e-commerce detail-page image for a fashion product.

INPUT: the attached photos show one garment (type: ${clothingType}) from multiple angles.

TASK: generate a single photorealistic image — ${scene}.

HARD CONSTRAINTS, in priority order:
1. Product identity is paramount: the garment in the output must be exactly the one in the input photos — same structure, details, pattern, and color. No redesign, no recolor.
2. Keep the garment's fit and silhouette consistent with how it appears in the input photos.
3. Clean commercial photography quality, vertical framing.`;
}

// base: 모든 스왑의 앵커가 될 빈 마네킹 원본 — 1회 생성해 고정 자산으로 쓴다
function buildBasePrompt() {
  const body =
    config.gender === 'male'
      ? 'a MALE mannequin with natural athletic proportions — broader shoulders, flat chest, narrow hips'
      : 'a FEMALE mannequin with elegant slender proportions, subtle sculpted neck and shoulders';
  return `You are generating the canonical base photo for an e-commerce mannequin photography system.

TASK: generate a single photorealistic studio photo of an empty retail display mannequin, exactly as it ships from the mannequin manufacturer, before any garment is put on it.

HARD CONSTRAINTS, in priority order:
1. Abstract retail display mannequin — ${body}. Smooth seamless body with a MATTE finish (no gloss, no satin sheen, no specular highlights), in cool neutral white. Absolutely no warm, cream, or ivory tint. Featureless egg-shaped head, no face, no hair.
2. FULL BODY visible from the top of the head to the feet, standing upright with BOTH feet flat on the floor — no tiptoe, no raised heel, no stepping pose. NO support rod, NO metal armature, NO base plate: the mannequin stands directly on the studio floor like a real physical mannequin.
3. Plain seamless studio background in VERY light gray, close to white (around #F0F0F1) — clearly lighter than before, airy and bright. Soft even neutral-temperature lighting with only a faint soft shadow. No props, no clothing, no fabric texture on the body.
4. Nearly frontal view with a subtle turn — the mannequin's body rotated only about 20 degrees, facing toward the LEFT side of the frame (viewer's left), like a premium fashion lookbook shot. The head must point in EXACTLY the same direction as the torso — no head turn, no tilt, no looking at the camera; head and chest face the same way, as one rigid piece. Centered, vertical catalog framing. This exact composition will be reused for every product shot, so keep it clean and consistent.`;
}

// swap 검증: 고정 마네킹 구도에 의류만 교체 — "의류만 바뀐다는 느낌"의 핵심 메커니즘
function buildSwapPrompt({ clothingType }) {
  return `You are editing a professional e-commerce mannequin photo.

INPUT: the FIRST attached image is the current mannequin shot (the base). The remaining photos show a NEW garment (type: ${clothingType}).

TASK: produce the same mannequin shot, but with the mannequin now wearing the NEW garment (replacing any garment it currently wears).

HARD CONSTRAINTS, in priority order:
1. Change ONLY the clothing. The mannequin itself, its pose, body turn, camera angle, lighting, and background must remain identical to the base image.
2. Keep the base image's framing EXACTLY — the FULL body from head to feet must stay visible at the same camera distance. Do NOT zoom in, do NOT crop to the torso.
3. New-garment identity is paramount: preserve its exact structure, seams, details, pattern, and color from the garment photos. No redesign, no recolor.
4. Reproduce any logo, embroidery, or graphic print EXACTLY as photographed — same shape, same colors, same position and proportions. Treat it as a protected trademark that must be copied pixel-faithfully, never redrawn or reinterpreted.
5. Drape the new garment naturally on the mannequin with a realistic fit.`;
}

function buildPrompt(label) {
  if (config.scenario === 'base') return buildBasePrompt();
  if (config.scenario === 'swap') return buildSwapPrompt({ clothingType: config.clothingType });
  return config.scenario === 'mannequin'
    ? buildMannequinPrompt({ clothingType: config.clothingType, fit: config.fit, candidate: label })
    : buildCutPrompt({ clothingType: config.clothingType, cutType: config.cutType });
}

// ---------- Gemini 호출 ----------

const EXT_BY_MIME = { 'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp' };

function buildRequestBody(prompt, photos) {
  return {
    contents: [
      {
        role: 'user',   // Vertex 필수 (AI Studio는 생략 허용 — 양쪽 다 OK)
        parts: [
          { text: prompt },
          ...photos.map((p) => ({ inline_data: { mime_type: p.mime, data: p.base64 } })),
        ],
      },
    ],
    // 모델에 따라 ['IMAGE']만 허용할 수도 있음 — 첫 실호출에서 오류 나면 여기 조정
    generationConfig: {
      responseModalities: ['TEXT', 'IMAGE'],
      imageConfig: { imageSize: config.res },
    },
  };
}

// 인증 경로별 엔드포인트 — Vertex(프로젝트 키)면 aiplatform, 아니면 AI Studio.
function endpointFor(model) {
  if (config.vertexProject) {
    const loc = config.vertexLocation;
    const host = loc === 'global' ? 'aiplatform.googleapis.com' : `${loc}-aiplatform.googleapis.com`;
    return `https://${host}/v1/projects/${config.vertexProject}/locations/${loc}/publishers/google/models/${model}:generateContent?key=${env.GEMINI_API_KEY}`;
  }
  return `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${env.GEMINI_API_KEY}`;
}

async function callGemini(prompt, photos) {
  const body = buildRequestBody(prompt, photos);
  const t0 = performance.now();
  const res = await fetch(endpointFor(config.model), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const latencyMs = Math.round(performance.now() - t0);
  if (!res.ok) throw new Error(`Gemini ${res.status}: ${(await res.text()).slice(0, 500)}`);
  const json = await res.json();
  const parts = json.candidates?.[0]?.content?.parts ?? [];
  // 진단용: 파트 구성 출력 (이미지 여러 장이면 가장 큰 것을 채택)
  console.log(
    `\n  parts: [${parts.map((p) => (p.inlineData ? `image ${p.inlineData.data.length}b64` : p.fileData ? `fileData ${p.fileData.fileUri}` : `text ${String(p.text || '').length}ch`)).join(', ')}]`,
  );
  const imagePart = parts
    .filter((p) => p.inlineData?.data)
    .sort((a, b) => b.inlineData.data.length - a.inlineData.data.length)[0];
  if (!imagePart) {
    const text = parts.map((p) => p.text).filter(Boolean).join(' ').slice(0, 300);
    throw new Error(`응답에 이미지가 없음. 텍스트: ${text || '(없음)'}`);
  }
  return {
    buffer: Buffer.from(imagePart.inlineData.data, 'base64'),
    mime: imagePart.inlineData.mimeType || 'image/png',
    latencyMs,
    usage: json.usageMetadata ?? null,
  };
}

// base64를 바이트 수 표기로 바꾼 요청 미리보기 (dry-run 검증용)
function requestPreview(prompt, photos) {
  if (config.provider === 'openai') return buildOpenAIBody(prompt);
  const body = buildRequestBody(prompt, photos);
  body.contents[0].parts = body.contents[0].parts.map((p) =>
    p.inline_data ? { inline_data: { mime_type: p.inline_data.mime_type, data: `<base64 ${p.inline_data.data.length} chars>` } } : p,
  );
  return body;
}

// ---------- OpenAI (gpt-image-2) — base 생성 전용 ----------

// 2:3 세로 프레이밍, 변 16배수·총 픽셀 8,294,400 이하 제약 충족값
const OPENAI_SIZE_BY_RES = { '1K': '896x1344', '2K': '1408x2112', '4K': '2304x3456' };

function buildOpenAIBody(prompt) {
  return { model: config.model, prompt, size: OPENAI_SIZE_BY_RES[config.res] };
}

async function callOpenAI(prompt) {
  const t0 = performance.now();
  const res = await fetch('https://api.openai.com/v1/images/generations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${env.OPENAI_API_KEY}` },
    body: JSON.stringify(buildOpenAIBody(prompt)),
  });
  const latencyMs = Math.round(performance.now() - t0);
  if (!res.ok) throw new Error(`OpenAI ${res.status}: ${(await res.text()).slice(0, 500)}`);
  const json = await res.json();
  const b64 = json.data?.[0]?.b64_json;
  if (!b64) throw new Error(`응답에 이미지가 없음: ${JSON.stringify(json).slice(0, 300)}`);
  return { buffer: Buffer.from(b64, 'base64'), mime: 'image/png', latencyMs, usage: json.usage ?? null };
}

// ---------- 리포트 ----------

const CHECKLIST = ['핏 재현', '의류 동일성', '디테일 보존', '색 정확도'];

function esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function renderReport(runDir, photos, results) {
  const inputStrip = photos
    .map((p) => `<figure><img src="input/${esc(p.name)}" alt=""><figcaption>${esc(p.name)}</figcaption></figure>`)
    .join('');

  const sections = results
    .map((r) => {
      const resultCell = r.error
        ? `<div class="placeholder error">실패: ${esc(r.error)}</div>`
        : r.file
          ? `<img class="result" src="${esc(r.file)}" alt="결과 ${esc(r.label)}">`
          : `<div class="placeholder">dry-run — 결과 없음<br><small>request-${esc(r.label)}.json 참고</small></div>`;
      const cost =
        config.provider === 'openai'
          ? `OpenAI ${config.res} — 가격표 참조`
          : `$${USD_BY_RES[config.res].toFixed(3)} (${config.res})`;
      const checklist = CHECKLIST.map(
        (c) => `<tr><td>${c}</td><td class="c"><input type="checkbox"></td><td contenteditable="true"></td></tr>`,
      ).join('');
      return `
  <section>
    <h2>결과 ${esc(r.label)}</h2>
    <div class="compare">
      <div><h3>원본</h3><div class="strip">${inputStrip}</div></div>
      <div><h3>생성</h3>${resultCell}</div>
    </div>
    <table>
      <tr><th>항목</th><th>통과</th><th>메모</th></tr>
      ${checklist}
      <tr><td>지연</td><td class="c">—</td><td>${r.latencyMs != null ? `${(r.latencyMs / 1000).toFixed(1)}s` : 'dry-run'}</td></tr>
      <tr><td>비용</td><td class="c">—</td><td>${cost}${r.usage ? ` · tokens ${esc(JSON.stringify(r.usage))}` : ''}</td></tr>
    </table>
    <details><summary>프롬프트</summary><pre>${esc(r.prompt)}</pre></details>
  </section>`;
    })
    .join('\n');

  const html = `<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>스파이크 리포트 — ${esc(config.scenario)} ${esc(new Date().toLocaleString('ko-KR'))}</title>
<style>
  body { font-family: -apple-system, sans-serif; margin: 24px; color: #222; }
  h1 { font-size: 20px; } h2 { font-size: 16px; margin-top: 40px; } h3 { font-size: 13px; color: #666; }
  .meta { font-size: 13px; color: #555; line-height: 1.7; }
  .compare { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; align-items: start; }
  .strip { display: flex; flex-wrap: wrap; gap: 8px; }
  .strip img { max-width: 180px; max-height: 240px; border: 1px solid #ddd; }
  figure { margin: 0; } figcaption { font-size: 11px; color: #888; text-align: center; }
  img.result { max-width: 100%; max-height: 560px; border: 1px solid #ddd; }
  .placeholder { border: 2px dashed #bbb; color: #888; padding: 60px 20px; text-align: center; }
  .placeholder.error { border-color: #d66; color: #b33; }
  table { border-collapse: collapse; margin-top: 12px; font-size: 13px; }
  td, th { border: 1px solid #ccc; padding: 6px 12px; min-width: 80px; }
  td[contenteditable] { min-width: 280px; }
  td.c { text-align: center; }
  pre { background: #f6f6f6; padding: 12px; font-size: 12px; white-space: pre-wrap; }
</style>
</head>
<body>
<h1>AI 품질 스파이크 리포트</h1>
<p class="meta">
  시나리오: <b>${esc(config.scenario)}</b>${config.scenario === 'cut' ? ` (${esc(config.cutType)})` : ''} ·
  모델: <b>${esc(config.provider)}/${esc(config.model)}</b> · 의류 타입: ${esc(config.clothingType)} · 핏: ${esc(config.fit)}<br>
  입력: ${esc(photoDir || '(없음 — base 생성)')} (${photos.length}장) · 모드: ${config.dryRun ? '<b>dry-run</b>' : '실호출'} · ${esc(new Date().toISOString())}
</p>
${sections}
</body>
</html>`;
  fs.writeFileSync(path.join(runDir, 'report.html'), html);
}

// ---------- 실행 ----------

let photos = config.scenario === 'base' ? [] : loadPhotos(photoDir);
if (config.scenario === 'swap') photos = [loadImage(config.base), ...photos]; // 첫 장 = 베이스
const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
const runDir = path.join(SPIKE_DIR, 'runs', `${stamp}-${config.scenario}`);
fs.mkdirSync(path.join(runDir, 'input'), { recursive: true });
for (const p of photos) {
  fs.copyFileSync(p.srcPath, path.join(runDir, 'input', p.name));
}

console.log(`모드: ${config.dryRun ? 'dry-run' + (env.GEMINI_API_KEY ? '' : ' (GEMINI_API_KEY 없음)') : '실호출'}`);
console.log(`시나리오: ${config.scenario} · 모델: ${config.model} · 입력 ${photos.length}장 · ${config.n}회 생성\n`);

const labels =
  config.scenario === 'mannequin' && config.n === 2
    ? ['A', 'B']
    : Array.from({ length: config.n }, (_, i) => String(i + 1));

const results = [];
for (const label of labels) {
  const prompt = buildPrompt(label);
  fs.writeFileSync(path.join(runDir, `prompt-${label}.txt`), prompt);
  fs.writeFileSync(
    path.join(runDir, `request-${label}.json`),
    JSON.stringify(requestPreview(prompt, photos), null, 2),
  );

  if (config.dryRun) {
    console.log(`[${label}] dry-run — request-${label}.json 작성`);
    results.push({ label, prompt, file: null, latencyMs: null, usage: null, error: null });
    continue;
  }

  try {
    process.stdout.write(`[${label}] 호출 중... `);
    const r = config.provider === 'openai' ? await callOpenAI(prompt) : await callGemini(prompt, photos);
    const file = `result-${label}${EXT_BY_MIME[r.mime] || '.png'}`;
    fs.writeFileSync(path.join(runDir, file), r.buffer);
    console.log(`완료 ${(r.latencyMs / 1000).toFixed(1)}s → ${file}`);
    results.push({ label, prompt, file, latencyMs: r.latencyMs, usage: r.usage, error: null });
  } catch (e) {
    console.error(`실패: ${e.message}`);
    results.push({ label, prompt, file: null, latencyMs: null, usage: null, error: e.message });
  }
}

renderReport(runDir, photos, results);
fs.writeFileSync(
  path.join(runDir, 'meta.json'),
  JSON.stringify(
    { config, photoDir, photos: photos.map((p) => ({ name: p.name, bytes: p.bytes })), results: results.map(({ prompt, ...r }) => r) },
    null,
    2,
  ),
);

console.log(`\n리포트: ${path.join(runDir, 'report.html')}`);
console.log(`열기:   open "${path.join(runDir, 'report.html')}"`);
