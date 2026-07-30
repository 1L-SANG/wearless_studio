/* 정보 블록 빌더 육안 QA 하니스 — 빌더 출력을 CanvasElement 근사 HTML 로 렌더.
   사용: node scripts/qa_info_blocks_harness.mjs > qa-info-blocks.html
   (임시 검증용 — 커밋 대상 아님. 에디터 실렌더의 근사치이며 정확한 폰트/줄바꿈은 다를 수 있다) */
import { INFO_PRESET_TYPES, applyInfoTemplate, buildInfoBlock, defaultInfoFor } from '../src/features/editor/presets/infoPresets.js';

const CTX = {
  productName: '와일드 팝컬러 롤업 반팔 T',
  clothingType: 'top',
  measurementSchema: { top: ['totalLength', 'shoulderWidth', 'chestWidth', 'sleeveLength'] },
  measurementLabels: { totalLength: '총장', shoulderWidth: '어깨너비', chestWidth: '가슴단면', sleeveLength: '소매길이' },
  measurements: [{ key: 'totalLength', value: 67 }, { key: 'shoulderWidth', value: 45 }, { key: 'chestWidth', value: 55 }, { key: 'sleeveLength', value: 23 }],
  materials: [{ name: '면', ratio: 93 }, { name: '스판', ratio: 7 }],
  sellingPoints: ['롤업 배색 소매', '도트 레터링 나염', '쫀쫀한 텍스처'],
  fit: 'regular',
  fits: [
    { value: 'slim', label: '슬림핏' }, { value: 'regular', label: '정핏' },
    { value: 'semi_over', label: '세미오버' }, { value: 'over', label: '오버핏' },
  ],
  colorLabels: ['레몬', '민트', '퍼플', '블랙', '화이트'],
};

const FONT = { 'Cal Sans': 'ui-rounded, system-ui', 'Roboto Mono': 'ui-monospace, monospace', 'Pretendard': 'system-ui', 'Cormorant': 'Georgia, serif' };
const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/\n/g, '<br/>');

function renderEl(el) {
  const base = `position:absolute;left:${el.x}px;top:${el.y}px;width:${el.w}px;`;
  if (el.type === 'text') {
    const s = el.style || {};
    const lines = (el.text || '').split('\n');
    const txt = (!s.list || s.list === 'none') ? esc(el.text) : lines.map((ln) => (s.list === 'ordered' ? '1. ' : '• ') + esc(ln)).join('<br/>');
    return `<div style="${base}font-family:${FONT[s.font] || 'system-ui'};font-size:${s.size}px;font-weight:${s.weight || 400};color:${s.color || '#0e0d14'};letter-spacing:${s.tracking || 0}px;text-align:${s.align || 'left'};line-height:${s.lineHeight ? s.lineHeight + 'px' : 1.4};font-style:${s.italic ? 'italic' : 'normal'}">${txt}</div>`;
  }
  if (el.type === 'shape') {
    const r = el.shape === 'circle' ? '50%' : (el.radius || 0) + 'px';
    return `<div style="${base}height:${el.h}px;background:${el.fill};border-radius:${r}"></div>`;
  }
  if (el.type === 'line') {
    return `<div style="${base}height:0;border-top:${el.strokeWidth || 2.5}px solid ${el.stroke || '#0e0d14'};margin-top:${(el.h || 8) / 2}px"></div>`;
  }
  if (el.type === 'image') {
    return `<div style="${base}height:${el.h}px;border:1.5px dashed #c9c9c5;border-radius:${el.radius || 8}px;display:flex;align-items:center;justify-content:center;color:#898989;font-size:14px;font-family:system-ui">${el.src ? '' : '+ 이미지 추가'}</div>`;
  }
  return '';
}

function renderBlock(b, label) {
  const h = Math.max(b.h || 220, b.elements.reduce((m, e) => Math.max(m, (e.y || 0) + (e.h || 40)), 0) + 50);
  return `<div style="margin:0 auto 6px;width:1000px;font-family:system-ui;font-size:12px;color:#898989">${esc(label)}</div>
<div style="position:relative;width:1000px;height:${h}px;background:${b.bg};margin:0 auto 28px;overflow:hidden;box-shadow:0 0 0 1px #eee">${b.elements.map(renderEl).join('')}</div>`;
}

let out = '<!doctype html><meta charset="utf-8"><body style="background:#e8e8e6;padding:40px 0">';
out += '<h2 style="text-align:center;font-family:system-ui">개별 프리셋 9종 (기본값)</h2>';
for (const p of INFO_PRESET_TYPES) {
  out += renderBlock(buildInfoBlock(p.type, defaultInfoFor(p.type, CTX), CTX), `${p.label} (${p.type})`);
}
{
  const doc = [
    { id: 'cut', name: '컷', kind: 'benefit', contentRole: 'hero', bg: '#ffffff', h: 300, elements: [{ id: 'cutimg', type: 'image', x: 60, y: 50, w: 880, h: 200, src: null }] },
    { id: 's', name: '사이즈 안내', kind: 'size', auto: true, bg: '#ffffff', h: 200, elements: [] },
    { id: 'c', name: '세탁 안내', kind: 'care', auto: true, bg: '#f5f5f5', h: 160, elements: [] },
    { id: 'n', name: 'AI 생성 안내', kind: 'ai-notice', auto: true, bg: '#ffffff', h: 120, elements: [{ id: 'nt', type: 'text', x: 60, y: 48, w: 880, h: 40, text: '본 상세페이지의 일부 이미지는 AI를 활용해 생성되었습니다.', style: { size: 13, color: '#4a4a45', align: 'center' } }] },
  ];
  const res = applyInfoTemplate(doc, CTX);
  out += `<h2 style="text-align:center;font-family:system-ui">기본 템플릿 적용 결과 (${res.inserted.length} 구성)</h2>`;
  for (const b of res.blocks) out += renderBlock(b, `${b.name} — kind:${b.kind}${b.infoType ? ' · ' + b.infoType : ''}`);
}
out += '</body>';
process.stdout.write(out);
