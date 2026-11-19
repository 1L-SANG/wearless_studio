import { pageTemplatesCatalog } from '../../lib/pageTemplates/catalog.js';

/** 시스템 요소를 뺀 콘텐츠의 아래 끝. 새 텍스트를 "기존 콘텐츠 아래"에 붙일 때도 쓴다. */
export function getBlockContentBottom(block) {
  return block.elements.reduce(
    (bottom, element) => element.system || element.hidden
      ? bottom
      : Math.max(bottom, (element.y || 0) + (element.h || 40)),
    0,
  );
}

export function isEmptyTextElement(element) {
  return element?.type === 'text' && String(element.text ?? '').trim() === '';
}

export function getHiddenExportElementIds(block) {
  const groups = new Map();
  for (const element of block.elements) {
    if (!element.groupId?.startsWith('template-copy:')) continue;
    const group = groups.get(element.groupId) || [];
    group.push(element);
    groups.set(element.groupId, group);
  }
  const hidden = new Set(block.elements.filter(element => element.hidden || isEmptyTextElement(element)).map(element => element.id ?? element));
  for (const members of groups.values()) {
    // Literal Q./A., stars and similar template ornaments are not copy. If the
    // seller rewrites one, its changed text counts as content and keeps the group.
    const hasContent = members.some(element => !element.hidden && (element.type === 'image'
      || element.type === 'text' && !isEmptyTextElement(element)
        && element.text !== element.templateCopyDecoration));
    if (!hasContent) members.forEach(element => hidden.add(element.id ?? element));
  }
  return hidden;
}

// Export is a projection: the editor keeps empty input slots and its saved
// geometry. Only linked intro panels and later linked paragraphs are packed.
function compactIntroExport(block, visible) {
  if (block.pageDesignId !== 'T01-D07') return visible;
  const panels = block.elements.filter(el => el.type === 'shape' && el.shape === 'rect'
    && el.groupId?.startsWith('template-copy:') && block.elements.some(text => text.groupId === el.groupId
      && text.copyRole === 'body' && text.paragraphIndex != null)).sort((a, b) => a.y - b.y);
  const byGroup = new Map(panels.map(panel => [panel.groupId, panel]));
  let output = visible;
  for (const [index, original] of panels.entries()) {
    const panel = output.find(el => el.id === original.id);
    const texts = block.elements.filter(el => el.type === 'text' && el.groupId === original.groupId && !el.hidden).sort((a, b) => a.y - b.y);
    if (!texts.some(isEmptyTextElement)) continue;
    const kept = output.filter(el => el.type === 'text' && el.groupId === original.groupId).sort((a, b) => a.y - b.y);
    let removed = original.h;
    const updates = new Map();
    if (panel && kept.length) {
      const topPad = Math.max(0, texts[0].y - original.y);
      const bottomPad = Math.max(0, original.y + original.h - Math.max(...texts.map(el => el.y + el.h)));
      let cursor = panel.y + topPad;
      kept.forEach((el, i) => {
        if (i) cursor += Math.max(0, el.y - kept[i - 1].y - kept[i - 1].h);
        updates.set(el.id, { ...el, y: cursor }); cursor += el.h;
      });
      const h = Math.min(panel.h, cursor - panel.y + bottomPad);
      removed = panel.h - h; updates.set(panel.id, { ...panel, h });
    } else if (panels[index + 1]) removed += Math.max(0, panels[index + 1].y - original.y - original.h);
    output = output.map(el => updates.get(el.id) || (removed && byGroup.get(el.groupId)?.y >= original.y + original.h
      ? { ...el, y: el.y - removed } : el));
  }
  return output;
}

export function getBlockExportElements(block) {
  const hidden = getHiddenExportElementIds(block);
  return compactIntroExport(block, block.elements.filter(element => !hidden.has(element.id ?? element)));
}

export function getBlockExportLayout(block) {
  return Object.fromEntries(getBlockExportElements(block).flatMap(el => {
    const original = block.elements.find(item => item.id === el.id);
    const changed = Object.fromEntries(['x', 'y', 'w', 'h'].filter(key => el[key] !== original?.[key]).map(key => [key, el[key]]));
    return Object.keys(changed).length ? [[el.id, changed]] : [];
  }));
}

function exportContentBottom(block) {
  return getBlockExportElements(block).reduce(
    (bottom, element) => element.system || element.hidden
      ? bottom
      : Math.max(bottom, (element.y || 0) + (element.h || 40)),
    0,
  );
}

export function introSectionPadding() {
  const catalog = pageTemplatesCatalog;
  const wrapper = catalog.designs['T01-D07'].layout.find(node => node.columns);
  const bubble = wrapper.columns.flatMap(col => col.nodes).find(node => node.splitParagraphs === 'body');
  return catalog.templates.find(template => template.id === 'emo').spacing.textSectionGap + wrapper.gap + bubble.gap;
}

export function getBlockRenderHeight(block, { forExport = false } = {}) {
  const contentBottom = getBlockContentBottom(block);
  const blockHeight = block.h || 220;
  // 이미 부모 안에 들어온 요소에는 여백을 다시 더하지 않는다. 요소가 실제로 넘친
  // 경우에만 한 번 50px 안전 여백과 함께 확장해야, 하단에 딱 맞춘 뒤 높이가 반복 증가하지 않는다.
  const padding = block.pageDesignId === 'T01-D07' ? introSectionPadding() : 50;
  const editorHeight = block.pageDesignId === 'T01-D07' ? Math.max(blockHeight, contentBottom + padding)
    : contentBottom > blockHeight ? contentBottom + padding : blockHeight;
  if (!forExport) return editorHeight;
  // 편집 높이에서 빈 텍스트가 만든 맨 아래 구간만 뺀다. 이렇게 해야 생성 캡션 자리는
  // 결과물에서 사라지면서도 셀러가 수동으로 늘린 블록 여백은 그대로 보존된다.
  return editorHeight - Math.max(0, contentBottom - exportContentBottom(block));
}

export function expandBlockHeights(blocks) {
  return blocks.map((block) => block.pageDesignId === 'T01-D07' && !block.genTemplateBaseline ? block
    : { ...block, h: getBlockRenderHeight(block) });
}

export function blockHeightFromBottom(startHeight, screenDeltaY, scale = 1) {
  const zoom = Number(scale) > 0 ? Number(scale) : 1;
  return Math.max(120, Math.round(Number(startHeight) + Number(screenDeltaY) / zoom));
}

export function clampDragDelta(snapshot, [dx, dy], blockHeight) {
  const elements = Object.values(snapshot || {});
  if (!elements.length) return [dx, dy];

  const left = Math.min(...elements.map((element) => element.x));
  const right = Math.max(...elements.map((element) => element.x + (element.w || 0)));
  const minDx = -left;
  const maxDx = Math.max(minDx, 1000 - right);
  const top = Math.min(...elements.map((element) => element.y));
  const bottom = Math.max(...elements.map((element) => element.y + (element.h || 0)));
  const minDy = -top;
  const maxDy = Number.isFinite(blockHeight)
    ? Math.max(minDy, blockHeight - bottom)
    : Number.POSITIVE_INFINITY;

  return [
    Math.max(minDx, Math.min(maxDx, dx)),
    Math.max(minDy, Math.min(maxDy, dy)),
  ];
}

export function clampElementRect(x, y, width, height) {
  const right = Math.min(1000, Math.max(24, x + Math.max(24, width)));
  const bottom = Math.max(24, y + Math.max(24, height));
  const left = Math.max(0, Math.min(x, right - 24));
  const top = Math.max(0, Math.min(y, bottom - 24));

  return { x: left, y: top, w: right - left, h: bottom - top };
}

/* 텍스트 요소의 클릭 판정 — 상자가 아니라 실제로 글자가 그려진 줄 상자로 본다.

   텍스트 요소는 배치 폭(보통 880)을 통째로 차지한다. 그래서 한 줄짜리 설명 오른쪽의 넓은
   흰 공간도 요소 안이고, 셀러가 "빈 곳"이라 여기고 눌러도 블록이 아니라 그 텍스트가 잡혔다.
   줄 상자(Range.getClientRects)에서 화면상 3px 이상 벗어나면 그 클릭은 블록 몫이다.
   캔버스가 축소되면 글자 높이도 6~8px까지 작아지므로 2px 오차만 허용하면 같은 문장을
   눌러도 포인터 위치에 따라 선택이 빠진다. 3px는 표 안의 인접 구분선 중심을 덮지 않는
   최대 안전 여유이며, viewport 좌표라 줌과 무관하게 유지된다.

   rects 가 비면(빈 텍스트) false — 잡을 단서가 상자밖에 없으니 종전대로 요소가 받는다. */
export function pointMissesTextLines(rects, x, y, pad = 3) {
  if (!rects || !rects.length) return false;
  return !rects.some((r) => x >= r.left - pad && x <= r.right + pad
    && y >= r.top - pad && y <= r.bottom + pad);
}
