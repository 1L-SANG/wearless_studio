/* =============================================================
   lib/detailPageJobEvents — 상세페이지 생성 잡 이벤트 → 잡 상태 반영(순수 함수).
   store(useAppStore)가 Vite 전용 임포트에 묶여 있어 node --test 로 직접 못 부른다.
   2026-09-26: 테스트로 고정하려고 store 에서 떼어 냈다(동작은 아래 표시한 곳만 바뀜).
   ============================================================= */
import { assetFileUrl } from './assetUrl.js';

/* 이벤트는 append-only 원장이라 재수신(after=0 재생)에도 같은 결과로 수렴한다 — 새로고침
   복원의 근거.

   cut_done 에 previewUrl 이 없는 컷(2026-09-26): REAL(FaceMarket 실존 모델) 얼굴이 들어간
   컷은 서버가 최종 권한 확인 전까지 출력 위치를 원장에 남기지 않는다(detail_page_job._store_cut).
   예전에는 이 컷을 { url: undefined } 로 적어, 대기 타일이 '생성 중'에서 '아직 시작 안 함'으로
   되돌아가 보였다 — 다 만든 컷이 오히려 뒤로 간 것처럼. 이제 done 표식을 남겨 타일이
   '완성됐어요'를 말하고, 이미 받아 둔 주소가 있으면 없는 값으로 덮지 않는다. */
export function applyDetailJobEvents(job, events) {
  const next = { ...job, cuts: { ...job.cuts }, copy: { ...job.copy } };
  const live = new Set(next.live);
  const failed = new Set(next.failedCuts);
  for (const e of events || []) {
    const p = e?.payload || {};
    if (e?.type === 'progress') {
      next.progress = Math.max(next.progress, p.progress || 0);
      if (p.phase) next.phase = p.phase;
      if (p.phase === 'cut') {
        next.cutsDone = p.done ?? next.cutsDone;
        next.cutsTotal = p.total ?? next.cutsTotal;
      }
    } else if (e?.type === 'step' && p.blockId) {
      if (p.status === 'cut_start') live.add(p.blockId);
      if (p.status === 'cut_done') {
        live.delete(p.blockId);
        const prev = next.cuts[p.blockId];
        next.cuts[p.blockId] = {
          url: p.previewUrl || prev?.url || null,
          width: p.width ?? prev?.width,
          height: p.height ?? prev?.height,
          done: true,
        };
      }
      if (p.status === 'cut_passthrough') {
        live.delete(p.blockId);
        // 셀러 원본 재사용 — asset 행이 이미 있어 안정 /file 경로가 즉시 유효
        next.cuts[p.blockId] = { url: p.assetId ? assetFileUrl(p.assetId) : null, done: true };
      }
      if (p.status === 'cut_failed') { live.delete(p.blockId); failed.add(p.blockId); }
      if (p.status === 'copy_ready') next.copy[p.blockId] = p.texts || [];
    }
  }
  next.live = [...live];
  next.failedCuts = [...failed];
  return next;
}
