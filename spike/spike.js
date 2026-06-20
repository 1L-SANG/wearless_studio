#!/usr/bin/env node
// 마네킹 착장 프롬프트 테스트 하네스 (일회용, src/와 무관)
//
// 사용법: node spike/spike.js <상의사진폴더> --base <마네킹> --prompt-file <프롬프트.txt> [옵션]
//   --base <이미지>       (필수) 베이스 마네킹 이미지 = 1번째 첨부
//   --prompt-file <파일>  (필수) 시스템 프롬프트 텍스트 파일. ${clothingType}/${productCount} 치환됨
//   --match <이미지>      (선택) 하의/매칭 의류 = 마지막 첨부
//   --product <json>      (선택) 분석 정보 — 프롬프트 끝에 ground-truth로 자동 주입
//   --type <의류타입>     기본 '상의' (${clothingType})
//   --res 1K|2K|4K        기본 1K (대문자 K)
//   --temp <0~1>          generationConfig.temperature. 미지정=모델 기본값
//   --model <모델명>      기본: SPIKE_MODEL > MODEL_ROUTING_IMAGE_HIGH > gemini-3-pro-image
//   --n <개수>            생성 횟수. 기본 1
//   --dry-run             호출 없이 요청 미리보기만
//
// 첨부 순서: [1]base · [2..]상의폴더(파일명 정렬순) · [마지막]match(있을 때)
// 키: spike/.env 의 GEMINI_API_KEY (AI Studio AIza… / 없으면 자동 dry-run)
// 결과: spike/runs/<타임스탬프>-mine/result-N.jpg + report.html

import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { parseArgs } from 'node:util';
import { fileURLToPath } from 'node:url';

const SPIKE_DIR = path.dirname(fileURLToPath(import.meta.url));
const USD_BY_RES = { '1K': 0.134, '2K': 0.134, '4K': 0.24 }; // gemini-3-pro-image (2026-06-12 가격표)
function geminiCostLabel(res, model) {
  if (/flash/i.test(model)) return res === '1K' ? '$0.067 (1K, flash)' : `${res} flash — 가격표 재확인`;
  return `$${USD_BY_RES[res].toFixed(3)} (${res}, pro)`;
}

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
    base: { type: 'string' },
    match: { type: 'string' },
    'prompt-file': { type: 'string' },
    product: { type: 'string' },
    type: { type: 'string', default: '상의' },
    res: { type: 'string', default: '1K' },
    temp: { type: 'string' },
    model: { type: 'string' },
    n: { type: 'string', default: '1' },
    'dry-run': { type: 'boolean', default: false },
  },
});

const photoDir = positionals[0];

const config = {
  clothingType: opts.type,
  base: opts.base,
  match: opts.match || null,
  promptFile: opts['prompt-file'] || null,
  product: opts.product ? JSON.parse(fs.readFileSync(opts.product, 'utf8')) : null,
  res: opts.res,
  temp: opts.temp != null ? parseFloat(opts.temp) : null,
  n: parseInt(opts.n, 10),
  model: opts.model || env.SPIKE_MODEL || env.MODEL_ROUTING_IMAGE_HIGH || 'gemini-3-pro-image',
  dryRun: opts['dry-run'] || !env.GEMINI_API_KEY,
  // 인증 경로: VERTEX_PROJECT 있으면 Vertex, 아니면 AI Studio. 둘 다 키는 GEMINI_API_KEY.
  vertexProject: env.VERTEX_PROJECT || '',
  vertexLocation: env.VERTEX_LOCATION || 'global',
};

if (!photoDir) {
  console.error('사용법: node spike/spike.js <상의폴더> --base <마네킹> --prompt-file <프롬프트.txt> [--match <하의>]');
  process.exit(1);
}
if (!config.base) {
  console.error('--base <베이스 마네킹 이미지>가 필요합니다.');
  process.exit(1);
}
if (!config.promptFile) {
  console.error('--prompt-file <프롬프트 텍스트 파일>이 필요합니다. (프롬프트는 이 파일에서만 읽습니다)');
  process.exit(1);
}
if (!USD_BY_RES[config.res]) {
  console.error(`--res는 1K|2K|4K (대문자 K): ${config.res}`);
  process.exit(1);
}

// ---------- 입력 사진 ----------

const MIME = { '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp' };

function loadImage(file) {
  const mime = MIME[path.extname(file).toLowerCase()];
  if (!mime || !fs.existsSync(file)) {
    console.error(`이미지 파일이 아니거나 없습니다: ${file}`);
    process.exit(1);
  }
  const buf = fs.readFileSync(file);
  return { name: path.basename(file), srcPath: file, mime, base64: buf.toString('base64'), bytes: buf.length };
}

function loadPhotos(dir) {
  if (!fs.existsSync(dir) || !fs.statSync(dir).isDirectory()) {
    console.error(`상의 사진 폴더가 없습니다: ${dir}`);
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

// ---------- 프롬프트 ----------
// 프롬프트는 --prompt-file 한 곳에서만 온다. 분석 정보(상품명·색·핏·소재·강조특징)만 끝에 자동 주입.

function productBlock() {
  const p = config.product;
  if (!p) return '';
  const lines = [
    p.name && `- Product name: ${p.name}`,
    p.color && `- Color: ${p.color}`,
    (p.clothingType || p.subCategory) &&
      `- Category: ${[p.clothingType, p.subCategory].filter(Boolean).join(' / ')}`,
    p.targetGender && `- Target gender: ${p.targetGender}`,
    p.fit && `- Fit: ${p.fit}`,
    p.materials && `- Material: ${[].concat(p.materials).join(', ')}`,
    p.measurements && `- Measurements: ${p.measurements}`,
    p.sellingPoints && `- Key features: ${[].concat(p.sellingPoints).join('; ')}`,
  ].filter(Boolean);
  return `PRODUCT CONTEXT (seller-confirmed analysis — treat as ground truth, never contradict it; use it to keep the garment's color, fit, and any collaboration logo faithful):\n${lines.join('\n')}`;
}

function buildPrompt() {
  const productCount = photos.length - 1 - (config.match ? 1 : 0); // 베이스·하의 제외 = 상의 장수
  const prompt = fs
    .readFileSync(config.promptFile, 'utf8')
    .replaceAll('${clothingType}', config.clothingType)
    .replaceAll('${productCount}', String(productCount));
  const ctx = productBlock();
  return ctx ? `${prompt}\n\n${ctx}` : prompt;
}

// ---------- Gemini 호출 ----------

const EXT_BY_MIME = { 'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp' };

function buildRequestBody(prompt, photos) {
  return {
    contents: [
      {
        role: 'user', // Vertex 필수 (AI Studio는 생략 허용 — 양쪽 다 OK)
        parts: [
          { text: prompt },
          ...photos.map((p) => ({ inline_data: { mime_type: p.mime, data: p.base64 } })),
        ],
      },
    ],
    generationConfig: {
      responseModalities: ['TEXT', 'IMAGE'],
      imageConfig: { imageSize: config.res },
      ...(config.temp != null ? { temperature: config.temp } : {}),
    },
  };
}

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
  const body = buildRequestBody(prompt, photos);
  body.contents[0].parts = body.contents[0].parts.map((p) =>
    p.inline_data ? { inline_data: { mime_type: p.inline_data.mime_type, data: `<base64 ${p.inline_data.data.length} chars>` } } : p,
  );
  return body;
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
      const cost = geminiCostLabel(config.res, config.model);
      const checklist = CHECKLIST.map(
        (c) => `<tr><td>${c}</td><td class="c"><input type="checkbox"></td><td contenteditable="true"></td></tr>`,
      ).join('');
      return `
  <section>
    <h2>결과 ${esc(r.label)}</h2>
    <div class="compare">
      <div><h3>원본(첨부)</h3><div class="strip">${inputStrip}</div></div>
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
<title>스파이크 리포트 — ${esc(new Date().toLocaleString('ko-KR'))}</title>
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
  모델: <b>${esc(config.model)}</b> · 의류: ${esc(config.clothingType)} · 해상도 ${esc(config.res)}${config.temp != null ? ` · temp ${config.temp}` : ''}<br>
  입력: ${esc(photoDir)} (${photos.length}장${config.match ? ', 하의 포함' : ''}) · 모드: ${config.dryRun ? '<b>dry-run</b>' : '실호출'} · ${esc(new Date().toISOString())}
</p>
${sections}
</body>
</html>`;
  fs.writeFileSync(path.join(runDir, 'report.html'), html);
}

// ---------- 실행 ----------

// 첨부 순서: [1]base · [2..]상의폴더 · [마지막]match(있을 때)
const photos = [loadImage(config.base), ...loadPhotos(photoDir), ...(config.match ? [loadImage(config.match)] : [])];

const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
const runDir = path.join(SPIKE_DIR, 'runs', `${stamp}-mine`);
fs.mkdirSync(path.join(runDir, 'input'), { recursive: true });
for (const p of photos) {
  fs.copyFileSync(p.srcPath, path.join(runDir, 'input', p.name));
}

console.log(`모드: ${config.dryRun ? 'dry-run' + (env.GEMINI_API_KEY ? '' : ' (GEMINI_API_KEY 없음)') : '실호출'}`);
console.log(`모델: ${config.model} · 입력 ${photos.length}장${config.match ? ' (하의 포함)' : ''} · 생성 ${config.n}회\n`);

const labels = Array.from({ length: config.n }, (_, i) => String(i + 1));
const prompt = buildPrompt();

const results = [];
for (const label of labels) {
  fs.writeFileSync(path.join(runDir, `prompt-${label}.txt`), prompt);
  fs.writeFileSync(path.join(runDir, `request-${label}.json`), JSON.stringify(requestPreview(prompt, photos), null, 2));

  if (config.dryRun) {
    console.log(`[${label}] dry-run — request-${label}.json 작성`);
    results.push({ label, prompt, file: null, latencyMs: null, usage: null, error: null });
    continue;
  }

  try {
    process.stdout.write(`[${label}] 호출 중... `);
    const r = await callGemini(prompt, photos);
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
