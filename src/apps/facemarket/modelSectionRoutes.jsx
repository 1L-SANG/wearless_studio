/* =============================================================
   모델 섹션(/model/*) 라우트와 그 소유 가드.

   **facemarket 앱만 이 파일을 import 한다.** 셀러(ai) 번들에 모델 등록·라이선스 화면이
   실리지 않게 하는 경계가 여기다 — App.jsx 에서 이 파일을 import 하는 순간 ModelRegister
   (생체정보 동의·신분증·사진 업로드)와 그 의존이 통째로 셀러 번들로 돌아온다.

   ai 도메인에서 /model/* 은 애초에 도달할 수 없다: host.js 의 domainRouteRedirect 가
   라우터보다 먼저 /create/input 으로 돌린다(#214). 그래서 셀러 쪽에 이 서브트리를 등록해
   두던 코드는 도달 불가능한 죽은 가지였고, 번들만 무겁게 했다.

   본인확인·라이선스(FM-10)와 개인화(사용자 얼굴·신체)가 한 섹션이다. 본인확인(성인 인증,
   T2-1)은 register 하나로 흡수됐다 — FaceMarket 실명 인증 1회가 개인화 성인 확인도 함께
   기록하므로 별도 identity 라우트가 없다. /model 은 섹션 허브(체크리스트)이고
   register·license 의 URL 은 종전 그대로다.
   ============================================================= */
import { useEffect, useState } from 'react';
import { Navigate, Outlet, Route } from 'react-router-dom';
import { ErrorState } from '@/components/ui.jsx';
import {
  getApplicationConfig, getCurrentApplication, getCurrentEnrollment, listMyModels,
} from '@/lib/api/facemarket.js';
import { ModelApply } from '@/features/model/ModelApply.jsx';
import { ModelRegister } from '@/features/model/ModelRegister.jsx';
import { ModelLicense } from '@/features/model/ModelLicense.jsx';
import { ModelGenerate } from '@/features/model/ModelGenerate.jsx';
import { ModelWithdraw } from '@/features/model/ModelWithdraw.jsx';

/* 모델 섹션 보호 — 등록 중 모델은 허브·라이선스에 접근할 수 있지만 생성은 verified만 허용한다. */
function RequireModel({ verifiedOnly = false }) {
  const [phase, setPhase] = useState('loading'); // loading | allowed | denied | error
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let alive = true;
    setPhase('loading');
    listMyModels()
      .then((models) => {
        if (!alive) return;
        const allowed = verifiedOnly
          ? models.some((model) => model.status === 'verified')
          : models.length > 0;
        setPhase(allowed ? 'allowed' : 'denied');
      })
      .catch(() => {
        if (alive) setPhase('error');
      });
    return () => { alive = false; };
  }, [attempt, verifiedOnly]);

  if (phase === 'loading') return <div className="route-loading">본인확인 상태를 확인하고 있어요…</div>;
  if (phase === 'denied') return <Navigate to="/model/register" replace />;
  if (phase === 'error') {
    return (
      <div className="wizard narrow">
        <div className="surface">
          <ErrorState desc="본인확인 상태를 불러오지 못했어요." onRetry={() => setAttempt((value) => value + 1)} />
        </div>
      </div>
    );
  }
  return <Outlet />;
}

async function loadOptional(fn) {
  try { return await fn(); }
  catch (e) { if (e?.status === 404) return null; throw e; }
}

/* 지원서 게이트(리뉴얼, 스펙 12). FM_APPLICATION_REQUIRED 가 켜져 있으면 승인된 지원서가
   없는 신규 사용자는 등록 화면(/model/register)에 들어갈 수 없다 — "검증된 사람만 등록".
   백엔드 create_enrollment 의 403 과 같은 규칙을 UI 에서 먼저 적용해, 동의 폼을 보여줬다가
   막는 일이 없게 한다. 기존 검증 모델(verified/reverification_required)·진행 중 등록 보유자는
   grandfathered(백엔드 E6 과 동일 조건). 거부되면 허브로 — 허브가 "지원 시작하기"를 안내한다. */
function RequireApprovedApplication() {
  const [phase, setPhase] = useState('loading'); // loading | allowed | denied | error
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let alive = true;
    setPhase('loading');
    (async () => {
      try {
        const [cfg, app, models, enrollment] = await Promise.all([
          loadOptional(getApplicationConfig),
          loadOptional(getCurrentApplication),
          listMyModels(),
          loadOptional(getCurrentEnrollment),
        ]);
        if (!alive) return;
        const required = !!cfg?.applicationRequired;
        const legacyExempt = (models || []).some((m) => ['verified', 'reverification_required'].includes(m.status));
        const inProgress = !!enrollment;
        const approved = app?.status === 'approved';
        setPhase(!required || legacyExempt || inProgress || approved ? 'allowed' : 'denied');
      } catch {
        if (alive) setPhase('error');
      }
    })();
    return () => { alive = false; };
  }, [attempt]);

  if (phase === 'loading') return <div className="route-loading">지원 상태를 확인하고 있어요…</div>;
  if (phase === 'denied') return <Navigate to="/status" replace />;
  if (phase === 'error') {
    return (
      <div className="wizard narrow">
        <div className="surface">
          <ErrorState desc="지원 상태를 불러오지 못했어요." onRetry={() => setAttempt((value) => value + 1)} />
        </div>
      </div>
    );
  }
  return <Outlet />;
}

function RequireOwnedModel() {
  return <RequireModel />;
}

function RequireVerifiedModel() {
  return <RequireModel verifiedOnly />;
}

export const MODEL_SECTION_ROUTES = (
  <Route path="model">
    {/* 지원서(리뉴얼)·등록은 모델 생성 전에도 연다. 그래서 apply·register 는 RequireOwnedModel
        밖에 둔다. 허브(index)는 2026-09-02 지시로 랜딩 상단바의 '등록 상태'(/status, StatusPage)로
        옮겼다 — /model 로 오는 옛 링크·복귀 경로는 전부 거기로 넘긴다. */}
    <Route path="apply" element={<ModelApply />} />
    {/* 등록 화면은 승인된 지원서(또는 기존 모델·진행 중 등록)가 있어야 열린다 — 검증된 사람만. */}
    <Route element={<RequireApprovedApplication />}>
      <Route path="register" element={<ModelRegister />} />
    </Route>
    <Route index element={<Navigate to="/status" replace />} />
    <Route element={<RequireOwnedModel />}>
      <Route path="license" element={<ModelLicense />} />
      {/* 폐기된 직접 업로드 북마크는 신규 등록 경계로 되돌린다. */}
      <Route path="consent" element={<Navigate to="/model/register" replace />} />
      <Route path="face" element={<Navigate to="/model/register" replace />} />
      <Route path="body" element={<Navigate to="/model/register" replace />} />
      <Route path="generate" element={<RequireVerifiedModel />}>
        <Route index element={<ModelGenerate />} />
      </Route>
      <Route path="withdraw" element={<ModelWithdraw />} />
      {/* 알 수 없는 /model/* 경로도 가드를 거친 뒤 등록 상태로만 복귀한다. */}
      <Route path="*" element={<Navigate to="/status" replace />} />
    </Route>
  </Route>
);
