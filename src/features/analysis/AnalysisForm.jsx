/* =============================================================
   features/analysis — ② AI 분석·세부 확인 (PRD §6)
   Ported verbatim from reference/prototype/features/analysis.jsx.
   Only change: ES imports + exports (was window globals). Markup,
   classNames, inline styles unchanged.
   ============================================================= */
import { useState, useEffect, useCallback, useRef } from 'react';
import { api } from '@/lib/api/index.js';
import { listModels, fetchLicenseFaceUrl, verifyLicensePublic } from '@/lib/api/facemarket.js';
import QRCode from 'qrcode';
import { isGenerationRelevantAnalysisPatch, useAppStore } from '@/store/useAppStore.js';
import { Icon, Chips, Button, Skeleton, ErrorState, Modal, useToast } from '@/components/ui.jsx';
import { useSteppedProgress } from '@/components/SmoothProgress.jsx';
import { PageHead, WizardCTA } from '@/features/shell/shell.jsx';
import { axesFor, fitProfileCategory } from '@/lib/fitAxes.js';
import {
  genderForClothingType,
  normalizeTargetGendersForClothingType,
} from '@/lib/productGender.js';
import {
  matchingFitDefinition,
  matchingFitFromProfile,
  resolveMainMatchingItem,
} from '@/lib/matchingFit.js';
import { mergeMatchClothing, reconcileMatchCompatibility } from '@/lib/api/matchingItems.js';
import { looksLikeImageFile, toUploadableImages } from '@/lib/imageTranscode.js';
import { invalidateStoryboardEntryPrefetch } from '@/features/storyboard/storyboardEntryPrefetch.js';
import { resolveSelectedModelId } from './modelSelection.js';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { SELLING_POINTS_MAX, applySellingPointEdit } from './sellingPoints.js';
import { selectAnalysisComposeMode } from './composeModeSelection.js';

// 모델 카드 썸네일 — 얼굴=생체 PII라 공개 URL 없음. 활성 라이선스 얼굴 게이트 URI(faceThumbUri)를
// Bearer fetch 로 받아 objectURL 로 표시하고, 언마운트 시 해제한다(fetchLicenseFaceUrl 계약).
// export: 에디터 AI 탭 실존 모델 피커(EditorPanels.AIPanel)도 같은 게이트 썸네일을 쓴다.
export function ModelThumb({ uri, alt }) {
  const [url, setUrl] = useState(null);
  useEffect(() => {
    if (!uri) return undefined;
    let objUrl = null, alive = true;
    fetchLicenseFaceUrl(uri).then((u) => { if (alive) { objUrl = u; setUrl(u); } }).catch(() => {});
    return () => { alive = false; if (objUrl) URL.revokeObjectURL(objUrl); };
  }, [uri]);
  if (url) return <img src={url} alt={alt} />;
  return <div className="fm-empty"><Icon name="person" size={24} /></div>;
}

const _won = (n) => `₩${Number(n || 0).toLocaleString('ko-KR')}`;
const _fmtDate = (iso) => { if (!iso) return null; try { return new Date(iso).toLocaleDateString('ko-KR'); } catch { return iso; } };

// 모델 상세 = 얼굴 라이선스 카드. 공개 검증 화이트리스트(verifyLicensePublic)만 표시하고
// 얼굴은 게이트 썸네일, QR 은 무인증 검증 페이지({origin}/verify/{id}) 주소만 싣는다(생체정보 X).
// PII(원본 얼굴·CI·생년월일·user_id)는 서버가 애초에 안 싣는다. selectable 이면 여기서 선택 확정.
function ModelDetailModal({ model, onClose, onSelect, selectable }) {
  const [data, setData] = useState(null);
  const [phase, setPhase] = useState('loading'); // loading|ready|nolicense|error
  const [qr, setQr] = useState(null);

  useEffect(() => {
    if (!model?.licenseId) { setPhase('nolicense'); return undefined; }
    let alive = true;
    verifyLicensePublic(model.licenseId)
      .then((d) => { if (alive) { setData(d); setPhase('ready'); } })
      .catch(() => { if (alive) setPhase('error'); });
    const verifyUrl = `${window.location.origin}/verify/${model.licenseId}`;
    QRCode.toDataURL(verifyUrl, { width: 160, margin: 0, errorCorrectionLevel: 'M' })
      .then((u) => { if (alive) setQr(u); }).catch(() => {});
    return () => { alive = false; };
  }, [model]);

  const age = data?.model?.age;
  return (
    <Modal onClose={onClose}>
      <div className="lic-card-wrap">
        <div className="lic-card">
          {/* 얼굴 밴드 */}
          <div className="lic-card-face">
            <ModelThumb uri={model.faceThumbUri} alt={model.displayName} />
            <span className="lic-card-brand">FACE&nbsp;LICENSE</span>
            {model.status === 'verified' && (
              <span className="lic-card-badge"><Icon name="check" size={11} />검증</span>
            )}
            <div className="lic-card-who">
              <div className="lic-card-name">{model.displayName}{age != null && <em> · {age}세</em>}</div>
              {model.vcId
                ? <div className="lic-card-vc"><Icon name="check" size={10} />라이선스 검증서 발급 완료</div>
                : <div className="lic-card-vc off">라이선스 검증서 발급 전</div>}
            </div>
          </div>

          {/* 본문 */}
          <div className="lic-card-body">
            {phase === 'loading' && <div className="hint" style={{ padding: '8px 0' }}>불러오는 중…</div>}
            {phase === 'nolicense' && <div className="hint" style={{ padding: '8px 0' }}>활성 라이선스가 없어 조건을 볼 수 없어요.</div>}
            {phase === 'error' && <div className="hint" style={{ padding: '8px 0' }}>상세 정보를 불러오지 못했어요.</div>}
            {phase === 'ready' && data && (
              <>
                {data.allowedUse?.length > 0 && (
                  <div className="lic-row"><span className="lic-k">허용 용도</span>
                    <span className="lic-tags">{data.allowedUse.map((u) => <span key={u} className="tag-allow">{u}</span>)}</span>
                  </div>
                )}
                {data.forbiddenUse?.length > 0 && (
                  <div className="lic-row"><span className="lic-k">금지 용도</span>
                    <span className="lic-tags">{data.forbiddenUse.map((u) => <span key={u} className="tag-forbid"><Icon name="ban" size={9} />{u}</span>)}</span>
                  </div>
                )}
                <div className="lic-foot">
                  <div className="lic-foot-info">
                    <div className="lic-price">{_won(data.unitPrice)}<em> · 상세페이지 1개당</em></div>
                    {_fmtDate(data.validUntil) && <div className="lic-valid">{_fmtDate(data.validUntil)}까지</div>}
                    {data.vcId && <code className="lic-vcid">{data.vcId}</code>}
                  </div>
                  {qr && <img className="lic-qr" src={qr} alt="검증 QR" />}
                </div>
              </>
            )}
          </div>
        </div>

        <div className="lic-actions">
          <Button variant="ghost" onClick={onClose}>닫기</Button>
          {selectable && (
            <Button variant="primary" iconRight="check" onClick={() => { onSelect(model.id); onClose(); }}>
              이 모델로 선택
            </Button>
          )}
        </div>
      </div>
    </Modal>
  );
}

// 글자 폭 추정(em) — 한글 ≈1em, 그 외 ≈0.55em. '직접 입력' pill이 다른 칩과 같은 크기로
// 시작해 내용 길이만큼만 유동 확장되게 하는 계산 (2026-07-13 사용자 피드백).
const chWidth = (s) => [...s].reduce((n, ch) => n + (/[가-힣]/.test(ch) ? 1 : 0.55), 0).toFixed(1);
import { CREDIT_COSTS } from '@/lib/limits.js';
import {
  createMeasurementFields,
  normalizeMeasurementValue,
  sanitizeMeasurementInput,
} from '@/lib/measurementSchema.js';

export const isMatchRecommendationPatch = (patch) => ['clothingType', 'targetGenders', 'styleTags'].some((key) => key in patch);

// ── 분석 대기 연출 (A안 · 단계 체크리스트 — 2026-07-13 확정, mockups/analysis-waiting-concepts.html) ──
// 2026-07-16 prod 실측(의류 5종·사진 1~3장 7회 벤치): 클릭→완료 = 업로드 3.5~11s +
// 서버 준비 1~4s + AI 4~8s + 폴링 ~1s ≈ 12~22s. 이 애니메이션은 클릭 직후(업로드 포함)
// 시작하므로 앞 4단계 합을 10초로 잡는다 — p50(~13s)에서 마지막 단계 대기가 짧게 남는다.
//
// 앞 4단계는 2.5초씩 균등(오너 결정 2026-08-14). 처음엔 단계마다 길이를 달리 줘 "자연스러운
// 페이스" 를 노렸는데, 진행바가 단계마다 같은 몫(20%)을 가져가는 이상 그 차이가 곧 속도 차로
// 보인다. 대신 변동은 전부 마지막 단계가 흡수한다 — 결과가 안 오면 거기서 더 기다린다.
//
// 결과 선착 시 잔여 단계를 순차(320ms)로 훑어 완주한 뒤 onFinished — 애니메이션 끝과 화면
// 전환이 맞물린다. 분석이 늦으면 마지막 단계 스피너로 은은히 대기(멈춘 느낌 방지).
// 퍼센트 숫자 금지(마네킹 대기화면과 동일 결정).
const ANALYZE_STEPS = ['사진 확인', '종류·핏 판별', '소재 추정', '특징 발굴', '매칭 의류 선정'];
const STEP_MS = 2500;                                    // 앞 4개 합 10000ms (실측 p50 기반)
// 마지막 항목은 실제로 쓰이지 않는다(그 단계는 타이머 없이 결과를 기다린다). 인덱스가
// ANALYZE_STEPS 와 어긋나지 않도록 같은 길이로 채워 둔다.
const STEP_DUR = ANALYZE_STEPS.map(() => STEP_MS);
const FAST_DUR = 320;                               // 결과 선착 시 잔여 단계 순차 훑기(스냅 방지)

const SLOW_NOTICE_MS = 20000; // 이 시간까지 결과가 없으면 안내 문구 전환(R2 지연 등 꼬리 케이스 방어)

export function AnalysisProgress({ photoSrc, done, onFinished }) {
  const [doneCount, setDoneCount] = useState(0);   // 완료된 단계 수 (0..5)
  const [slow, setSlow] = useState(false);         // 20초+ 지연 — 멈춤으로 오해받지 않게 문구만 교체
  const finishedRef = useRef(false);
  const onFinishedRef = useRef(onFinished);
  onFinishedRef.current = onFinished;

  useEffect(() => {
    if (done) { setSlow(false); return undefined; }
    const t = setTimeout(() => setSlow(true), SLOW_NOTICE_MS);
    return () => clearTimeout(t);
  }, [done]);

  useEffect(() => {
    if (doneCount >= ANALYZE_STEPS.length) {       // 전 단계 완료 → 살짝 여운 후 전환
      if (finishedRef.current) return;
      finishedRef.current = true;
      const t = setTimeout(() => onFinishedRef.current?.(), 400);
      return () => clearTimeout(t);
    }
    // 마지막 단계는 결과가 도착해야만 완료 — 그 전엔 스피너 유지 (분석 최대시간 커버)
    if (!done && doneCount === ANALYZE_STEPS.length - 1) return;
    const t = setTimeout(() => setDoneCount((n) => n + 1), done ? FAST_DUR : STEP_DUR[doneCount]);
    return () => clearTimeout(t);
  }, [doneCount, done]);

  // 마지막 단계는 타이머 없이 결과를 기다린다 — 예정 시간이 없다는 뜻으로 null 을 넘긴다.
  const waitingForResult = !done && doneCount === ANALYZE_STEPS.length - 1;
  const plannedMs = waitingForResult ? null : (done ? FAST_DUR : STEP_DUR[doneCount]);

  /* 지금 칸의 시계가 언제 시작됐는지. 렌더 중에 갱신해야 단계가 바뀐 바로 그 프레임부터
     새 칸을 채우기 시작한다(effect 로 미루면 한 프레임 늦게 출발해 경계에서 튄다).

     단계 번호뿐 아니라 **예정 시간이 바뀔 때도** 시계를 다시 건다. 결과가 도착하면
     예정 시간이 2500ms→320ms 로 줄어드는데, 경과시간을 그대로 두면 elapsed/planned 가
     즉시 1 을 넘겨 칸 끝까지 한 프레임에 튄다(실측 9~17%p — 이 작업이 없애려던 바로
     그 증상이 다른 트리거로 남아 있었다). */
  const stepRef = useRef({ key: '', at: Date.now() });
  const stepKey = `${doneCount}:${plannedMs}`;
  if (stepRef.current.key !== stepKey) stepRef.current = { key: stepKey, at: Date.now() };

  const barPercent = useSteppedProgress({
    stepIndex: doneCount,
    stepCount: ANALYZE_STEPS.length,
    stepStartedAt: stepRef.current.at,
    plannedMs,
  });

  return (
    <div className="ap-stage surface">
      {photoSrc && <img className="ap-photo" src={photoSrc} alt="" />}
      <div className="ap-body">
        <div className="ap-title">
          {slow ? '조금 더 꼼꼼히 확인하고 있어요…' : 'AI가 상품을 분석하고 있어요'}
        </div>
        {ANALYZE_STEPS.map((s, i) => (
          <div key={s} className={`ap-step${i < doneCount ? ' done' : i === doneCount ? ' run' : ''}`}>
            <span className="ap-dot" />{s}
          </div>
        ))}
        {/* 단계마다 같은 몫을 그 단계의 예정 시간 동안 균등하게 채운다(steppedProgress).
            숫자는 여전히 안 쓴다 — 마네킹 대기화면과 동일 결정. */}
        <div className="ap-bar"><i style={{ width: `${barPercent}%` }} /></div>
      </div>
    </div>
  );
}

export function AnalysisSkeleton() {
  const chipRow = (ws) => <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>{ws.map((w, i) => <Skeleton key={i} w={w} h={34} r={9999} />)}</div>;
  const fieldRow = (ws, i) => (
    <div className="field-row" key={i}>
      <Skeleton w={52} h={13} r={4} />
      {chipRow(ws)}
    </div>
  );
  const secHead = (tw, sw) => (
    <div className="sec-head"><div>
      <Skeleton w={tw} h={18} r={5} />
      <Skeleton w={sw} h={13} r={4} style={{ marginTop: 9 }} />
    </div></div>
  );
  const cards = (n, ws) => (
    <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
      {Array.from({ length: n }).map((_, i) => (
        <div key={i} style={{ width: 110 }}>
          <Skeleton h={130} r={8} />
          <Skeleton w={ws} h={12} r={4} style={{ marginTop: 8 }} />
        </div>
      ))}
    </div>
  );
  return (
    <div className="af-skeleton" aria-busy="true">
      {/* 진행 헤더는 AnalysisProgress(단계 체크리스트)가 담당 — 여기는 폼 뼈대만 */}
      <div className="merged-card" style={{ marginTop: 16 }}>
        {/* 기본 정보 */}
        <div className="surface">
          <Skeleton w={70} h={18} r={5} style={{ marginBottom: 20 }} />
          <div className="basic-fields">
            {[[64, 64, 72, 56], [60, 68, 56, 64], [52, 52], [64, 52, 72, 60]].map((ws, i) => fieldRow(ws, i))}
          </div>
        </div>
        {/* 소재 */}
        <div className="surface">
          {secHead(50, 200)}
          {chipRow([96, 112, 80])}
        </div>
        {/* 실측 정보 */}
        <div className="surface">
          {secHead(70, 220)}
          <div className="measure-grid">
            {[0, 1, 2, 3].map((i) => (
              <div className="measure-cell" key={i}>
                <Skeleton w={48} h={12} r={4} />
                <Skeleton h={44} r={8} />
              </div>
            ))}
          </div>
        </div>
        {/* 강조하고 싶은 특징 */}
        <div className="surface">
          {secHead(118, 230)}
          {chipRow([90, 72, 124, 96, 108])}
        </div>
        {/* 모델 선택 */}
        <div className="surface">
          <Skeleton w={70} h={18} r={5} />
          <Skeleton w={200} h={13} r={4} style={{ margin: '9px 0 16px' }} />
          {cards(3, 56)}
        </div>
        {/* 매칭 의류 */}
        <div className="surface">
          <Skeleton w={70} h={18} r={5} />
          <Skeleton w={220} h={13} r={4} style={{ margin: '9px 0 16px' }} />
          {cards(4, 64)}
        </div>
      </div>
    </div>
  );
}

// AI(가상) 모델 — 서버 레지스트리(server/app/data/virtual_models.json)와 동기 유지.
// 컷 생성(AG-06)이 이 id('mA'…)로 아이덴티티 자산을 해석하고, 라이선스 게이트는
// 비-UUID id를 no-op 처리한다(과금 없음). 실제 모델(FaceMarket)과 탭으로 구분 표시.
// 이름은 인물 외형에 맞춘다(2026-08-01 사용자 결정): 서양인 = 짧은 영문 이름,
// 동양인 = 짧은 한국어 이름. 'mA'… id 는 서버 자산 키라 그대로 두고 표시명만 바꾼다.
const AI_MODELS = [
  { id: 'mA', displayName: 'Mia', gender: 'women', thumb: '/models/women/w1.webp' },
  { id: 'mB', displayName: 'Leo', gender: 'men', thumb: '/models/men/m1.webp' },
  { id: 'mC', displayName: '도윤', gender: 'men', thumb: '/models/men/m2.webp' },
  { id: 'mD', displayName: '수혁', gender: 'men', thumb: '/models/men/m3.webp' },
  { id: 'mE', displayName: '지안', gender: 'women', thumb: '/models/women/w2.webp' },
  { id: 'mF', displayName: '하린', gender: 'women', thumb: '/models/women/w3.webp' },
  { id: 'mG', displayName: '세아', gender: 'women', thumb: '/models/women/w4.webp' },
  { id: 'mH', displayName: '예린', gender: 'women', thumb: '/models/women/w5.webp' },
  { id: 'mI', displayName: '다인', gender: 'women', thumb: '/models/women/w6.webp' },
  { id: 'mJ', displayName: '소윤', gender: 'women', thumb: '/models/women/w7.webp' },
  { id: 'mK', displayName: '유나', gender: 'women', thumb: '/models/women/w8.webp' },
  { id: 'mL', displayName: '채원', gender: 'women', thumb: '/models/women/w9.webp' },
  { id: 'mM', displayName: '나윤', gender: 'women', thumb: '/models/women/w10.webp' },
  { id: 'mN', displayName: 'Nora', gender: 'women', thumb: '/models/women/w11.webp' },
];

const expectedMatchingType = (clothingType) => {
  if (clothingType === 'dress') return null;
  return clothingType === 'bottom' ? 'top' : 'bottom';
};

const CUSTOM_MATCH_IMAGE_OPTIONS = Object.freeze({
  maxEdge: 1600,
  minEdge: 400,
  forceJpeg: true,
  timeoutMs: 10000,
});

function CustomMatchUploadModal({ projectId, anchorRect, onApply, onClose }) {
  const [phase, setPhase] = useState('idle');
  const [files, setFiles] = useState([]);
  const [error, setError] = useState('');
  const [doneItem, setDoneItem] = useState(null);
  const [dragIndex, setDragIndex] = useState(null);
  const controllerRef = useRef(new AbortController());
  const previewUrlsRef = useRef(new Set());
  const aliveRef = useRef(true);

  // setup 에서 aliveRef 를 반드시 되살린다. StrictMode(개발)는 마운트 직후 effect 를
  // 실행→정리→재실행하는데, 정리에서 false 로 내린 값을 되돌리지 않으면 **모달이 열리자마자
  // 죽은 상태**가 된다. 그러면 addFiles 의 `if (!aliveRef.current) return` 가 항상 걸려
  // phase 가 'preparing' 에 갇히고, 예외도 네트워크 요청도 없이 스피너만 돈다
  // (2026-08-05 오너 재현: "한참 로딩·콘솔 깨끗·서버 요청 0건").
  useEffect(() => {
    aliveRef.current = true;
    const controller = controllerRef.current;
    const previews = previewUrlsRef.current;
    return () => {
      aliveRef.current = false;
      controller.abort();
      previews.forEach((url) => URL.revokeObjectURL(url));
      previews.clear();
    };
  }, []);

  const renewController = () => {
    controllerRef.current.abort();
    controllerRef.current = new AbortController();
    return controllerRef.current;
  };
  const releasePreview = (entry) => {
    if (!entry?.preview) return;
    URL.revokeObjectURL(entry.preview);
    previewUrlsRef.current.delete(entry.preview);
  };
  const toEntry = (file) => {
    const preview = URL.createObjectURL(file);
    previewUrlsRef.current.add(preview);
    return { id: `${Date.now()}-${Math.random()}`, file, preview };
  };
  const addFiles = async (fileList) => {
    if (phase === 'preparing' || phase === 'uploading' || phase === 'analyzing') return;
    const controller = renewController();
    const room = 4 - files.length;
    const picked = [...fileList].filter(looksLikeImageFile).slice(0, room);
    if (!picked.length) {
      setError('JPG, PNG 또는 HEIC 사진을 선택해주세요.');
      return;
    }
    setPhase('preparing');
    try {
      const prepared = await toUploadableImages(
        picked, CUSTOM_MATCH_IMAGE_OPTIONS, { signal: controller.signal },
      );
      if (!aliveRef.current || controller.signal.aborted) return;
      if (prepared.files.length) {
        setFiles((current) => [...current, ...prepared.files.map(toEntry)]);
        setPhase('picking');
      } else {
        setPhase('error');
      }
      setError(prepared.failed.length
        ? `${prepared.failed.length}장은 불러오지 못했어요. 다른 사진으로 다시 시도해 주세요.`
        : '');
    } catch (prepareError) {
      if (prepareError?.name === 'AbortError' || !aliveRef.current) return;
      setError(prepareError?.message || '사진을 준비하지 못했어요. 잠시 후 다시 시도해 주세요.');
      setPhase('error');
    }
  };
  const replaceFile = async (index, file) => {
    if (!looksLikeImageFile(file)) {
      setError('JPG, PNG 또는 HEIC 사진을 선택해주세요.');
      return;
    }
    const controller = renewController();
    setPhase('preparing');
    try {
      const prepared = await toUploadableImages(
        [file], CUSTOM_MATCH_IMAGE_OPTIONS, { signal: controller.signal },
      );
      if (!aliveRef.current || controller.signal.aborted) return;
      if (!prepared.files.length) {
        setError('사진을 불러오지 못했어요. 다른 사진으로 다시 시도해 주세요.');
        setPhase('picking');
        return;
      }
      setFiles((current) => current.map((entry, itemIndex) => {
        if (itemIndex !== index) return entry;
        releasePreview(entry);
        return toEntry(prepared.files[0]);
      }));
      setError('');
      setPhase('picking');
    } catch (prepareError) {
      if (prepareError?.name === 'AbortError' || !aliveRef.current) return;
      setError(prepareError?.message || '사진을 불러오지 못했어요. 잠시 후 다시 시도해 주세요.');
      setPhase('picking');
    }
  };
  const removeFile = (index) => {
    renewController();
    setFiles((current) => {
      releasePreview(current[index]);
      const next = current.filter((_, itemIndex) => itemIndex !== index);
      setPhase(next.length ? 'picking' : 'idle');
      return next;
    });
  };
  const moveFile = (from, to) => {
    if (from === to || from < 0 || to < 0 || from >= files.length || to >= files.length) return;
    renewController();
    setFiles((current) => {
      const next = [...current];
      const [moved] = next.splice(from, 1);
      next.splice(to, 0, moved);
      return next;
    });
  };
  const clearFiles = () => {
    renewController();
    files.forEach(releasePreview);
    setFiles([]);
    setError('');
    setPhase('idle');
  };
  const requestClose = () => {
    controllerRef.current.abort();
    onClose();
  };
  const submit = async () => {
    if (!files.length || phase === 'uploading' || phase === 'analyzing') return;
    const controller = renewController();
    setError('');
    setPhase('uploading');
    try {
      const uploaded = await Promise.all(files.map(({ file }) => api.uploadPhoto(
        projectId,
        {
          filename: file.name,
          mime: file.type,
          blob: file,
          purpose: 'custom_match_source',
        },
        { signal: controller.signal },
      )));
      if (!aliveRef.current || controller.signal.aborted) return;
      setPhase('analyzing');
      const result = await api.addCustomMatchItem(
        projectId,
        { assetIds: uploaded.map((asset) => asset.assetId) },
        { signal: controller.signal },
      );
      if (!aliveRef.current || controller.signal.aborted) return;
      onApply(result.analysis);
      setDoneItem(result.item);
      setPhase('done');
      setTimeout(() => { if (aliveRef.current) requestClose(); }, 500);
    } catch (uploadError) {
      if (uploadError?.name === 'AbortError' || !aliveRef.current) return;
      setError(uploadError?.message || '옷을 추가하지 못했어요. 다시 시도해 주세요.');
      setPhase('error');
    }
  };

  const busy = phase === 'preparing' || phase === 'uploading' || phase === 'analyzing';
  return (
    <Modal onClose={requestClose} narrow anchorRect={anchorRect} glass>
      <div className="custom-match-modal">
        <div className="custom-match-modal-head">
          <div>
            <h3>내 매칭 의류 추가</h3>
            <p>같은 옷 사진을 최대 4장 올려주세요. 1장만으로도 추가할 수 있어요.</p>
          </div>
          <button className="custom-match-close" onClick={requestClose} aria-label="닫기">
            <Icon name="x" size={18} />
          </button>
        </div>

        {/* 상품 입력 페이지의 다중 업로드와 같은 타일 문법(.slot-tiles/.tile/.tile.add) — 사진 타일 +
            점선 추가 타일이 한 줄에 서고, 그리드 어디에 떨궈도 파일이 추가된다. */}
        <div className="slot-tiles custom-match-tiles"
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => { event.preventDefault(); addFiles(event.dataTransfer.files); }}>
          {files.map((entry, index) => (
            <div className="tile custom-match-tile" key={entry.id} draggable={!busy && phase !== 'done'}
              onDragStart={() => setDragIndex(index)}
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault(); event.stopPropagation();
                moveFile(dragIndex, index); setDragIndex(null);
              }}>
              <img src={entry.preview} alt={`${index + 1}번 옷 사진`} />
              <span className="custom-match-order">{index + 1}</span>
              {!busy && phase !== 'done' && (
                <>
                  <button className="rm" onClick={() => removeFile(index)}
                    aria-label={`${index + 1}번 사진 삭제`}>
                    <Icon name="x" size={13} />
                  </button>
                  <div className="custom-match-preview-actions">
                    <button onClick={() => moveFile(index, index - 1)} disabled={index === 0}>앞으로</button>
                    <label>교체<input type="file" accept="image/*,.heic,.heif,.hif"
                      onChange={(event) => { if (event.target.files[0]) replaceFile(index, event.target.files[0]); event.target.value = ''; }} /></label>
                    <button onClick={() => moveFile(index, index + 1)} disabled={index === files.length - 1}>뒤로</button>
                  </div>
                </>
              )}
            </div>
          ))}
          {!busy && phase !== 'done' && files.length < 4 && (
            <label className="tile add custom-match-add-tile">
              <Icon name="plus" size={26} className="add-ico" />
              <span className="add-cap">사진 추가<small>JPG/PNG/HEIC · 최소 400px</small></span>
              <input type="file" accept="image/*,.heic,.heif,.hif" multiple
                onChange={(event) => { addFiles(event.target.files); event.target.value = ''; }} />
            </label>
          )}
        </div>

        {busy && (
          <div className="custom-match-status"><Icon name="loader" className="spin" size={18} />
            {phase === 'preparing' ? '사진을 가볍게 준비하는 중이에요…' : '옷을 확인하는 중이에요…'}
          </div>
        )}
        {phase === 'done' && (
          <div className="custom-match-status success"><Icon name="check" size={18} />{doneItem?.name || '내 옷'}을 추가했어요.</div>
        )}
        {error && <div className="custom-match-error">{error}</div>}

        {phase === 'picking' && (
          <div className="custom-match-modal-actions">
            <span>1번 사진이 타일 썸네일로 보여요.</span>
            <Button variant="primary" onClick={submit}>사진 선택 완료</Button>
          </div>
        )}
        {phase === 'error' && (
          <div className="custom-match-modal-actions">
            <span />
            <Button variant="ghost" onClick={clearFiles}>다른 사진 고르기</Button>
          </div>
        )}
      </div>
    </Modal>
  );
}

export function AnalysisForm({
  inline, analysis, catalogs, onChange, onNext, projectId = null, onAnalysisReplace,
  onConfirmingChange,
}) {
  const a = analysis;
  const toast = useToast();
  const { session, loading: authLoading } = useAuth();
  const composeMode = useAppStore((s) => s.composeMode);
  const setComposeMode = useAppStore((s) => s.setComposeMode);
  const restoreComposeMode = useAppStore((s) => s.restoreComposeMode);
  const composeModeSaveRef = useRef(Promise.resolve());
  const composeModeSelectionRef = useRef({ requestId: 0, confirmedMode: composeMode });
  const [composeModeSaving, setComposeModeSaving] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [composeModeOpen, setComposeModeOpen] = useState(false);
  const composeModeMenuRef = useRef(null);
  const composeModeTriggerRef = useRef(null);
  useEffect(() => {
    if (!composeModeSaving) composeModeSelectionRef.current.confirmedMode = composeMode;
  }, [composeMode, composeModeSaving]);
  useEffect(() => {
    if (!composeModeOpen) return undefined;
    // 열리면 '현재 선택된' 옵션부터 — 첫 옵션 고정 포커스는 선택 상태를 무시한다(리뷰 P2).
    const options = () => [...(composeModeMenuRef.current?.querySelectorAll('[role="option"]') || [])];
    (options().find((el) => el.getAttribute('aria-selected') === 'true') || options()[0])?.focus();
    const closeOnOutsideClick = (event) => {
      if (!composeModeMenuRef.current?.contains(event.target)) setComposeModeOpen(false);
    };
    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        setComposeModeOpen(false);
        composeModeTriggerRef.current?.focus();
        return;
      }
      // listbox 화살표 이동 (roving focus). 옵션이 2개라 위/아래가 곧 서로 전환이다.
      if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
      const list = options();
      if (!list.length) return;
      event.preventDefault();
      const idx = list.indexOf(document.activeElement);
      const next = event.key === 'ArrowDown'
        ? list[Math.min(list.length - 1, idx + 1)] || list[0]
        : list[Math.max(0, idx - 1)] || list[list.length - 1];
      next?.focus();
    };
    document.addEventListener('pointerdown', closeOnOutsideClick);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', closeOnOutsideClick);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [composeModeOpen]);
  useEffect(() => {
    if (composeModeSaving || confirming) setComposeModeOpen(false);
  }, [composeModeSaving, confirming]);
  const [washing, setWashing] = useState(false);
  const [spDraft, setSpDraft] = useState('');
  const [ccDraft, setCcDraft] = useState(a.customCategory || '');   // 직접 입력 pill (blur 커밋)
  const [measurementDraft, setMeasurementDraft] = useState(() => ({
    clothingType: a.clothingType,
    values: Object.fromEntries((a.measurements || []).map((m) => [m.key, m.value == null ? '' : String(m.value)])),
  }));
  // 직접 입력에 커서가 있는 동안 enum 칩 하이라이트를 지운다 — 커밋은 여전히 blur 시점이라
  // 데이터는 안 바뀌고, 빈 채로 나가면 칩 선택이 그대로 돌아온다(오클릭 무해).
  const [ccFocus, setCcFocus] = useState(false);
  useEffect(() => { setCcDraft(a.customCategory || ''); }, [a.customCategory]);
  const [spAdding, setSpAdding] = useState(false);
  // 칩을 눌러 그 자리에서 문구를 고친다 (2026-08-03 사용자 결정) — AI가 뽑아준 특징도 손댈 수 있게.
  const [spEditIdx, setSpEditIdx] = useState(null);
  const [spEditDraft, setSpEditDraft] = useState('');
  const [editMatIdx, setEditMatIdx] = useState(null);
  const matTotal = (a.materials || []).reduce((s, m) => s + (Number(m.ratio) || 0), 0);
  const matOver = matTotal > 100;
  const composeModes = catalogs?.composeModes || [];
  const selectedComposeMode = composeModes.find((mode) => mode.value === composeMode)
    || composeModes[0];
  const selectedComposeModeLabel = selectedComposeMode
    ? `${selectedComposeMode.label} · ${selectedComposeMode.count}컷`
    : '';
  const changeComposeMode = (nextMode) => {
    if (nextMode === composeMode) return;
    setComposeModeSaving(true);
    const pending = selectAnalysisComposeMode({
      currentMode: composeMode,
      nextMode,
      projectId,
      setComposeMode,
      restoreComposeMode,
      invalidateStoryboardPrefetch: invalidateStoryboardEntryPrefetch,
      selectionState: composeModeSelectionRef,
      onFailure: () => {
        toast.push('사진 양을 저장하지 못했어요 — 잠시 후 다시 시도해주세요');
      },
    });
    composeModeSaveRef.current = pending;
    void pending.finally(() => {
      if (composeModeSaveRef.current === pending) setComposeModeSaving(false);
    });
  };
  const confirmAnalysis = async () => {
    if (confirming) return;
    setConfirming(true);
    onConfirmingChange?.(true);
    try {
      await composeModeSaveRef.current;
      await onNext();
    } finally {
      setConfirming(false);
      onConfirmingChange?.(false);
    }
  };
  // 인물 모델 카탈로그 — FaceMarket 검증 모델(GET /v1/facemarket/models, listModels()).
  // 정적 시드를 버리고 런타임 로드한다. 라이선스가 활성인(hasActiveLicense) 모델만 선택 가능.
  const [models, setModels] = useState([]);
  const [modelsLoading, setModelsLoading] = useState(true);
  const [modelsError, setModelsError] = useState('');
  const [modelsAttempt, setModelsAttempt] = useState(0);
  const [detailFor, setDetailFor] = useState(null); // 상세 모달 대상 모델 카드
  const [customMatchOpen, setCustomMatchOpen] = useState(false);
  // 누른 '의류 추가하기' 타일의 화면상 위치 — 모달을 그 바로 위에 띄운다(Modal anchorRect).
  const [customMatchAnchor, setCustomMatchAnchor] = useState(null);
  const [customMatchDeleting, setCustomMatchDeleting] = useState(false);
  // AI 모델 / 실제 모델 탭 (2026-07-21 사용자 결정). 초기 탭은 현재 선택이 속한 쪽.
  const [modelTab, setModelTab] = useState(() =>
    (a.selectedModelId && !AI_MODELS.some((m) => m.id === a.selectedModelId)) ? 'real' : 'ai');
  useEffect(() => {
    if (authLoading) return undefined;
    if (!session) {
      setModels([]);
      setModelsError('');
      setModelsLoading(false);
      return undefined;
    }
    let alive = true;
    setModelsLoading(true);
    setModelsError('');
    listModels()
      .then((list) => { if (alive) setModels(Array.isArray(list) ? list : []); })
      .catch((error) => {
        if (!alive) return;
        setModels([]);
        setModelsError(error?.message || '검증 모델을 불러오지 못했어요.');
      })
      .finally(() => { if (alive) setModelsLoading(false); });
    return () => { alive = false; };
  }, [authLoading, modelsAttempt, session]);

  useEffect(() => {
    const normalized = normalizeTargetGendersForClothingType(
      a.clothingType,
      a.targetGenders,
    );
    if (a.targetGenders?.length !== 1 || a.targetGenders[0] !== normalized[0]) {
      onChange({ targetGenders: normalized });
    }
  }, [a.clothingType, a.targetGenders, onChange]);

  // AI 추천 특징은 일단 강제로 칩에 채워둔다 (사용자가 지우면 빠짐). 최대 5개.
  useEffect(() => {
    const ai = a.aiSuggestedPoints || [];
    const missing = ai.filter((p) => !a.sellingPoints.includes(p));
    if (missing.length) onChange({ sellingPoints: [...a.sellingPoints, ...missing].slice(0, SELLING_POINTS_MAX) });
  }, []);

  // 카탈로그 로드 후 선택값이 AI 모델도, 라이선스 활성 실제 모델도 아니면 첫 AI 모델로 자동 선택.
  // 기본은 무료 AI 모델 — 실제 모델(유료 라이선스)은 사용자가 탭에서 명시적으로 고를 때만.
  // (AI 모델 id 'mA'…는 비-UUID라 생성 라이선스 게이트가 no-op 처리 — 레거시 호환 확인됨.)
  useEffect(() => {
    const nextSelectedModelId = resolveSelectedModelId({
      selectedModelId: a.selectedModelId,
      targetGenders: a.targetGenders,
      models,
      modelsLoading,
      aiModels: AI_MODELS,
    });
    if (nextSelectedModelId !== a.selectedModelId) {
      onChange({ selectedModelId: nextSelectedModelId });
    }
  }, [models, modelsLoading, a.selectedModelId, a.targetGenders, onChange]);
  const aiSet = new Set(a.aiSuggestedPoints || []);
  const selectableModels = models.filter((model) => model.hasActiveLicense);
  const pendingLicenseModels = models.filter((model) => !model.hasActiveLicense);
  const applyAnalysisReplacement = useCallback((nextAnalysis) => {
    if (!nextAnalysis) return;
    if (onAnalysisReplace) onAnalysisReplace(nextAnalysis);
    else onChange(nextAnalysis);
  }, [onAnalysisReplace, onChange]);
  const closeCustomMatchModal = useCallback(async () => {
    setCustomMatchOpen(false);
    try {
      const actual = await api.refreshMatchClothing(projectId);
      applyAnalysisReplacement(actual);
    } catch (refreshError) {
      toast.push(refreshError?.message || '매칭 의류 상태를 다시 불러오지 못했어요.', { icon: 'alertTri' });
    }
  }, [applyAnalysisReplacement, projectId, toast]);
  const removeCustomMatch = useCallback(async () => {
    if (customMatchDeleting) return;
    if (!window.confirm('업로드한 매칭 의류를 삭제할까요?')) return;
    setCustomMatchDeleting(true);
    try {
      const result = await api.removeCustomMatchItem(projectId);
      applyAnalysisReplacement(result.analysis);
    } catch (removeError) {
      toast.push(removeError?.message || '내 옷을 지우지 못했어요.', { icon: 'alertTri' });
      try {
        applyAnalysisReplacement(await api.refreshMatchClothing(projectId));
      } catch { /* 원래 삭제 오류를 사용자에게 유지 */ }
    } finally {
      setCustomMatchDeleting(false);
    }
  }, [applyAnalysisReplacement, customMatchDeleting, projectId, toast]);

  // 커스텀 매칭 누끼는 백그라운드에서 ~25s 처리된다(Task 5). processing 인 아이템이 하나라도
  // 있는 동안만 기존 refreshMatchClothing 을 5s 간격으로 폴링해 ready/failed 로 교체한다 —
  // 새 API 는 만들지 않는다. 더 이상 processing 이 없으면 인터벌을 정리해 폴링을 멈춘다.
  // 폴링 결과는 matchClothing 만 머지한다(전체 치환 금지). onAnalysisReplace 는 상위의
  // setAnalysis 라 함수형 업데이트를 받는다 — 인터벌 클로저가 붙잡고 있는 옛 analysis 로
  // 덮어쓰면 저장 왕복 중이던 편집이 한 틱 되돌아간다.
  const applyMatchClothingRefresh = useCallback((nextAnalysis) => {
    if (!Array.isArray(nextAnalysis?.matchClothing)) return;
    if (onAnalysisReplace) onAnalysisReplace((prev) => mergeMatchClothing(prev, nextAnalysis));
    else onChange({ matchClothing: nextAnalysis.matchClothing });
  }, [onAnalysisReplace, onChange]);
  const hasPendingCutout = (a.matchClothing || []).some((item) => item.cutoutStatus === 'processing');
  useEffect(() => {
    if (!hasPendingCutout || !projectId) return undefined;
    const interval = setInterval(() => {
      api.refreshMatchClothing(projectId)
        .then((actual) => applyMatchClothingRefresh(actual))
        .catch(() => { /* 다음 tick 에서 재시도 — 사용자에게 토스트 스팸 없음 */ });
    }, 5000);
    return () => clearInterval(interval);
  }, [hasPendingCutout, projectId, applyMatchClothingRefresh]);

  const commitSp = () => {
    const t = spDraft.trim();
    if (!t) { setSpAdding(false); setSpDraft(''); return; }
    onChange({ sellingPoints: [...a.sellingPoints, t] });
    setSpDraft(''); setSpAdding(false);
  };

  // 편집 대상 인덱스를 ref 로도 들고 있는다 — blur 핸들러가 "지금 열린 편집이 내 것인가"를
  // 즉시(리렌더 전에) 판단해야 한다. 아래 mousedown 커밋과 짝이다.
  const spEditIdxRef = useRef(null);
  const startEditSp = (i) => { spEditIdxRef.current = i; setSpEditIdx(i); setSpEditDraft(a.sellingPoints[i] || ''); };
  const cancelEditSp = () => { spEditIdxRef.current = null; setSpEditIdx(null); setSpEditDraft(''); };
  // Enter 로 확정하면 이어서 blur 도 오는데, 인덱스를 먼저 비워 두 번 반영되지 않게 한다.
  // confirmed=false(포커스 이탈)일 때 빈 문구는 삭제가 아니라 취소다: blur 로 목록이 줄면
  // 그 직후 도착하는 click 의 인덱스가 밀려 엉뚱한 칩이 열린다(실측 확인).
  const commitSpEdit = (confirmed) => {
    if (spEditIdx === null) return;
    const patch = applySellingPointEdit({
      sellingPoints: a.sellingPoints,
      aiSuggestedPoints: a.aiSuggestedPoints,
      index: spEditIdx,
      text: spEditDraft,
      allowDelete: confirmed,
    });
    cancelEditSp();
    if (patch) onChange(patch);
  };
  // 한글 IME 조합 중의 Enter 는 글자 확정용이다 — 칩 확정까지 하면 "수정"을 치다가 창이 닫힌다.
  const isComposing = (e) => e.nativeEvent?.isComposing === true;

  const subCats = catalogs.subCategories[a.clothingType] || [];
  const selMatch = (a.matchClothing || []).filter((c) => c.selected).sort((x, y) => (x.selOrder || 0) - (y.selOrder || 0));
  const mainMatchId = selMatch[0]?.id;
  const isMatchCompatible = (item, clothingType = a.clothingType) => {
    const expectedType = expectedMatchingType(clothingType);
    return expectedType !== null
      && item.isCompatible !== false
      && (item.clothingType == null || item.clothingType === expectedType);
  };
  const withMatchSelection = (matchClothing) => {
    const category = fitProfileCategory(a.clothingType, a.subCategory) || 'top';
    const gender = genderForClothingType(a.clothingType, a.targetGenders);
    const previousProfile = a.fitProfile;
    const sameProfileScope = previousProfile?.category === category && previousProfile?.gender === gender;
    const previousMain = resolveMainMatchingItem(a);
    const nextAnalysis = { ...a, matchClothing };
    const nextMain = resolveMainMatchingItem(nextAnalysis);
    // A legacy matchCut has no clothingId, so it is safe to migrate only when
    // the authoritative main garment did not change. v2 matchingFit validates
    // its own binding in matchingFitFromProfile.
    const profileForMatching = previousMain?.id === nextMain?.id
      ? previousProfile
      : { ...(previousProfile || {}), matchCut: undefined };
    const matchingFit = sameProfileScope
      ? matchingFitFromProfile(
        profileForMatching,
        matchingFitDefinition(nextMain, gender),
      )
      : null;
    return {
      matchClothing,
      fitProfile: {
        category,
        gender,
        axes: sameProfileScope ? { ...(previousProfile.axes || {}) } : {},
        source: previousProfile?.source || 'auto',
        version: 2,
        ...(matchingFit ? { matchingFit } : {}),
      },
    };
  };
  const toggleMatch = (id) => {
    const cur = a.matchClothing;
    const item = cur.find((c) => c.id === id);
    if (!item || !isMatchCompatible(item)) return;
    if (item.selected) {
      const next = cur.map((c) => c.id === id ? { ...c, selected: false, selOrder: undefined } : c);
      onChange(withMatchSelection(next));
    } else {
      const next = cur.map((c) => c.id === id
        ? { ...c, selected: true, selOrder: 1 }
        : { ...c, selected: false, selOrder: undefined });
      onChange(withMatchSelection(next));
    }
  };
  const setMat = (i, patch) => onChange({ materials: a.materials.map((m, j) => j === i ? { ...m, ...patch } : m) });
  const draftWash = async () => {
    setWashing(true);
    try {
      const t = await api.draftWashCare(useAppStore.getState().projectId);
      onChange({ washCare: t });
      toast.push('AI 초안을 채웠어요 · 실제 케어라벨과 확인해주세요', { icon: 'sparkles' });
    } catch (e) {
      toast.push(e.message || '세탁 초안 생성에 실패했어요', { icon: 'alert' });
    } finally { setWashing(false); }
  };
  // ── 핏 = fitProfile.axes.fit 의 셀러 편집기 (spec §1 — '핏' 개념이 두 번 보이지 않게 단일화) ──
  // 값 세트는 카테고리×성별로 fitAxes 에서 파생 (여성 상의 = 타이트~오버 5단 등). 원피스는 핏 축 없음 → 행 숨김.
  const fitOptsOf = (draft) => {
    const cat = fitProfileCategory(draft.clothingType, draft.subCategory) || 'top';
    const values = axesFor(
      cat,
      genderForClothingType(draft.clothingType, draft.targetGenders),
    ).fit || [];
    return { cat, opts: values.map(({ value, label }) => ({ value, label })) };
  };
  // patch 적용 후의 핏·fitProfile 을 함께 산출. 카테고리·성별 변경으로 기존 값이 무효면 regular(없으면 첫 값)로 방어 리셋.
  const withFitProfile = (patch, source) => {
    const next = { ...a, ...patch };
    const { cat, opts } = fitOptsOf(next);
    let fit = 'fit' in patch ? patch.fit : next.fit;
    let src = source;
    if (!opts.length) fit = null; // 원피스 등 핏 축 없는 카테고리
    else if (!opts.some((o) => o.value === fit)) { fit = opts.some((o) => o.value === 'regular') ? 'regular' : opts[0].value; src = 'auto'; }
    const prev = next.fitProfile;
    const gender = genderForClothingType(next.clothingType, next.targetGenders);
    const sameProfileScope = prev?.category === cat && prev?.gender === gender;
    const axes = sameProfileScope ? { ...(prev.axes || {}) } : {}; // 카테고리·성별이 바뀌면 타 축 무효 → 리셋
    if (fit === null) delete axes.fit; else axes.fit = fit;
    const matchingFit = sameProfileScope
      ? matchingFitFromProfile(
        prev,
        matchingFitDefinition(resolveMainMatchingItem(next), gender),
      )
      : null;
    return { ...patch, fit, fitProfile: {
      category: cat, gender, axes,
      source: src ?? prev?.source ?? 'auto', version: 2,
      ...(matchingFit ? { matchingFit } : {}),
    } };
  };
  // subCategory 는 영문 토큰, 실측 key 는 MeasurementKey — 라벨은 catalogs 에서 파생 (계약 §4)
  const changeType = (t) => {
    if (!t || t === a.clothingType) return;
    setMeasurementDraft({ clothingType: t, values: {} });
    const matchClothing = reconcileMatchCompatibility(a.matchClothing, t);
    onChange(withFitProfile({
      clothingType: t,
      subCategory: (catalogs.subCategories[t] || [])[0]?.value ?? null,
      targetGenders: normalizeTargetGendersForClothingType(t, a.targetGenders),
      measurements: createMeasurementFields(t),
      matchClothing,
    }));
  };
  const measurementValues = Object.fromEntries((a.measurements || []).map((m) => [m.key, m.value]));
  const visibleMeasurements = createMeasurementFields(a.clothingType, measurementValues);
  const setMeasure = (key, value) => onChange({ measurements: visibleMeasurements.map((m) => m.key === key ? { ...m, value: normalizeMeasurementValue(value) } : m) });
  const measurementInputValue = (measurement) => (
    measurementDraft.clothingType === a.clothingType && measurement.key in measurementDraft.values
      ? measurementDraft.values[measurement.key]
      : (measurement.value ?? '')
  );
  const editMeasurement = (key, rawValue) => {
    const value = sanitizeMeasurementInput(rawValue);
    setMeasurementDraft((current) => ({
      clothingType: a.clothingType,
      values: {
        ...(current.clothingType === a.clothingType ? current.values : {}),
        [key]: value,
      },
    }));
    if (!value.endsWith('.')) setMeasure(key, value);
  };
  const commitMeasurement = (key, rawValue) => {
    const value = normalizeMeasurementValue(rawValue);
    setMeasurementDraft((current) => ({
      clothingType: a.clothingType,
      values: {
        ...(current.clothingType === a.clothingType ? current.values : {}),
        [key]: value == null ? '' : String(value),
      },
    }));
    setMeasure(key, value);
  };
  const typeLabel = catalogs.clothingTypes.find((t) => t.value === a.clothingType)?.label;
  const fitOpts = fitOptsOf(a).opts;
  const genderOptions = a.clothingType === 'dress'
    ? catalogs.genders.filter((option) => option.value === 'women')
    : catalogs.genders;

  const sections = (
    <>
      {/* 1. basic info */}
      <div className="surface">
        <div className="sec-title" style={{ marginBottom: 20 }}>기본 정보</div>
        <div className="basic-fields">
          <div className="field-row"><label className="lbl">의류 종류</label>
            <Chips options={catalogs.clothingTypes} value={a.clothingType} onChange={changeType} allowDeselect={false} /></div>
          {/* 세부 카테고리 — enum 칩 + 같은 줄 끝의 '직접 입력' pill (2026-07-13 사용자 결정:
              별도 줄이 아니라 칩처럼). enum 선택 ↔ 직접 입력은 배타: 칩을 고르면 custom을
              비우고, custom을 쓰면 칩 해제. AI 추측(customCategory)이 있으면 pill에 채워짐.
              저장은 blur/Enter 커밋, key로 분석 갱신 시 리셋(소재 인라인 편집 관례). */}
          <div className="field-row"><label className="lbl">세부 카테고리</label>
            <Chips options={subCats} value={ccFocus ? null : a.subCategory}
              allowDeselect={false}
              onChange={(v) => onChange(withFitProfile({ subCategory: v, customCategory: null }))}
              trailing={
                <span className="chip-input-wrap">
                  <input
                    className={`chip chip-input${ccFocus || (a.customCategory && !a.subCategory) ? ' on' : ''}`}
                    value={ccDraft} maxLength={20} placeholder="직접 입력"
                    style={{ width: `calc(${chWidth(ccDraft || '직접 입력')}em + 32px)` }}
                    onChange={(e) => setCcDraft(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); }}
                    onFocus={() => setCcFocus(true)}
                    onBlur={() => {
                      setCcFocus(false);
                      const v = ccDraft.trim();
                      if (v === (a.customCategory || '')) return;
                      onChange(withFitProfile(v ? { customCategory: v, subCategory: null } : { customCategory: null }));
                    }} />
                  {ccDraft.trim() !== (a.customCategory || '') && <span className="chip-input-pending">적용 전</span>}
                </span>
              } /></div>
          <div className="field-row"><label className="lbl">대상 성별</label>
            <Chips options={genderOptions} value={a.targetGenders?.[0] || null}
              allowDeselect={false}
              onChange={(v) => onChange(withFitProfile({
                targetGenders: normalizeTargetGendersForClothingType(
                  a.clothingType,
                  v ? [v] : [],
                ),
              }))} /></div>
          {fitOpts.length > 0 && (
            <div className="field-row"><label className="lbl">핏</label>
              <Chips options={fitOpts} value={a.fit} onChange={(v) => onChange(withFitProfile({ fit: v }, 'seller'))} /></div>
          )}
        </div>
      </div>

      {/* 2. materials */}
      <div className="surface">
        <div className="sec-head"><div><div className="sec-title">소재</div><div className="sec-sub">혼용률을 입력해주세요. 합계 100%를 권장해요.</div></div></div>
        <div className="material-chips">
            {a.materials.map((m, i) => (
              editMatIdx === i ? (
                <span className="mat-chip draft editing" key={i}
                  onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) { setEditMatIdx(null); if (!(m.name || '').trim() && !m.ratio) onChange({ materials: a.materials.filter((_, j) => j !== i) }); } }}>
                  <input className="mc-name" autoFocus value={m.name}
                    onChange={(e) => setMat(i, { name: e.target.value })}
                    onKeyDown={(e) => { if (e.key === 'Enter') setEditMatIdx(null); }} />
                  <span className="mc-div" />
                  <input className="mc-ratio" type="number" inputMode="numeric" min="0" max="100" value={m.ratio || ''}
                    onChange={(e) => setMat(i, { ratio: Number(e.target.value.replace(/[^0-9]/g, '').slice(0, 3)) || 0 })}
                    onKeyDown={(e) => { if (e.key === 'Enter') setEditMatIdx(null); }} /><span className="mc-pct">%</span>
                  <button className="mc-x" onMouseDown={(e) => e.preventDefault()} onClick={() => { setEditMatIdx(null); onChange({ materials: a.materials.filter((_, j) => j !== i) }); }}><Icon name="x" size={12} /></button>
                </span>
              ) : (
                <span className="mat-chip done" key={i} role="button" tabIndex={0} title="클릭해서 수정"
                  onClick={() => setEditMatIdx(i)} onKeyDown={(e) => { if (e.key === 'Enter') setEditMatIdx(i); }}>
                  <span className="mc-text">{m.name || '소재'}</span>
                  <span className="mc-div" />
                  <span className="mc-val">{m.ratio || 0}%</span>
                  <button className="mc-x" onClick={(e) => { e.stopPropagation(); onChange({ materials: a.materials.filter((_, j) => j !== i) }); }}><Icon name="x" size={12} /></button>
                </span>
              )
            ))}
            <button className="mat-add" onClick={() => { onChange({ materials: [...a.materials, { name: '', ratio: 0 }] }); setEditMatIdx(a.materials.length); }}>
              <Icon name="plus" size={14} />소재 추가
            </button>
          </div>
          {matOver && <p className="mat-warn"><Icon name="alertTri" size={14} />혼용률 합계가 100%를 넘었어요 (현재 {matTotal}%). 다시 확인해주세요.</p>}
      </div>

      {/* 3. measurements */}
      <div className="surface">
        <div style={{ marginBottom: 16 }}>
          <div className="sec-title">실측 정보</div>
          <div className="sec-sub">실측정보를 입력하면 상품의 사실성이 더욱 향상돼요. · {typeLabel}</div>
        </div>
        {!a.measurementsUnknown && (
          <div className="measure-grid">
            {visibleMeasurements.map((m) => (
              <div className="measure-cell" key={m.key}>
                <label className="lbl" style={{ fontWeight: 400, color: 'var(--fg-2)', fontSize: 12.5 }}>{(catalogs.measurementLabels || {})[m.key] || m.key}</label>
                <div className="mfield"><input type="text" inputMode="decimal" pattern="[0-9]*[.]?[0-9]?" placeholder="0" value={measurementInputValue(m)}
                  onKeyDown={(e) => { if (['e', 'E', '+', '-'].includes(e.key)) e.preventDefault(); }}
                  onChange={(e) => editMeasurement(m.key, e.target.value)}
                  onBlur={(e) => commitMeasurement(m.key, e.target.value)} /><span className="u">cm</span></div>
              </div>
            ))}
          </div>
        )}
        <label className={`check-row${a.measurementsUnknown ? ' on' : ''}`} style={{ marginTop: a.measurementsUnknown ? 0 : 16 }}>
          <input type="checkbox" checked={a.measurementsUnknown} onChange={(e) => onChange({ measurementsUnknown: e.target.checked })} />
          <span className="check-box"><Icon name="check" size={12} /></span>
          실측 모름
        </label>
      </div>

      {/* 4. selling points — chips */}
      <div className="surface">
        <div className="sec-head"><div><div className="sec-title">강조하고 싶은 특징</div>
          <div className="sec-sub">상세페이지에서 가장 강조될 핵심 포인트예요. 최대 {SELLING_POINTS_MAX}개까지 넣을 수 있어요.</div></div>
          <span className="pill pill-soft">{a.sellingPoints.length}/{SELLING_POINTS_MAX}개</span></div>
        <div className="sp-chipwrap">
          {/* 칩을 누르면 그 자리에서 문구를 고친다. Enter 확정(비우고 Enter = 삭제), Esc 취소,
              포커스 이탈은 고친 문구만 저장. 입력 폭은 .sp-draft-fit 이 글자 폭 그대로 잡는다 —
              글자 수 추정으로는 영문 대문자·이모지·전각문자에서 앞글자가 밀렸다(실측). */}
          {a.sellingPoints.map((p, i) => (
            spEditIdx === i ? (
              <span className="sp-chip draft" key={i}>
                {/* 비워도 폭은 원래 문구만큼 유지 — 편집 중 칩이 쪼그라들며 옆 칩들이 밀리지 않게 */}
                <span className="sp-draft-fit" data-value={spEditDraft || p}>
                  <input className="sp-draft-input" autoFocus value={spEditDraft} maxLength={40} size={1}
                    aria-label="특징 문구 수정"
                    onChange={(e) => setSpEditDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (isComposing(e)) return;
                      if (e.key === 'Enter') commitSpEdit(true); else if (e.key === 'Escape') cancelEditSp();
                    }}
                    /* 다른 칩을 눌러 옮겨간 blur 는 그쪽 mousedown 이 이미 커밋했다 — 여기서 또
                       처리하면 갓 열린 편집창을 닫아버린다. 내 편집이 아직 열려 있을 때만 커밋. */
                    onBlur={() => { if (spEditIdxRef.current === i) commitSpEdit(false); }} />
                </span>
              </span>
            ) : (
              <span className={`sp-chip editable${aiSet.has(p) ? ' ai' : ''}`} key={i}
                /* click 이 아니라 mousedown 에서 연다: blur 커밋이 먼저 일어나면 칩 폭이 바뀌어
                   레이아웃이 밀리고, mouseup 이 다른 요소에서 끝나 click 자체가 사라진다(실측). */
                onMouseDown={(e) => {
                  // preventDefault 필수: mousedown 의 기본 동작이 이 span(tabIndex=0)을 포커스하는데,
                  // 그 사이 span 은 입력창으로 교체돼 포커스가 body 로 떨어지고 → 갓 열린 입력창이
                  // blur 되어 즉시 닫힌다(실측: JS 이벤트로는 재현 안 되고 실제 클릭에서만 발생).
                  e.preventDefault();
                  if (spEditIdxRef.current !== null && spEditIdxRef.current !== i) commitSpEdit(false);
                  startEditSp(i);
                }}
                title="눌러서 수정"
                role="button" tabIndex={0}
                onKeyDown={(e) => {
                  // 안쪽 삭제 버튼의 Enter/Space 가 여기까지 올라오면 삭제 대신 편집이 열린다.
                  if (e.target !== e.currentTarget) return;
                  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); startEditSp(i); }
                }}>
                {aiSet.has(p) && <span className="sp-ai-tag">AI 제안</span>}
                {p}
                <button className="sp-chip-x" aria-label={`${p} 삭제`}
                  onPointerDown={(e) => e.stopPropagation()}
                  onMouseDown={(e) => e.stopPropagation()}
                  onClick={(e) => { e.stopPropagation(); onChange({ sellingPoints: a.sellingPoints.filter((_, j) => j !== i), aiSuggestedPoints: (a.aiSuggestedPoints || []).filter((x) => x !== p) }); }}><Icon name="x" size={12} /></button>
              </span>
            )
          ))}
          {a.sellingPoints.length < SELLING_POINTS_MAX && (
            spAdding ? (
              <span className="sp-chip draft">
                <span className="sp-draft-fit" data-value={spDraft || '특징 입력 후 Enter'}>
                  <input className="sp-draft-input" autoFocus placeholder="특징 입력 후 Enter" value={spDraft}
                    maxLength={40} size={1} aria-label="새 특징 문구"
                    onChange={(e) => setSpDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (isComposing(e)) return;
                      if (e.key === 'Enter') commitSp(); else if (e.key === 'Escape') { setSpAdding(false); setSpDraft(''); }
                    }}
                    onBlur={commitSp} />
                </span>
              </span>
            ) : (
              <button className="sp-add" onClick={() => setSpAdding(true)}><Icon name="plus" size={14} />추가하기</button>
            )
          )}
        </div>
      </div>

      {/* 5. model select — AI(가상) 모델 / 실제(FaceMarket 라이선스) 모델 탭 구분 (2026-07-21) */}
      <div className="surface">
        <div className="sec-title" style={{ marginBottom: 6 }}>모델 선택</div>
        <Chips className="model-tabs" options={[{ value: 'ai', label: 'AI 모델' }, { value: 'real', label: '실제 모델' }]}
          value={modelTab} onChange={(v) => v && setModelTab(v)} />
        <div className="sec-sub" style={{ margin: '10px 0 16px' }}>
          {modelTab === 'ai'
            ? '가상 인물 모델이에요 · 라이선스 비용 없이 바로 쓸 수 있어요.'
            : '검증된 얼굴 라이선스 모델이에요 · 라이선스가 활성인 모델만 선택할 수 있어요.'}
        </div>
        {modelTab === 'ai' ? (
          /* 성별 칩과 같은 성별만 노출(2026-08-01 사용자 결정) — 칩 미선택이면 전체.
             이름은 사진 위 우측 하단에 흰 글씨로 얹는다(별도 메타 줄 없음). */
          <div className="model-grid">
            {AI_MODELS
              .filter((m) => !a.targetGenders?.[0] || m.gender === a.targetGenders[0])
              .map((m) => {
                const on = a.selectedModelId === m.id;
                return (
                  <div key={m.id} className={`model-card fm-model ai-model${on ? ' on' : ''}`}
                    onClick={() => onChange({ selectedModelId: m.id })} title={m.displayName}>
                    <img src={m.thumb} alt={m.displayName} />
                    <span className="ai-name">
                      {m.displayName}{on && <Icon name="check" size={12} />}
                    </span>
                  </div>
                );
              })}
          </div>
        ) : authLoading || modelsLoading ? (
          <div className="hint">검증 모델을 불러오는 중이에요…</div>
        ) : !session ? (
          <div className="hint">로그인하면 실제 모델을 선택할 수 있어요</div>
        ) : modelsError ? (
          <div className="fm-model-state">
            <div className="hint">{modelsError}</div>
            <Button variant="quiet" size="sm" onClick={() => setModelsAttempt((attempt) => attempt + 1)}>다시 시도</Button>
          </div>
        ) : models.length === 0 ? (
          <div className="hint">아직 등록된 검증 모델이 없어요.</div>
        ) : (
          <>
            <div className="model-grid">
            {selectableModels.map((m) => {
              const on = a.selectedModelId === m.id;
              return (
                <div key={m.id}
                  className={`model-card fm-model${on ? ' on' : ''}`}
                  onClick={() => setDetailFor(m)}
                  title="눌러서 상세 정보 보기">
                  {m.coverImageUrl
                    ? <img src={m.coverImageUrl} alt={m.displayName} />
                    : <ModelThumb uri={m.faceThumbUri} alt={m.displayName} />}
                  {m.status === 'verified' && <span className="fm-verified"><Icon name="check" size={11} />검증</span>}
                  <div className="fm-meta">
                    <div className="fm-name">{m.displayName}{on && <Icon name="check" size={13} className="star" />}</div>
                    <div className="fm-price">
                      {m.unitPrice != null
                        ? `₩${Number(m.unitPrice).toLocaleString('ko-KR')} · 상세페이지 1개당`
                        : '상세페이지 1개당 가격 확인 필요'}
                    </div>
                  </div>
                </div>
              );
            })}
            </div>
            {pendingLicenseModels.length > 0 && (
              <details className="fm-license-pending">
                <summary>라이선스 준비 중 <span>{pendingLicenseModels.length}</span><Icon name="chevDown" size={15} /></summary>
                <div className="model-grid">
                  {pendingLicenseModels.map((m) => (
                    <div key={m.id} className="model-card fm-model disabled"
                      onClick={() => setDetailFor(m)} title="눌러서 상세 정보 보기">
                      {m.coverImageUrl
                        ? <img src={m.coverImageUrl} alt={m.displayName} />
                        : <ModelThumb uri={m.faceThumbUri} alt={m.displayName} />}
                      {m.status === 'verified' && <span className="fm-verified"><Icon name="check" size={11} />검증</span>}
                      <div className="fm-meta">
                        <div className="fm-name">{m.displayName}</div>
                        <div className="fm-price">라이선스 없음</div>
                      </div>
                    </div>
                  ))}
                </div>
              </details>
            )}
          </>
        )}
        {detailFor && (
          <ModelDetailModal
            model={detailFor}
            selectable={!!detailFor.hasActiveLicense}
            onSelect={(id) => onChange({ selectedModelId: id })}
            onClose={() => setDetailFor(null)}
          />
        )}
      </div>

      {/* 6. match clothing — full width */}
      {expectedMatchingType(a.clothingType) && <div className="surface">
        <div className="sec-title" style={{ marginBottom: 6 }}>매칭 의류</div>
        <div className="sec-sub" style={{ marginBottom: 16 }}>핏·코디 이미지 생성에 쓰여요 · 1개 선택</div>
        <div className="model-grid custom-match-grid">
          {a.matchClothing.map((m) => {
            const compatible = isMatchCompatible(m);
            return (
              <div key={m.id}
                className={`model-card custom-match-card${m.selected ? ' on' : ''}${compatible ? '' : ' incompatible'}`}
                onClick={() => toggleMatch(m.id)}>
                {m.cutoutStatus === 'processing' ? (
                  <div className="custom-match-cutout-pending">
                    <Icon name="loader" className="spin" size={16} />
                    <span>이미지 업로드됐어요! 지금 배경 정리 중이에요</span>
                  </div>
                ) : (
                  <img src={m.thumb} alt={m.name} />
                )}
                {m.isCustom && <span className="custom-match-badge">내 옷</span>}
                {m.isCustom && (
                  <button className="custom-match-delete" disabled={customMatchDeleting}
                    aria-label="내 옷 삭제"
                    onClick={(event) => { event.stopPropagation(); removeCustomMatch(); }}>
                    <Icon name={customMatchDeleting ? 'loader' : 'x'}
                      className={customMatchDeleting ? 'spin' : ''} size={14} />
                  </button>
                )}
                {m.id === mainMatchId && <span className="match-role main">선택</span>}
                <div className="nm">
                  <span>{m.name}{!compatible && <small>현재 상품과 종류가 맞지 않아요</small>}</span>
                  {m.selected && <Icon name="check" size={13} className="star" />}
                </div>
              </div>
            );
          })}
          {!a.matchClothing.some((item) => item.isCustom) && (
            <button className="model-card custom-match-add"
              onClick={(event) => {
                setCustomMatchAnchor(event.currentTarget.getBoundingClientRect());
                setCustomMatchOpen(true);
              }}>
              <span className="custom-match-add-art">
                <Icon name={expectedMatchingType(a.clothingType) === 'top' ? 'shirt' : 'pants'}
                  size={52} stroke={1.4} />
              </span>
              <span className="nm">의류 추가하기</span>
            </button>
          )}
        </div>
        {customMatchOpen && (
          <CustomMatchUploadModal
            projectId={projectId}
            anchorRect={customMatchAnchor}
            onApply={applyAnalysisReplacement}
            onClose={closeCustomMatchModal}
          />
        )}
      </div>}
    </>
  );

  const cta = (
    <div className={`af-cta-split${composeModeOpen ? ' open' : ''}`}>
      <div className="af-compose-menu" ref={composeModeMenuRef}>
        {/* 저장 중엔 native disabled 가 아니라 aria-disabled — disabled 는 방금 복귀시킨
            포커스를 body 로 뱉어내 키보드 사용자가 자리를 잃는다(리뷰 P2 후속 실측). */}
        <button type="button" className="af-compose-trigger" ref={composeModeTriggerRef}
          aria-haspopup="listbox" aria-expanded={composeModeOpen}
          aria-controls="analysis-compose-mode-listbox"
          aria-disabled={composeModeSaving || confirming}
          onClick={() => {
            if (composeModeSaving || confirming) return;
            setComposeModeOpen((open) => !open);
          }}>
          <span className="af-compose-trigger-copy">
            <small>사진 양</small>
            <b>{selectedComposeModeLabel}</b>
          </span>
          <Icon name="chevUp" size={12} className="af-compose-chevron" />
        </button>
        <div id="analysis-compose-mode-listbox" className="af-compose-popover"
          role="listbox" aria-label="상세페이지 사진 양" aria-hidden={!composeModeOpen}>
          {composeModes.map((mode) => (
            <button type="button" role="option" aria-selected={composeMode === mode.value}
              tabIndex={composeModeOpen && composeMode === mode.value ? 0 : -1}
              className={`af-compose-option${composeMode === mode.value ? ' on' : ''}`}
              disabled={composeModeSaving || confirming}
              key={mode.value} onClick={() => {
                setComposeModeOpen(false);
                composeModeTriggerRef.current?.focus();
                changeComposeMode(mode.value);
              }}>
              <span>
                <b>{mode.label}</b>
                <small>{mode.desc} · {mode.count}컷</small>
              </span>
              <span className="af-compose-tick" aria-hidden="true">
                <Icon name="check" size={10} stroke={2.5} />
              </span>
            </button>
          ))}
        </div>
      </div>
      <Button variant="primary" size="lg" iconRight="arrowRight"
        className="af-cta-confirm"
        disabled={composeModeSaving || confirming}
        onClick={confirmAnalysis}>의류정보 확정 완료 · {CREDIT_COSTS.mannequinGenerate} 크레딧</Button>
    </div>
  );

  if (inline) {
    return (
      <>
        <div className="af-inline-head"><div><div className="af-head-title">AI가 분석한 정보예요</div><div className="hint" style={{ marginTop: 2 }}>틀린 부분이 있으면 직접 수정해주세요.</div></div></div>
        <div className="af-body af-cards merged-card">{sections}</div>
        <WizardCTA className="wizard-cta-analysis">{cta}</WizardCTA>
      </>
    );
  }

  return (
    <div className="wizard">
      <PageHead title="AI가 상품 정보를 분석했어요" sub="틀린 부분이 있으면 직접 수정해주세요." />
      <div className="af-body af-cards merged-card">{sections}</div>
      <WizardCTA className="wizard-cta-analysis">{cta}</WizardCTA>
    </div>
  );
}

export function Analysis({ onNext }) {
  const [phase, setPhase] = useState('loading');
  const [analysis, setAnalysis] = useState(null);
  const [catalogs, setCatalogs] = useState(null);

  const run = useCallback(() => {
    setPhase('loading');
    // 시그니처 통일 — analyzeProduct 는 projectId 를 받는다(http 모드 job 시작 대상). mock 은 무시.
    Promise.all([api.analyzeProduct(useAppStore.getState().projectId, {}), api.getCatalogs()])
      .then(([a, c]) => { setAnalysis(a); setCatalogs(c); setPhase('ready'); })
      .catch(() => setPhase('error'));
  }, []);
  useEffect(() => { run(); }, [run]);

  if (phase === 'loading') return <div className="wizard"><PageHead title="AI가 상품 정보를 분석했어요" sub="틀린 부분이 있으면 직접 수정해주세요." /><AnalysisSkeleton /></div>;
  if (phase === 'error') return <div className="wizard narrow"><div className="surface"><ErrorState desc="분석 서버에 일시적인 문제가 발생했어요." onRetry={run} /></div></div>;

  return <AnalysisForm analysis={analysis} catalogs={catalogs}
    projectId={useAppStore.getState().projectId}
    onAnalysisReplace={setAnalysis}
    onChange={(patch) => {
      const refreshMatch = isMatchRecommendationPatch(patch);
      if (isGenerationRelevantAnalysisPatch(patch)) {
        useAppStore.getState().markGenerationRelevantEdits();
      }
      setAnalysis((a) => ({ ...a, ...patch }));
      api.saveAnalysis(null, patch).then((saved) => {
        if (refreshMatch) setAnalysis((a) => ({ ...a, matchClothing: saved.matchClothing }));
      });
    }} onNext={onNext} />;
}
