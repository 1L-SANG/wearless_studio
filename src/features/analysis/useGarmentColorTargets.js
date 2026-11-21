import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api/index.js';
import { alphaBoundsFromPixels } from '@/lib/outfitImageBounds.js';
import { ZERO_SLIDERS, createTonePreview, isZeroSliders, normalizeSliders, opaqueMedianSrgb8, sameSliders } from '@/lib/garmentColorTone.js';

const PREVIEW_MAX_SIDE = 560;
const SAVE_DELAY_MS = 400;
let statusRequest = null;

async function decodePreview(src) {
  const blob = await (await fetch(src)).blob();
  const bitmap = await createImageBitmap(blob);
  const scale = Math.min(1, PREVIEW_MAX_SIDE / Math.max(bitmap.width, bitmap.height));
  const width = Math.max(1, Math.round(bitmap.width * scale));
  const height = Math.max(1, Math.round(bitmap.height * scale));
  const canvas = document.createElement('canvas');
  canvas.width = width; canvas.height = height;
  const context = canvas.getContext('2d', { willReadFrequently: true });
  context.drawImage(bitmap, 0, 0, width, height);
  bitmap.close?.();
  const full = context.getImageData(0, 0, width, height);
  // 보드가 옷 그림 칸을 잡는 규칙(alphaBoundsFromPixels)과 같게 여백을 잘라 칸에 꽉 차게 한다.
  const bounds = alphaBoundsFromPixels(full.data, width, height);
  const imageData = bounds ? context.getImageData(Math.round(bounds.x * width), Math.round(bounds.y * height),
    Math.max(1, Math.round(bounds.w * width)), Math.max(1, Math.round(bounds.h * height))) : full;
  return { preview: createTonePreview(imageData), median: opaqueMedianSrgb8(imageData) };
}

const frontImage = (color) => color?.images?.find((image) => String(image.slot || 'Front').toLowerCase() === 'front') || null;
const sourceKey = (color) => `${color.id}:${frontImage(color)?.id || frontImage(color)?.src || ''}`;

/* 내 옷 색 맞추기 상태(documents/garment_color_target_spec.md §3, §5, §6).
   셀러 입력은 분석 키 garmentColorSliders에 저장한다(손님 초안도 같은 길로 저장되고 확정 때 함께 옮겨진다).
   미리보기 누끼는 판을 열 때 색상마다 한 번 받아 이 화면 동안 기억한다. */
export function useGarmentColorTargets({ projectId, product, analysis, onChange }) {
  const [enabled, setEnabled] = useState(false);
  const [sources, setSources] = useState({});
  const [overrides, setOverrides] = useState({});
  const requested = useRef(new Set());
  const pendingSave = useRef(null);
  const latest = useRef({});
  latest.current = { analysis, onChange, overrides };

  useEffect(() => {
    let alive = true;
    statusRequest ||= Promise.resolve().then(() => api.getGarmentColorStatus()).catch(() => ({ enabled: false }));
    statusRequest.then((status) => { if (alive) setEnabled(!!status?.enabled); });
    return () => { alive = false; };
  }, []);

  const productColors = useMemo(() => {
    const list = product?.colors || [];
    return [...list.filter((color) => color.isBase), ...list.filter((color) => !color.isBase)].filter((color) => color?.id);
  }, [product?.colors]);

  const savedSliders = analysis?.garmentColorSliders?.colors || {};
  const colors = productColors.map((color, index) => {
    const source = sources[sourceKey(color)];
    return {
      colorId: color.id, name: color.name || `색상 ${index + 1}`, isBase: !!color.isBase,
      photo: frontImage(color)?.src || null,
      status: !frontImage(color) ? 'unavailable' : source?.status || 'unprepared',
      auto: source?.auto || null, sourceSrgb: source?.sourceSrgb || source?.median || null,
      sliders: normalizeSliders(overrides[color.id] ?? savedSliders[color.id] ?? ZERO_SLIDERS),
    };
  });
  const byKey = Object.fromEntries(productColors.map((color) => [color.id, sources[sourceKey(color)]]));

  const loadSource = useCallback(async (color) => {
    const key = sourceKey(color);
    if (requested.current.has(key)) return;
    requested.current.add(key);
    setSources((prior) => ({ ...prior, [key]: { status: 'loading' } }));
    try {
      let image;
      if (!projectId) image = await (await fetch(frontImage(color).src)).blob();
      const result = await api.getGarmentColorSource(projectId, {
        colorId: color.id, image, clothingType: latest.current.analysis?.clothingType || product?.clothingType, subCategory: latest.current.analysis?.subCategory,
      });
      if (result?.status !== 'ready' || !result.cutoutPng) {
        setSources((prior) => ({ ...prior, [key]: { status: 'unavailable', reason: result?.reason || 'mask_failed' } }));
        return;
      }
      const decoded = await decodePreview(result.cutoutPng);
      setSources((prior) => ({ ...prior, [key]: { status: 'ready', ...decoded, auto: result.auto || null, sourceSrgb: result.sourceSrgb || null } }));
    } catch {
      // 연결 끊김이나 요청 한도(429)는 옷을 못 찾은 것과 다르다. 다시 시도할 수 있게 둔다.
      requested.current.delete(key);
      setSources((prior) => ({ ...prior, [key]: { status: 'error' } }));
    }
  }, [projectId, product?.clothingType]);

  // 이미 맞춰 둔 기준 색상은 보드와 줄에 맞춘 색을 보여 주려고 누끼를 미리 받는다.
  const base = productColors[0];
  const baseTouched = base && !isZeroSliders(savedSliders[base.id]);
  useEffect(() => { if (enabled && base && baseTouched && frontImage(base)) void loadSource(base); }, [enabled, base, baseTouched, loadSource]);

  // 보고 있는 색상의 누끼만 받는다(색상마다 외부 호출 한 번이라 열지 않은 색상은 부르지 않는다).
  const prepare = useCallback((colorId) => {
    const color = productColors.find((item) => item.id === colorId) || productColors[0];
    if (color && frontImage(color)) void loadSource(color);
  }, [productColors, loadSource]);

  const save = useCallback(() => {
    clearTimeout(pendingSave.current);
    pendingSave.current = null;
    const { analysis: current, onChange: change, overrides: next } = latest.current;
    if (!Object.keys(next).length) return;
    const merged = { ...(current?.garmentColorSliders?.colors || {}), ...next };
    const kept = Object.fromEntries(Object.entries(merged).map(([id, value]) => [id, normalizeSliders(value)]).filter(([, value]) => !isZeroSliders(value)));
    const before = current?.garmentColorSliders?.colors || {};
    const unchanged = Object.keys(kept).length === Object.keys(before).length
      && Object.entries(kept).every(([id, value]) => sameSliders(value, before[id]));
    if (!unchanged) change({ garmentColorSliders: { version: 1, colors: kept } });
    setOverrides({});
  }, []);

  const setSliders = useCallback((colorId, next, { immediate = false } = {}) => {
    setOverrides((prior) => ({ ...prior, [colorId]: normalizeSliders(next) }));
    clearTimeout(pendingSave.current);
    pendingSave.current = setTimeout(save, immediate ? 0 : SAVE_DELAY_MS);
  }, [save]);

  useEffect(() => () => { if (pendingSave.current) save(); }, [save]);

  return { enabled: enabled && colors.length > 0, colors, sources: byKey, setSliders, prepare, flush: save };
}
