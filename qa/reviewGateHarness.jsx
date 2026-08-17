/* ⚠️ 현재 동작하지 않음(2026-08-17 확인) — 아래 import 가 가리키는
   src/features/editor/VaryReviewModal.jsx · reviewGate.js 가 저장소에서 삭제됐다.
   그 탓에 vite 의 의존성 스캔이 실패해 dev 서버가 흰 화면이 됐고(2026-08-16),
   vite.config.js 의 optimizeDeps.entries 를 index.html 로 좁혀 우회했다.
   되살리려면 삭제된 모듈을 복구하거나 이 하네스와 qa-review-gate.html 을 정리해야 한다. */
/* =============================================================
   qa/reviewGateHarness.jsx — 검수 게이트 실 DOM QA (dev 전용, 빌드 미포함)

   Editor.jsx 의 게이트 배선을 **그대로** 재현한다(같은 createReviewGate, 같은
   continuation 규칙). 여기서 확인하는 건 단위 테스트가 못 보는 것들이다:
   렌더, 배지 식별, 모달 z-order, focus, ESC, 이미지 로드 실패 fallback,
   좁은 화면 레이아웃, 그리고 "클릭했을 때 실제로 무엇이 일어나는가".

   외부 호출 0 — 이미지는 인라인 SVG, 검수 API 는 아래 fakeReview 다.
   ============================================================= */
import { useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';

import '@/styles/tokens.css';
import '@/styles/app.css';
import '@/styles/features.css';
import { ToastProvider, useToast } from '@/components/ui.jsx';
import { WardrobePanel } from '@/features/editor/EditorPanels.jsx';
import { VaryReviewModal } from '@/features/editor/VaryReviewModal.jsx';
import { InfoBlockModal } from '@/features/editor/InfoBlockModal.jsx';
import { createReviewGate } from '@/features/editor/reviewGate.js';

/* ── fixture ────────────────────────────────────────────────────────────── */

const swatch = (label, bg) => 'data:image/svg+xml;utf8,' + encodeURIComponent(
  `<svg xmlns="http://www.w3.org/2000/svg" width="200" height="260">
     <rect width="200" height="260" fill="${bg}"/>
     <text x="100" y="135" font-size="15" text-anchor="middle" fill="#111">${label}</text>
   </svg>`);

// 서버 계약과 같은 모양 — repo._wardrobe_image_api 가 내려주는 필드 그대로.
const WARDROBE = {
  col1: [
    { id: 'w-plain', src: swatch('업로드', '#e8e4dc'), ai: false, cutType: 'product',
      editSessionId: null, qcStatus: null, needsReview: false, reviewDecision: null,
      sourceAssetId: null, sourceSrc: null, qcSummary: null },
    { id: 'w-pass', src: swatch('pass', '#dbeafe'), ai: true, cutType: 'styling',
      editSessionId: 'sess-pass', qcStatus: 'pass', needsReview: false, reviewDecision: null,
      sourceAssetId: 'a-1', sourceSrc: swatch('원본', '#f4f4f5'),
      qcSummary: { decision: 'pass', requestedChangeSatisfied: true,
                   unexpectedChanges: [], lockedInvariantViolations: [], visionStatus: 'ok' } },
  ],
  col2: [
    { id: 'w-unreviewed', src: swatch('review_required', '#fef3c7'), ai: true, cutType: 'styling',
      editSessionId: 'sess-a', qcStatus: 'review_required', needsReview: true, reviewDecision: null,
      sourceAssetId: 'a-2', sourceSrc: swatch('원본', '#f4f4f5'),
      qcSummary: { decision: 'review_required', requestedChangeSatisfied: true,
                   unexpectedChanges: ['cuffY', 'hemY'],
                   lockedInvariantViolations: [], visionStatus: 'ok' } },
    { id: 'w-accepted', src: swatch('accepted', '#dcfce7'), ai: true, cutType: 'styling',
      editSessionId: 'sess-b', qcStatus: 'review_required', needsReview: true,
      reviewDecision: 'accepted', sourceAssetId: 'a-3', sourceSrc: swatch('원본', '#f4f4f5'),
      qcSummary: { decision: 'review_required', requestedChangeSatisfied: true,
                   unexpectedChanges: ['poseChanged'],
                   lockedInvariantViolations: [], visionStatus: 'ok' } },
    { id: 'w-rejected', src: swatch('rejected', '#f4f4f5'), ai: true, cutType: 'styling',
      editSessionId: 'sess-c', qcStatus: 'review_required', needsReview: true,
      reviewDecision: 'rejected', sourceAssetId: 'a-4', sourceSrc: swatch('원본', '#f4f4f5'),
      qcSummary: { decision: 'review_required', requestedChangeSatisfied: false,
                   unexpectedChanges: ['patternChanged'],
                   lockedInvariantViolations: ['collarChanged'], visionStatus: 'ok' } },
  ],
  misc: [
    // sourceSrc 누락 — 비교 UI 의 fallback 을 본다.
    { id: 'w-nosource', src: swatch('원본없음', '#ede9fe'), ai: true, cutType: 'styling',
      editSessionId: 'sess-d', qcStatus: 'review_required', needsReview: true, reviewDecision: null,
      sourceAssetId: null, sourceSrc: null,
      qcSummary: { decision: 'review_required', requestedChangeSatisfied: false,
                   unexpectedChanges: [], lockedInvariantViolations: [], visionStatus: 'timeout' } },
    // 긴 사유 — 레이아웃이 깨지는지 본다.
    { id: 'w-long', src: swatch('긴 사유', '#ffe4e6'), ai: true, cutType: 'styling',
      editSessionId: 'sess-e', qcStatus: 'review_required', needsReview: true, reviewDecision: null,
      sourceAssetId: 'a-5', sourceSrc: swatch('원본', '#f4f4f5'),
      qcSummary: { decision: 'review_required', requestedChangeSatisfied: false,
                   unexpectedChanges: ['cameraChanged', 'framingChanged', 'poseChanged',
                                       'backgroundChanged', 'lightingChanged', 'bodyWidth',
                                       'shoulderWidth', 'hemY', 'cuffY', 'centerX', 'centerY',
                                       'subjectHeight', 'backgroundDeltaE'],
                   lockedInvariantViolations: ['collarChanged', 'sleevesChanged',
                                               'buttonsChanged', 'pocketsChanged',
                                               'patternChanged', 'logoChanged'],
                   visionStatus: 'ok' } },
    // 이미지 로드 실패 — 깨진 src.
    { id: 'w-broken', src: '/qa/does-not-exist.png', ai: true, cutType: 'styling',
      editSessionId: 'sess-f', qcStatus: 'review_required', needsReview: true, reviewDecision: null,
      sourceAssetId: 'a-6', sourceSrc: '/qa/also-missing.png',
      qcSummary: { decision: 'review_required', requestedChangeSatisfied: true,
                   unexpectedChanges: [], lockedInvariantViolations: [], visionStatus: 'ok' } },
  ],
};

const COLOR_OPTS = [{ id: 'col1', label: '아이보리', hex: '#efe9dd' },
                    { id: 'col2', label: '네이비', hex: '#2b3a55' }];

/* ── fake review API — 성공/실패/지연/중복을 화면에서 바꾼다 ────────────── */

const MODES = [
  { id: 'ok', label: '성공' },
  { id: 'fail', label: '실패(500)' },
  { id: 'slow', label: '지연 1.5s' },
  { id: 'slowfail', label: '지연 후 실패' },
];

function Harness() {
  const toast = useToast();
  const [wardrobe, setWardrobe] = useState(WARDROBE);
  const [mode, setMode] = useState('ok');
  const [review, setReview] = useState(null);
  const [infoOpen, setInfoOpen] = useState(null);   // 'feature_icons' | 'model_info'
  const [pendingSlot, setPendingSlot] = useState(false);
  const [log, setLog] = useState([]);
  const [canvas, setCanvas] = useState([]);
  const modeRef = useRef(mode); modeRef.current = mode;
  const say = (m) => setLog((l) => [`${l.length + 1}. ${m}`, ...l].slice(0, 14));

  // 서버 기록 대역. 실제 계약과 같은 성공/실패 의미(true/false)만 돌려준다.
  const calls = useRef(0);
  const record = async (im, decision) => {
    const m = modeRef.current;
    calls.current += 1;
    say(`API #${calls.current} ${im.id} → ${decision} (${m})`);
    if (m === 'slow' || m === 'slowfail') await new Promise((r) => setTimeout(r, 1500));
    if (m === 'fail' || m === 'slowfail') { toast.push('검수 기록에 실패했어요', { icon: 'alertTri' }); return false; }
    setWardrobe((w) => {
      const nw = {};
      for (const [g, arr] of Object.entries(w)) nw[g] = arr.map((x) => x.id === im.id ? { ...x, reviewDecision: decision } : x);
      return nw;
    });
    return true;
  };

  // Editor.jsx 와 같은 구조: 게이트 하나, 목적은 호출자가 만든다.
  const gateRef = useRef(null);
  const gate = () => (gateRef.current ||= createReviewGate({ record, onChange: setReview }));

  const insertPastGate = (im) => {
    if (pendingSlot) { setPendingSlot(false); say(`SLOT ← ${im.id}`); setCanvas((c) => [...c, `slot:${im.id}`]); }
    else { say(`CANVAS ← ${im.id}`); setCanvas((c) => [...c, `canvas:${im.id}`]); }
  };
  const requestWardrobeUse = (im, use) => gate().request(im, use);

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '320px 1fr', gap: 24, padding: 24,
                  fontFamily: 'var(--font-body, system-ui)', minHeight: '100vh' }}>
      <div>
        <h2 style={{ marginTop: 0, fontSize: 17 }}>의류 (실 WardrobePanel)</h2>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
          {MODES.map((m) => (
            <button key={m.id} data-qa={`mode-${m.id}`} onClick={() => setMode(m.id)}
              style={{ padding: '4px 9px', fontSize: 12, borderRadius: 6,
                       border: mode === m.id ? '2px solid #2563eb' : '1px solid #d4d4d8',
                       background: '#fff', cursor: 'pointer' }}>{m.label}</button>
          ))}
        </div>
        <label style={{ fontSize: 13, display: 'block', marginBottom: 10 }}>
          <input type="checkbox" data-qa="pending-slot" checked={pendingSlot}
            onChange={(e) => setPendingSlot(e.target.checked)} /> pendingSlot 모드
        </label>
        <WardrobePanel wardrobe={wardrobe} colorOpts={COLOR_OPTS}
          pendingSlot={pendingSlot ? { blockId: 'b', elId: 'e' } : null}
          onInsert={(im) => requestWardrobeUse(im, insertPastGate)}
          onUpload={() => say('upload')} onVaryImage={(im) => say(`vary source ← ${im.id}`)}
          onDeleteSelected={(ids) => say(`delete ${ids.join(',')}`)} onFreshSeen={() => {}} />
      </div>

      <div>
        <h2 style={{ marginTop: 0, fontSize: 17 }}>정보 블록 (실 InfoBlockModal)</h2>
        <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
          <button data-qa="open-feature" onClick={() => setInfoOpen('feature_icons')}
            style={{ padding: '6px 12px', cursor: 'pointer' }}>특징 포인트 열기</button>
          <button data-qa="open-model" onClick={() => setInfoOpen('model_info')}
            style={{ padding: '6px 12px', cursor: 'pointer' }}>모델 정보 열기</button>
          <button data-qa="reset" onClick={() => { setCanvas([]); setLog([]); calls.current = 0; setWardrobe(WARDROBE); }}
            style={{ padding: '6px 12px', cursor: 'pointer' }}>초기화</button>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <section>
            <h3 style={{ fontSize: 14 }}>캔버스에 들어간 것 <span data-qa="canvas-count">{canvas.length}</span></h3>
            <ul data-qa="canvas" style={{ fontSize: 13, paddingLeft: 18 }}>
              {canvas.map((c, i) => <li key={i}>{c}</li>)}
            </ul>
          </section>
          <section>
            <h3 style={{ fontSize: 14 }}>이벤트 로그 (API 호출 <span data-qa="api-count">{calls.current}</span>)</h3>
            <ul data-qa="log" style={{ fontSize: 12, paddingLeft: 18, color: '#52525b' }}>
              {log.map((l, i) => <li key={i}>{l}</li>)}
            </ul>
          </section>
        </div>
      </div>

      {infoOpen && (
        <InfoBlockModal type={infoOpen} ctx={{ sellingPoints: ['가벼운 무게', '자연 소재', '데일리 핏'], fmModels: [] }}
          initialInfo={infoOpen === 'feature_icons'
            ? { items: [{ title: 'A', desc: '', src: null }, { title: 'B', desc: '', src: null }, { title: 'C', desc: '', src: null }] }
            : { models: [{ name: 'MODEL A', height: '', size: '', src: null }] }}
          wardrobe={wardrobe} colorOpts={COLOR_OPTS} editing={false}
          onRequestUse={requestWardrobeUse}
          onClose={() => { setInfoOpen(null); say('info modal closed'); }}
          onSubmit={(info) => { say(`submit ${JSON.stringify(info).slice(0, 90)}`); setInfoOpen(null); }} />
      )}
      {review && (
        <VaryReviewModal image={review.image} busy={review.busy}
          onAccept={() => gate().accept()} onReject={() => gate().reject()}
          onClose={() => gate().close()} />
      )}
    </div>
  );
}

createRoot(document.getElementById('qa-root')).render(
  <ToastProvider><Harness /></ToastProvider>);
