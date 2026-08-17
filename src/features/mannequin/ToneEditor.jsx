/* 마네킹컷 톤 에디터 — 색감·밝기 두 개뿐.

   시스템이 하는 일은 **의류 픽셀을 고르는 것 하나**다. 얼마나 진하게, 얼마나 밝게는 셀러가
   정한다. 그래서 어떤 조건에서도 슬라이더를 대신 꺼주지 않는다 — 마스크가 준비 중이면
   준비 중이라고 말할 뿐이다.

   미리보기는 별도 썸네일이 아니라 **메인 컷 이미지 위**에 그린다(QA 피드백 2026-08-12:
   같은 사진이 두 번 보이는 건 혼란이다). 캔버스를 포털로 `.fit-mine-img` 에 얹고, 조정이
   중립(0/0)일 때는 캔버스를 치워 원본 <img> 가 그대로 보이게 한다.

   드래그 중에는 네트워크가 0이다. 원본과 마스크를 열 때 한 번 디코드해 두고, 그 뒤로는
   requestAnimationFrame 마다 화면 크기 버퍼만 다시 칠한다. `적용`을 눌러야 비로소 원본
   해상도로 한 번 렌더하고 업로드한다 — 프리뷰와 최종이 **같은 함수**라 결과가 갈리지 않는다. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import { api } from '../../lib/api/index.js';
import {
  EXPOSURE_RANGE,
  SATURATION_RANGE,
  applyTone,
  clampExposure,
  clampSaturation,
  isNeutral,
  maskAlphaFrom,
} from '../../lib/toneRender.js';

//: 드래그 중 다시 칠하는 버퍼의 긴 변. 화면에 보이는 크기면 충분하고, 원본(2K 이상)을
//  매 프레임 돌리면 슬라이더가 끊긴다. 최종 렌더만 원본 해상도로 간다.
const PREVIEW_MAX_EDGE = 900;
//: 마스크 전처리 폴링. 생성 직후 몇 초면 끝나므로 공격적으로 두드릴 이유가 없다.
const POLL_MS = 4000;
const POLL_LIMIT = 15;
//: 중앙 스냅 폭. 0 근처는 0 으로 — "아무것도 안 한 상태"가 슬라이더에서 명확해야 한다.
const SNAP = 1;

async function decodeBlob(blob) {
  if (typeof createImageBitmap === 'function') return createImageBitmap(blob);
  const url = URL.createObjectURL(blob);
  try {
    const img = new Image();
    img.src = url;
    await img.decode();
    return img;
  } finally {
    URL.revokeObjectURL(url);
  }
}

function drawTo(source, width, height) {
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(source, 0, 0, width, height);
  return ctx.getImageData(0, 0, width, height);
}

const snapCenter = (clamp) => (v) => {
  const n = clamp(v);
  return Math.abs(n) <= SNAP ? 0 : n;
};
const snapSat = snapCenter(clampSaturation);
const snapExp = snapCenter(clampExposure);

function ToneSlider({ label, value, range, disabled, onChange, listId }) {
  return (
    <label className="tone-editor-row">
      <span className="tone-editor-name">{label}</span>
      <input
        type="range" min={-range} max={range} step={1} list={listId}
        value={value} disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        onDoubleClick={() => onChange(0)}
        aria-label={label} aria-valuetext={`${value}`}
      />
      <datalist id={listId}><option value="0" /></datalist>
      <output className={`tone-editor-value${value === 0 ? ' zero' : ''}`}>
        {value > 0 ? `+${value}` : value}
      </output>
    </label>
  );
}

export function ToneEditor({ projectId, cutId, enabled = true, overlayRef, onApplied }) {
  const [state, setState] = useState(null);          // 서버가 말하는 준비 상태
  const [saturation, setSaturation] = useState(0);
  const [exposure, setExposure] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [applied, setApplied] = useState(false);     // 방금 적용 성공 — 슬라이더를 만지면 꺼진다

  const canvasRef = useRef(null);
  const buffers = useRef(null);                       // {src, mask, out, w, h}
  const full = useRef(null);                          // {srcImg, maskImg, w, h} — 적용 시에만
  const frame = useRef(0);

  const ready = state?.status === 'ready' && !!buffers.current;
  const neutral = isNeutral(saturation, exposure);

  // ── 상태 조회 (준비될 때까지만 폴링) ──────────────────────────────────────
  useEffect(() => {
    if (!enabled || !projectId || !cutId) return undefined;
    let alive = true;
    let tries = 0;
    let timer;
    const tick = async () => {
      try {
        const next = await api.getToneEditor(projectId, cutId);
        if (!alive) return;
        setState(next);
        setApplied(false);
        // 저장된 조정값 복원. 처음이면 0/0 — 슬라이더는 항상 중앙에서 시작한다.
        setSaturation(snapSat(next?.adjustment?.saturation ?? 0));
        setExposure(snapExp(next?.adjustment?.exposure ?? 0));
        if (next?.status === 'processing' && (tries += 1) < POLL_LIMIT) {
          timer = setTimeout(tick, POLL_MS);
        }
      } catch {
        if (alive) setState({ status: 'failed' });     // 조회 실패 = 이 컷은 조정 불가
      }
    };
    tick();
    return () => { alive = false; clearTimeout(timer); };
  }, [enabled, projectId, cutId]);

  // ── 원본·마스크를 한 번만 디코드 ────────────────────────────────────────
  useEffect(() => {
    if (state?.status !== 'ready') return undefined;
    let alive = true;
    (async () => {
      try {
        const [srcBlob, maskBlob] = await Promise.all([
          api.toneEditorSource(projectId, cutId),
          api.toneEditorMask(projectId, cutId),
        ]);
        const [srcImg, maskImg] = await Promise.all([decodeBlob(srcBlob), decodeBlob(maskBlob)]);
        if (!alive) return;
        const w0 = srcImg.width, h0 = srcImg.height;
        const scale = Math.min(1, PREVIEW_MAX_EDGE / Math.max(w0, h0));
        const w = Math.max(1, Math.round(w0 * scale));
        const h = Math.max(1, Math.round(h0 * scale));
        const src = drawTo(srcImg, w, h);
        const mask = maskAlphaFrom(drawTo(maskImg, w, h));
        buffers.current = { src: src.data, mask, out: new Uint8ClampedArray(src.data.length), w, h };
        full.current = { srcImg, maskImg, w: w0, h: h0 };
        setState((s) => ({ ...s }));                  // 버퍼 준비 → 리렌더
      } catch {
        if (alive) setError('이미지를 불러오지 못했어요.');
      }
    })();
    return () => { alive = false; };
  }, [state?.status, projectId, cutId]);

  // ── 프리뷰: 메인 이미지 위 오버레이 캔버스를 프레임마다 다시 칠한다 ──────
  const paint = useCallback(() => {
    const b = buffers.current;
    const canvas = canvasRef.current;
    if (!b || !canvas) return;
    applyTone(b.src, b.mask, b.out, saturation, exposure);
    canvas.width = b.w;
    canvas.height = b.h;
    canvas.getContext('2d').putImageData(new ImageData(b.out, b.w, b.h), 0, 0);
  }, [saturation, exposure]);

  useEffect(() => {
    cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(paint);
    return () => cancelAnimationFrame(frame.current);
  }, [paint, ready, neutral]);

  const reset = () => { setSaturation(0); setExposure(0); };

  const apply = async () => {
    if (!full.current || busy) return;
    setBusy(true);
    setError('');
    try {
      if (neutral) {
        // 0/0 은 곧 초기화다. 원본과 같은 이미지를 굳이 한 장 더 만들지 않는다.
        const next = await api.applyToneEditor(projectId, cutId, {
          assetId: state.sourceAssetId, saturation: 0, exposure: 0 });
        setState(next);
        setApplied(true);
        onApplied?.(next);
        return;
      }
      const { srcImg, maskImg, w, h } = full.current;
      const src = drawTo(srcImg, w, h);
      const mask = maskAlphaFrom(drawTo(maskImg, w, h));
      const out = new Uint8ClampedArray(src.data.length);
      applyTone(src.data, mask, out, saturation, exposure);   // 프리뷰와 같은 함수

      const canvas = document.createElement('canvas');
      canvas.width = w; canvas.height = h;
      canvas.getContext('2d').putImageData(new ImageData(out, w, h), 0, 0);
      const blob = await new Promise((res) => canvas.toBlob(res, 'image/png'));
      // uploadPhoto 는 (projectId, {filename, mime, blob}) 시그니처다 — File 을 통째로 넘기면
      // blob.size 접근에서 undefined 로 죽는다 (QA 에서 실측된 버그).
      const { assetId } = await api.uploadPhoto(
        projectId, { filename: 'tone-adjusted.png', mime: 'image/png', blob });
      const next = await api.applyToneEditor(projectId, cutId, { assetId, saturation, exposure });
      setState(next);
      setApplied(true);
      onApplied?.(next);
    } catch (e) {
      setError(e?.message || '적용하지 못했어요.');
    } finally {
      setBusy(false);
    }
  };

  const dirty = useMemo(
    () => saturation !== (state?.adjustment?.saturation || 0)
      || exposure !== (state?.adjustment?.exposure || 0),
    [saturation, exposure, state],
  );

  if (!enabled || !state || state.status === 'disabled') return null;

  // 조정이 중립이면 캔버스를 아예 빼서 원본 <img> 가 그대로 보이게 한다 — 디코드 지연이나
  // 픽셀 왕복을 "원본이 살짝 달라 보인다"로 오독할 여지를 남기지 않는다.
  const overlay = ready && !neutral && overlayRef?.current
    ? createPortal(
      <canvas ref={canvasRef} className="tone-editor-overlay" aria-hidden="true" />,
      overlayRef.current)
    : null;

  return (
    <section className="tone-editor" aria-label="색감·밝기 조정">
      {overlay}
      {state.status === 'processing' && (
        <p className="tone-editor-wait" role="status">색감 조정 준비 중...</p>
      )}
      {state.status === 'failed' && (
        <p className="tone-editor-wait">이 컷은 색감 조정을 지원하지 않아요.</p>
      )}
      {state.status === 'ready' && (
        <>
          {/* 코디 의류를 함께 입은 컷에서만 — 조정 대상이 파는 옷 하나라는 걸 셀러가 알아야
              슬라이더를 믿을 수 있다. 마스크 자체도 그렇게 만들어진다(서버가 보장). */}
          {state.matchingSide && (
            <p className="tone-editor-note">메인 의류에만 적용돼요 — 함께 입은 코디 옷은 그대로예요.</p>
          )}
          <ToneSlider label="색감" value={saturation} range={SATURATION_RANGE}
            disabled={busy || !ready} listId={`tone-sat-${cutId}`}
            onChange={(v) => { setApplied(false); setSaturation(snapSat(v)); }} />
          <ToneSlider label="밝기" value={exposure} range={EXPOSURE_RANGE}
            disabled={busy || !ready} listId={`tone-exp-${cutId}`}
            onChange={(v) => { setApplied(false); setExposure(snapExp(v)); }} />
          <div className="tone-editor-actions">
            <button type="button" className="btn btn-ghost" onClick={reset}
              disabled={busy || neutral}>초기화</button>
            <button type="button" className="btn btn-primary" onClick={apply} disabled={busy || !dirty}>
              {busy ? '적용 중...' : applied && !dirty ? '적용됨 ✓' : '적용'}
            </button>
          </div>
          {applied && !dirty && (
            <p className="tone-editor-done" role="status">적용됐어요 — 다운로드·상세페이지에 이 색감이 쓰여요.</p>
          )}
        </>
      )}
      {error && <p className="tone-editor-error" role="alert">{error}</p>}
    </section>
  );
}

export default ToneEditor;
