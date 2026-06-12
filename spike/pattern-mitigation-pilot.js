#!/usr/bin/env node
// Pattern mitigation pilot runner.
// Tests prompt-only and prompt+fabric-detail-reference mitigations for
// matching items that failed on fine repeated texture/pattern preservation.

import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import zlib from 'node:zlib';
import crypto from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { parseArgs } from 'node:util';
import { fileURLToPath } from 'node:url';
import { seedMatchingItems } from '../src/mock/seedMatchingItems.js';

const SPIKE_DIR = path.dirname(fileURLToPath(import.meta.url));
const ROOT_DIR = path.resolve(SPIKE_DIR, '..');
const RESOLUTION = '2K';
const REPEATS = 2;
const USD_PER_OUTPUT_IMAGE = 0.134;
const USD_PER_INPUT_IMAGE = 0.0011;
const PROMPT_TEMPLATE_ID = 'pattern-mitigation-v0.1';

const BASE_PROMPT = `Create one realistic Korean fashion e-commerce styling image using the provided reference images.

Reference priority:
1. Main product reference image(s): this is the hero garment. Preserve its color, material impression, silhouette, neckline/waistline, length, pattern, closures, trims, and distinctive details.
2. Matching clothing reference image: use this only as the complementary styling item. Preserve its broad color, garment type, and silhouette, but keep it secondary to the main product.

Scene:
A realistic Korean female model wears the main product together with the matching clothing item.
Use a clean Korean online shopping mall product-detail styling cut.
Show the outfit clearly in a vertical 4:5 composition.
Show the full body including head and feet, with margin above the head and visible space below the feet.
Use a clean white or very light neutral studio background with soft natural shadows.
Keep the styling simple, commercial, and wearable.

Important constraints:
- Do not use a mannequin.
- Do not redesign the main product.
- Do not change the main product color, pattern, fabric impression, or key shape.
- Do not replace the matching item with a different garment type.
- Do not add logos, text, graphics, props, bags, hats, jewelry, or extra layers unless already present in the references.
- If the references conflict, prioritize preserving the main product identity over the matching item.
- Keep body proportions natural and garment fit plausible.`;

const AVOID_PROMPT = `Avoid:
main product color shift, pattern loss, distorted silhouette, changed neckline, changed length, invented logos, extra prints, busy background, gray or tinted product background, cropped outfit, duplicated garments, warped body, unrealistic hands, excessive wrinkles, over-stylized editorial look`;

const TEST_ITEMS = [
  {
    matchingItemId: 'match_top_navy_stripe_shirt',
    mainProductId: 'match_bottom_beige_chino_pants',
    detailCrop: { x: 410, y: 330, w: 430, h: 430 },
    patternInstruction:
      'For the matching shirt, preserve thin regular navy pinstripes, evenly spaced, NOT bold graphic stripes, NOT zebra stripes, NOT irregular wavy stripes.',
    riskReason: 'Observed 3/3 match_pattern_loss in v0.1: thin stripes became bold irregular graphic stripes.',
  },
  {
    matchingItemId: 'match_top_ivory_ribbed_knit',
    mainProductId: 'match_bottom_beige_chino_pants',
    detailCrop: { x: 405, y: 300, w: 440, h: 440 },
    patternInstruction:
      'For the matching knit top, preserve fine vertical ribbed knit texture, NOT cable knit, NOT wavy raised patterns, NOT chunky sweater texture.',
    riskReason: 'Observed 3/3 match_pattern_loss in v0.1: fine ribbing became wavy/cable-like knit texture.',
  },
];

const VARIANTS = [
  {
    id: 'base',
    label: 'framing-only baseline',
    usesPatternInstruction: false,
    usesDetailReference: false,
  },
  {
    id: 'pattern_prompt',
    label: 'A: explicit pattern prompt',
    usesPatternInstruction: true,
    usesDetailReference: false,
  },
  {
    id: 'pattern_prompt_detail',
    label: 'A+B: explicit pattern prompt plus fabric detail reference',
    usesPatternInstruction: true,
    usesDetailReference: true,
  },
];

const FAILURE_ENUM = [
  'pass',
  'match_color_shift',
  'match_pattern_loss',
  'match_silhouette_distortion',
  'main_product_changed',
  'type_confusion',
  'fit_implausible',
  'background_leak',
  'prompt_ignored',
];

const RISK_CLASSIFICATION = [
  ['match_top_white_oxford_shirt', 'stable', 'heuristic_solid_basic', 'Solid white shirt; no fine repeated pattern. Buttons/collar still worth future normal coverage.'],
  ['match_top_ivory_ribbed_knit', 'risky', 'observed_fail', 'Fine vertical ribbed knit failed 3/3 in v0.1.'],
  ['match_top_black_turtleneck_knit', 'stable', 'observed_pass', 'Solid dark turtleneck passed 3/3 in v0.1.'],
  ['match_top_gray_basic_tshirt', 'stable', 'observed_anchor_pass', 'Solid gray T-shirt remained stable as main-product anchor.'],
  ['match_top_white_basic_tshirt', 'stable', 'heuristic_solid_basic', 'Solid basic T-shirt; no fine repeated pattern.'],
  ['match_top_black_basic_tshirt', 'stable', 'heuristic_solid_basic', 'Solid basic T-shirt; no fine repeated pattern.'],
  ['match_top_navy_stripe_shirt', 'risky', 'observed_fail', 'Thin regular stripes failed 3/3 in v0.1.'],
  ['match_top_beige_round_knit', 'risky', 'heuristic_micro_texture', 'Visible knit texture resembles the failed rib/knit family; needs mitigation or replacement check.'],
  ['match_top_cream_vneck_blouse', 'stable', 'heuristic_solid_basic', 'Solid blouse; no fine repeated pattern.'],
  ['match_top_black_basic_sweatshirt', 'stable', 'heuristic_solid_basic', 'Solid sweatshirt; no fine repeated pattern.'],
  ['match_top_charcoal_regular_shirt', 'stable', 'heuristic_solid_basic', 'Solid charcoal shirt; no fine repeated pattern.'],
  ['match_top_white_layered_tshirt', 'stable', 'heuristic_solid_basic', 'Solid layered T-shirt; no fine repeated pattern.'],
  ['match_bottom_midblue_straight_denim', 'stable', 'heuristic_simple_texture', 'Denim texture is broad/noise-like rather than fine repeated pattern.'],
  ['match_bottom_black_semiwide_slacks', 'stable', 'observed_pass', 'Solid black slacks passed 3/3 in v0.1.'],
  ['match_bottom_ivory_cotton_pants', 'stable', 'heuristic_solid_basic', 'Solid cotton pants; no fine repeated pattern.'],
  ['match_bottom_white_cotton_pants', 'stable', 'observed_pass', 'White cotton pants passed 2/3, with one framing issue rather than clothing identity issue.'],
  ['match_bottom_black_cotton_pants', 'stable', 'heuristic_solid_basic', 'Solid cotton pants; no fine repeated pattern.'],
  ['match_bottom_beige_chino_pants', 'stable', 'observed_anchor_pass', 'Solid beige chino remained stable as main-product anchor.'],
  ['match_bottom_charcoal_tapered_slacks', 'stable', 'heuristic_solid_basic', 'Solid slacks; no fine repeated pattern.'],
  ['match_bottom_lightblue_wide_denim', 'stable', 'heuristic_simple_texture', 'Denim texture is broad/noise-like rather than fine repeated pattern.'],
  ['match_bottom_black_hline_midi_skirt', 'stable', 'heuristic_solid_basic', 'Solid skirt; no fine repeated pattern.'],
  ['match_bottom_cream_pleated_skirt', 'stable', 'observed_pass', 'Pleats passed in v0.1; repeated structure is larger-scale than failed micro patterns.'],
  ['match_bottom_navy_training_pants', 'stable', 'heuristic_solid_basic', 'Solid training pants; no fine repeated pattern.'],
  ['match_bottom_black_regular_denim', 'stable', 'heuristic_simple_texture', 'Dark denim texture is broad/noise-like rather than fine repeated pattern.'],
];

const MIME = {
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.png': 'image/png',
  '.webp': 'image/webp',
};
const EXT_BY_MIME = {
  'image/png': '.png',
  'image/jpeg': '.jpg',
  'image/webp': '.webp',
};

function loadDotEnv(file) {
  if (!fs.existsSync(file)) return {};
  const out = {};
  for (const line of fs.readFileSync(file, 'utf8').split(/\r?\n/)) {
    if (line.trim().startsWith('#')) continue;
    const match = line.match(/^\s*([A-Za-z0-9_]+)\s*=\s*(.*?)\s*$/);
    if (match) out[match[1]] = match[2];
  }
  return out;
}

const env = { ...loadDotEnv(path.join(SPIKE_DIR, '.env')), ...process.env };
const { values: opts } = parseArgs({
  options: {
    model: { type: 'string' },
    'dry-run': { type: 'boolean', default: false },
    resume: { type: 'string' },
  },
});

const config = {
  dryRun: Boolean(opts['dry-run']),
  model: opts.model || env.SPIKE_MODEL || env.MODEL_ROUTING_IMAGE_HIGH || 'gemini-3-pro-image',
  vertexProject: env.VERTEX_PROJECT || '',
  vertexLocation: env.VERTEX_LOCATION || 'global',
};

if (!config.dryRun && !env.GEMINI_API_KEY) {
  console.error('GEMINI_API_KEY is missing. Add it to spike/.env or run with --dry-run.');
  process.exit(1);
}

function itemById(id) {
  const item = seedMatchingItems.find((x) => x.id === id);
  if (!item) throw new Error(`Unknown seed item id: ${id}`);
  return item;
}

function assetPath(item) {
  return path.join(ROOT_DIR, 'public', item.imageUrl.replace(/^\//, ''));
}

function loadImage(file) {
  const mime = MIME[path.extname(file).toLowerCase()];
  if (!mime || !fs.existsSync(file)) throw new Error(`Image file missing or unsupported: ${file}`);
  const buffer = fs.readFileSync(file);
  return {
    file,
    name: path.basename(file),
    mime,
    base64: buffer.toString('base64'),
    bytes: buffer.length,
    sha256: crypto.createHash('sha256').update(buffer).digest('hex'),
  };
}

const PNG_SIG = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);

function crc32(buffer) {
  let table = crc32.table;
  if (!table) {
    table = crc32.table = new Uint32Array(256);
    for (let n = 0; n < 256; n += 1) {
      let c = n;
      for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      table[n] = c >>> 0;
    }
  }
  let c = 0xffffffff;
  for (let i = 0; i < buffer.length; i += 1) c = table[(c ^ buffer[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const typeBuffer = Buffer.from(type, 'ascii');
  const out = Buffer.alloc(12 + data.length);
  out.writeUInt32BE(data.length, 0);
  typeBuffer.copy(out, 4);
  data.copy(out, 8);
  out.writeUInt32BE(crc32(Buffer.concat([typeBuffer, data])), 8 + data.length);
  return out;
}

function decodePng(file) {
  const buffer = fs.readFileSync(file);
  if (!buffer.subarray(0, 8).equals(PNG_SIG)) throw new Error(`Not a PNG: ${file}`);
  let pos = 8;
  let width;
  let height;
  let bitDepth;
  let colorType;
  const idats = [];
  while (pos < buffer.length) {
    const len = buffer.readUInt32BE(pos); pos += 4;
    const type = buffer.toString('ascii', pos, pos + 4); pos += 4;
    const data = buffer.subarray(pos, pos + len); pos += len + 4;
    if (type === 'IHDR') {
      width = data.readUInt32BE(0);
      height = data.readUInt32BE(4);
      bitDepth = data[8];
      colorType = data[9];
    } else if (type === 'IDAT') {
      idats.push(data);
    } else if (type === 'IEND') {
      break;
    }
  }
  if (bitDepth !== 8 || ![2, 6].includes(colorType)) throw new Error(`Unsupported PNG ${file}: bitDepth=${bitDepth} colorType=${colorType}`);
  const channels = colorType === 6 ? 4 : 3;
  const stride = width * channels;
  const raw = zlib.inflateSync(Buffer.concat(idats));
  const rows = Buffer.alloc(height * stride);
  let inPos = 0;
  for (let y = 0; y < height; y += 1) {
    const filter = raw[inPos]; inPos += 1;
    const rowStart = y * stride;
    for (let x = 0; x < stride; x += 1) {
      const val = raw[inPos]; inPos += 1;
      const left = x >= channels ? rows[rowStart + x - channels] : 0;
      const up = y > 0 ? rows[rowStart + x - stride] : 0;
      const upLeft = y > 0 && x >= channels ? rows[rowStart + x - stride - channels] : 0;
      let recon;
      if (filter === 0) recon = val;
      else if (filter === 1) recon = (val + left) & 255;
      else if (filter === 2) recon = (val + up) & 255;
      else if (filter === 3) recon = (val + Math.floor((left + up) / 2)) & 255;
      else if (filter === 4) {
        const p = left + up - upLeft;
        const pa = Math.abs(p - left);
        const pb = Math.abs(p - up);
        const pc = Math.abs(p - upLeft);
        const pr = pa <= pb && pa <= pc ? left : pb <= pc ? up : upLeft;
        recon = (val + pr) & 255;
      } else {
        throw new Error(`Bad PNG filter ${filter}: ${file}`);
      }
      rows[rowStart + x] = recon;
    }
  }
  const data = Buffer.alloc(width * height * 4);
  for (let i = 0, j = 0; i < rows.length; i += channels, j += 4) {
    data[j] = rows[i];
    data[j + 1] = rows[i + 1];
    data[j + 2] = rows[i + 2];
    data[j + 3] = channels === 4 ? rows[i + 3] : 255;
  }
  return { width, height, data };
}

function encodePng({ width, height, data }) {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;
  ihdr[9] = 6;
  ihdr[10] = 0;
  ihdr[11] = 0;
  ihdr[12] = 0;
  const raw = Buffer.alloc(height * (1 + width * 4));
  let pos = 0;
  for (let y = 0; y < height; y += 1) {
    raw[pos] = 0; pos += 1;
    data.copy(raw, pos, y * width * 4, (y + 1) * width * 4);
    pos += width * 4;
  }
  return Buffer.concat([PNG_SIG, chunk('IHDR', ihdr), chunk('IDAT', zlib.deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]);
}

function cropPng(sourceFile, outFile, crop) {
  const source = decodePng(sourceFile);
  const out = { width: crop.w, height: crop.h, data: Buffer.alloc(crop.w * crop.h * 4, 255) };
  for (let y = 0; y < crop.h; y += 1) {
    for (let x = 0; x < crop.w; x += 1) {
      const sx = Math.min(source.width - 1, crop.x + x);
      const sy = Math.min(source.height - 1, crop.y + y);
      const si = (sy * source.width + sx) * 4;
      const di = (y * crop.w + x) * 4;
      out.data[di] = source.data[si];
      out.data[di + 1] = source.data[si + 1];
      out.data[di + 2] = source.data[si + 2];
      out.data[di + 3] = source.data[si + 3];
    }
  }
  fs.writeFileSync(outFile, encodePng(out));
}

function drawScaled(dest, source, dx, dy, dw, dh) {
  for (let y = 0; y < dh; y += 1) {
    const sy = Math.min(source.height - 1, Math.floor(y * source.height / dh));
    for (let x = 0; x < dw; x += 1) {
      const sx = Math.min(source.width - 1, Math.floor(x * source.width / dw));
      const si = (sy * source.width + sx) * 4;
      const di = ((dy + y) * dest.width + dx + x) * 4;
      dest.data[di] = source.data[si];
      dest.data[di + 1] = source.data[si + 1];
      dest.data[di + 2] = source.data[si + 2];
      dest.data[di + 3] = 255;
    }
  }
}

function buildPrompt(item, variant) {
  const patternBlock = variant.usesPatternInstruction
    ? `\nPattern preservation for the matching clothing:\n- ${item.patternInstruction}`
    : '';
  const detailBlock = variant.usesDetailReference
    ? '\nAdditional reference:\n- The third image is a close-up crop of the matching clothing fabric. Use it only to preserve the matching item pattern/texture scale and regularity.'
    : '';
  return `${BASE_PROMPT}${patternBlock}${detailBlock}\n\n${AVOID_PROMPT}`;
}

function buildRequestBody(prompt, images) {
  return {
    contents: [
      {
        role: 'user',
        parts: [
          { text: prompt },
          ...images.map((image) => ({ inline_data: { mime_type: image.mime, data: image.base64 } })),
        ],
      },
    ],
    generationConfig: {
      responseModalities: ['TEXT', 'IMAGE'],
      imageConfig: { imageSize: RESOLUTION },
    },
  };
}

function requestPreview(prompt, images) {
  const body = buildRequestBody(prompt, images);
  body.contents[0].parts = body.contents[0].parts.map((part) =>
    part.inline_data
      ? { inline_data: { mime_type: part.inline_data.mime_type, data: `<base64 ${part.inline_data.data.length} chars>` } }
      : part,
  );
  return body;
}

function endpointFor(model) {
  if (config.vertexProject) {
    const loc = config.vertexLocation;
    const host = loc === 'global' ? 'aiplatform.googleapis.com' : `${loc}-aiplatform.googleapis.com`;
    return `https://${host}/v1/projects/${config.vertexProject}/locations/${loc}/publishers/google/models/${model}:generateContent?key=${env.GEMINI_API_KEY}`;
  }
  return `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${env.GEMINI_API_KEY}`;
}

async function callGemini(prompt, images) {
  const body = buildRequestBody(prompt, images);
  const startedAt = performance.now();
  const res = await fetch(endpointFor(config.model), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const latencyMs = Math.round(performance.now() - startedAt);
  if (!res.ok) throw new Error(`Gemini ${res.status}: ${(await res.text()).slice(0, 800)}`);
  const json = await res.json();
  const parts = json.candidates?.[0]?.content?.parts ?? [];
  const imagePart = parts
    .filter((part) => part.inlineData?.data)
    .sort((a, b) => b.inlineData.data.length - a.inlineData.data.length)[0];
  if (!imagePart) {
    const text = parts.map((part) => part.text).filter(Boolean).join(' ').slice(0, 500);
    throw new Error(`No image in Gemini response. Text: ${text || '(empty)'}`);
  }
  return {
    buffer: Buffer.from(imagePart.inlineData.data, 'base64'),
    mime: imagePart.inlineData.mimeType || 'image/png',
    latencyMs,
    usage: json.usageMetadata ?? null,
  };
}

function isRetryableGeminiError(error) {
  return /\b(429|500|502|503|504)\b/.test(error.message);
}

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function callGeminiWithRetry(prompt, images, runId) {
  const delays = [60_000, 90_000, 120_000];
  let lastError;
  for (let attempt = 0; attempt <= delays.length; attempt += 1) {
    try {
      if (attempt > 0) process.stdout.write(`[${runId}] retry ${attempt + 1}/${delays.length + 1}... `);
      return await callGemini(prompt, images);
    } catch (error) {
      lastError = error;
      if (attempt >= delays.length || !isRetryableGeminiError(error)) break;
      const delay = delays[attempt];
      console.log(`retryable error: ${error.message.slice(0, 120)}; waiting ${Math.round(delay / 1000)}s`);
      await wait(delay);
    }
  }
  throw lastError;
}

function ensurePng(buffer, mime, outFile) {
  if (mime === 'image/png') {
    fs.writeFileSync(outFile, buffer);
    return { converted: false, sourceMime: mime };
  }
  const ext = EXT_BY_MIME[mime] || '.img';
  const tmpFile = `${outFile}${ext}`;
  fs.writeFileSync(tmpFile, buffer);
  execFileSync('sips', ['-s', 'format', 'png', tmpFile, '--out', outFile], { stdio: 'ignore' });
  fs.rmSync(tmpFile, { force: true });
  return { converted: true, sourceMime: mime };
}

function csvCell(value) {
  const s = Array.isArray(value) ? value.join(';') : value == null ? '' : String(value);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function writeRiskClassification(runDir) {
  const rows = RISK_CLASSIFICATION.map(([id, riskClass, basis, note]) => {
    const item = itemById(id);
    return { id, name: item.name, clothingType: item.clothingType, category: item.category, riskClass, basis, note };
  });
  const md = [
    '# Matching Item Risk Classification',
    '',
    'Risk classes are based on the v0.1 failure pattern: solid items are low-risk, while fine repeated pattern/texture items are risky. Final library decisions should wait for visual review and mitigation results.',
    '',
    '| id | name | type | category | riskClass | basis | note |',
    '|---|---|---|---|---|---|---|',
    ...rows.map((row) => `| ${row.id} | ${row.name} | ${row.clothingType} | ${row.category} | ${row.riskClass} | ${row.basis} | ${row.note} |`),
    '',
  ];
  fs.writeFileSync(path.join(runDir, 'risk_classification.md'), md.join('\n'));
  fs.writeFileSync(path.join(runDir, 'risk_classification.json'), JSON.stringify(rows, null, 2));
  fs.writeFileSync(
    path.join(runDir, 'risk_classification.csv'),
    [
      ['id', 'name', 'clothingType', 'category', 'riskClass', 'basis', 'note'],
      ...rows.map((row) => [row.id, row.name, row.clothingType, row.category, row.riskClass, row.basis, row.note]),
    ].map((row) => row.map(csvCell).join(',')).join('\n') + '\n',
  );
  return rows;
}

function writeFailureTables(runDir, entries) {
  const md = [
    '# Pattern Mitigation Failure Table',
    '',
    `Allowed enum: ${FAILURE_ENUM.map((x) => `\`${x}\``).join(', ')}`,
    '',
    '| runId | variant | matchingItemId | repeatNo | preliminaryFailureModes | notes | resultPath |',
    '|---|---|---|---:|---|---|---|',
    ...entries.map((entry) => `| ${entry.runId} | ${entry.variantId} | ${entry.matchingItemId} | ${entry.repeatNo} | ${entry.preliminaryFailureModes.join('; ')} | ${entry.notes || ''} | ${entry.resultPath || ''} |`),
    '',
  ];
  fs.writeFileSync(path.join(runDir, 'failure_table.md'), md.join('\n'));
  fs.writeFileSync(
    path.join(runDir, 'failure_table.csv'),
    [
      ['runId', 'variantId', 'mainProductId', 'matchingItemId', 'repeatNo', 'preliminaryFailureModes', 'notes', 'resultPath'],
      ...entries.map((entry) => [
        entry.runId,
        entry.variantId,
        entry.mainProductId,
        entry.matchingItemId,
        entry.repeatNo,
        entry.preliminaryFailureModes,
        entry.notes || '',
        entry.resultPath || '',
      ]),
    ].map((row) => row.map(csvCell).join(',')).join('\n') + '\n',
  );
}

function writeManifest(runDir, manifest) {
  fs.writeFileSync(path.join(runDir, 'manifest.json'), JSON.stringify(manifest, null, 2));
  const md = [
    '# Pattern Mitigation Pilot Manifest',
    '',
    `- Model: \`${manifest.model}\``,
    `- Resolution: \`${manifest.resolution}\``,
    `- Prompt template: \`${manifest.promptTemplateId}\``,
    `- Dry run: \`${manifest.dryRun}\``,
    `- Estimated output cost: \`$${manifest.estimatedCost.outputUsd.toFixed(2)}\``,
    `- Estimated input cost: \`$${manifest.estimatedCost.inputUsd.toFixed(2)}\``,
    '',
    '| runId | variant | mainProduct | matchingItem | repeat | result | latency |',
    '|---|---|---|---|---:|---|---:|',
    ...manifest.runs.map((run) =>
      `| ${run.runId} | ${run.variantId} | ${run.mainProductId} | ${run.matchingItemId} | ${run.repeatNo} | ${run.resultPath || run.error || 'dry-run'} | ${run.latencyMs ?? ''} |`,
    ),
    '',
  ];
  fs.writeFileSync(path.join(runDir, 'manifest.md'), md.join('\n'));
}

function makeContactSheet(runDir, runs) {
  const files = runs.filter((run) => run.resultPath).map((run) => path.join(runDir, run.resultPath));
  if (!files.length) return null;
  const cols = 6;
  const cellW = 220;
  const cellH = 275;
  const gap = 10;
  const rows = Math.ceil(files.length / cols);
  const sheet = {
    width: cols * cellW + (cols + 1) * gap,
    height: rows * cellH + (rows + 1) * gap,
    data: Buffer.alloc((cols * cellW + (cols + 1) * gap) * (rows * cellH + (rows + 1) * gap) * 4, 255),
  };
  files.forEach((file, index) => {
    const img = decodePng(file);
    const col = index % cols;
    const row = Math.floor(index / cols);
    drawScaled(sheet, img, gap + col * (cellW + gap), gap + row * (cellH + gap), cellW, cellH);
  });
  fs.writeFileSync(path.join(runDir, 'contact-sheet.png'), encodePng(sheet));
  return 'contact-sheet.png';
}

function makeContactSheetHtml(runDir, runs) {
  const html = `<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>Pattern Mitigation Contact Sheet</title>
<style>
body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 24px; color: #222; }
.grid { display: grid; grid-template-columns: repeat(3, minmax(220px, 1fr)); gap: 16px; }
figure { margin: 0; border: 1px solid #ddd; padding: 10px; }
img { width: 100%; display: block; background: #f7f7f7; }
figcaption { font-size: 12px; line-height: 1.45; margin-top: 8px; word-break: break-all; }
</style>
</head>
<body>
<h1>Pattern Mitigation Contact Sheet</h1>
<div class="grid">
${runs.map((run) => `<figure>${run.resultPath ? `<img src="${run.resultPath}" alt="${run.runId}">` : '<div>No image</div>'}<figcaption><b>${run.runId}</b><br>${run.variantId}<br>${run.matchingItemId}</figcaption></figure>`).join('\n')}
</div>
</body>
</html>`;
  fs.writeFileSync(path.join(runDir, 'contact-sheet.html'), html);
  return 'contact-sheet.html';
}

function stamp() {
  return new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
}

function validateInputs() {
  for (const test of TEST_ITEMS) {
    for (const id of [test.mainProductId, test.matchingItemId]) {
      const item = itemById(id);
      if (!fs.existsSync(assetPath(item))) throw new Error(`Missing asset for ${id}: ${assetPath(item)}`);
    }
  }
}

validateInputs();

const runDir = opts.resume ? path.resolve(ROOT_DIR, opts.resume) : path.join(SPIKE_DIR, 'runs', `${stamp()}-pattern-mitigation`);
for (const dir of ['results', 'prompts', 'requests', 'detail_refs']) fs.mkdirSync(path.join(runDir, dir), { recursive: true });
fs.writeFileSync(path.join(runDir, 'prompt-base-v0.2.txt'), `${BASE_PROMPT}\n\n${AVOID_PROMPT}`);
fs.writeFileSync(path.join(runDir, 'prompt-template-notes.md'), [
  '# Pattern Mitigation Prompt Notes',
  '',
  '- `base`: v0.1 prompt plus stronger full-body framing sentence.',
  '- `pattern_prompt`: base plus item-specific pattern preservation instruction.',
  '- `pattern_prompt_detail`: pattern prompt plus a third fabric detail crop reference.',
  '',
].join('\n'));

const runs = [];
const failureEntries = [];
const detailRefs = {};

console.log(`Mode: ${config.dryRun ? 'dry-run' : 'real call'}`);
console.log(`Model: ${config.model}`);
console.log(`Resolution: ${RESOLUTION}`);
console.log(`Run dir: ${runDir}`);

for (const test of TEST_ITEMS) {
  const matching = itemById(test.matchingItemId);
  const detailPath = path.join(runDir, 'detail_refs', `${test.matchingItemId}_fabric_detail.png`);
  if (!fs.existsSync(detailPath)) cropPng(assetPath(matching), detailPath, test.detailCrop);
  detailRefs[test.matchingItemId] = path.relative(runDir, detailPath);
}

for (const test of TEST_ITEMS) {
  const main = itemById(test.mainProductId);
  const matching = itemById(test.matchingItemId);
  const mainImage = loadImage(assetPath(main));
  const matchingImage = loadImage(assetPath(matching));
  const detailImage = loadImage(path.join(runDir, detailRefs[test.matchingItemId]));

  for (const variant of VARIANTS) {
    const prompt = buildPrompt(test, variant);
    for (let repeatNo = 1; repeatNo <= REPEATS; repeatNo += 1) {
      const runId = `${test.matchingItemId}_${variant.id}_${String(repeatNo).padStart(2, '0')}`;
      const promptPath = `prompts/${runId}.txt`;
      const requestPath = `requests/${runId}.json`;
      const resultPath = `results/${runId}.png`;
      const resultFile = path.join(runDir, resultPath);
      const images = variant.usesDetailReference ? [mainImage, matchingImage, detailImage] : [mainImage, matchingImage];

      fs.writeFileSync(path.join(runDir, promptPath), prompt);
      fs.writeFileSync(path.join(runDir, requestPath), JSON.stringify(requestPreview(prompt, images), null, 2));

      const baseRun = {
        runId,
        promptTemplateId: PROMPT_TEMPLATE_ID,
        variantId: variant.id,
        variantLabel: variant.label,
        usesPatternInstruction: variant.usesPatternInstruction,
        usesDetailReference: variant.usesDetailReference,
        mainProductId: test.mainProductId,
        mainProductName: main.name,
        matchingItemId: test.matchingItemId,
        matchingItemName: matching.name,
        repeatNo,
        riskReason: test.riskReason,
        patternInstruction: variant.usesPatternInstruction ? test.patternInstruction : null,
        promptPath,
        requestPath,
        detailReferencePath: variant.usesDetailReference ? detailRefs[test.matchingItemId] : null,
        resultPath: null,
        latencyMs: null,
        usage: null,
        error: null,
        inputImages: [
          {
            role: 'main_product',
            itemId: main.id,
            assetPath: main.imageUrl,
            filePath: path.relative(ROOT_DIR, mainImage.file),
            mime: mainImage.mime,
            bytes: mainImage.bytes,
            sha256: mainImage.sha256,
          },
          {
            role: 'matching_clothing',
            itemId: matching.id,
            assetPath: matching.imageUrl,
            filePath: path.relative(ROOT_DIR, matchingImage.file),
            mime: matchingImage.mime,
            bytes: matchingImage.bytes,
            sha256: matchingImage.sha256,
          },
          ...(variant.usesDetailReference ? [{
            role: 'matching_fabric_detail',
            itemId: matching.id,
            filePath: detailRefs[test.matchingItemId],
            mime: detailImage.mime,
            bytes: detailImage.bytes,
            sha256: detailImage.sha256,
          }] : []),
        ],
      };

      if (!config.dryRun && fs.existsSync(resultFile)) {
        const buffer = fs.readFileSync(resultFile);
        console.log(`[${runId}] existing result -> ${resultPath}`);
        runs.push({
          ...baseRun,
          resultPath,
          resumedExistingOutput: true,
          output: {
            mime: 'image/png',
            sourceMime: 'image/png',
            convertedToPng: false,
            bytes: buffer.length,
            sha256: crypto.createHash('sha256').update(buffer).digest('hex'),
          },
        });
        failureEntries.push({ ...baseRun, resultPath, preliminaryFailureModes: [], notes: 'pending first-pass visual review; existing output from resumed run' });
        continue;
      }

      if (config.dryRun) {
        console.log(`[${runId}] dry-run`);
        runs.push(baseRun);
        failureEntries.push({ ...baseRun, preliminaryFailureModes: [], notes: 'dry-run; no visual result' });
        continue;
      }

      try {
        process.stdout.write(`[${runId}] calling... `);
        const response = await callGeminiWithRetry(prompt, images, runId);
        const pngInfo = ensurePng(response.buffer, response.mime, resultFile);
        console.log(`done ${(response.latencyMs / 1000).toFixed(1)}s -> ${resultPath}`);
        const buffer = fs.readFileSync(resultFile);
        runs.push({
          ...baseRun,
          resultPath,
          latencyMs: response.latencyMs,
          usage: response.usage,
          output: {
            mime: 'image/png',
            sourceMime: pngInfo.sourceMime,
            convertedToPng: pngInfo.converted,
            bytes: buffer.length,
            sha256: crypto.createHash('sha256').update(buffer).digest('hex'),
          },
        });
        failureEntries.push({ ...baseRun, resultPath, preliminaryFailureModes: [], notes: 'pending first-pass visual review' });
      } catch (error) {
        console.error(`failed: ${error.message}`);
        runs.push({ ...baseRun, error: error.message });
        failureEntries.push({ ...baseRun, preliminaryFailureModes: ['prompt_ignored'], notes: `generation failed: ${error.message}` });
      }
    }
  }
}

const riskClassification = writeRiskClassification(runDir);
const totalInputImages = runs.reduce((count, run) => count + run.inputImages.length, 0);
const manifest = {
  createdAt: new Date().toISOString(),
  model: config.model,
  dryRun: config.dryRun,
  resolution: RESOLUTION,
  promptTemplateId: PROMPT_TEMPLATE_ID,
  repeats: REPEATS,
  variants: VARIANTS,
  failureEnum: FAILURE_ENUM,
  estimatedCost: {
    outputUsd: config.dryRun ? 0 : runs.length * USD_PER_OUTPUT_IMAGE,
    inputUsd: config.dryRun ? 0 : totalInputImages * USD_PER_INPUT_IMAGE,
    totalUsd: config.dryRun ? 0 : (runs.length * USD_PER_OUTPUT_IMAGE) + (totalInputImages * USD_PER_INPUT_IMAGE),
  },
  detailRefs,
  riskClassificationSummary: riskClassification.reduce((acc, row) => {
    acc[row.riskClass] = (acc[row.riskClass] || 0) + 1;
    return acc;
  }, {}),
  runs,
};

const contactSheetHtml = makeContactSheetHtml(runDir, runs);
let contactSheetPng = null;
try {
  contactSheetPng = makeContactSheet(runDir, runs);
} catch (error) {
  console.warn(`contact-sheet.png was not generated: ${error.message}`);
}
manifest.contactSheetHtml = contactSheetHtml;
manifest.contactSheetPng = contactSheetPng;

writeManifest(runDir, manifest);
writeFailureTables(runDir, failureEntries);

console.log('');
console.log(`Manifest: ${path.join(runDir, 'manifest.json')}`);
console.log(`Failure table: ${path.join(runDir, 'failure_table.md')}`);
console.log(`Risk classification: ${path.join(runDir, 'risk_classification.md')}`);
console.log(`Contact sheet: ${path.join(runDir, contactSheetPng || contactSheetHtml)}`);
