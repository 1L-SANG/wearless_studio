/* FaceMarket 모델 등록 상태와 다음 안전한 진입점만 보여주는 허브.

   2026-09-02 지시로 /model 이 아니라 랜딩 상단바의 '등록 상태'(/status, StatusPage)에 실린다.
   LandingShell 안이라 좌우 여백은 셸이 주고(.hubPage 는 가로 패딩 0), 상단바·푸터도 셸 것이다.
   /model 은 여기로 리다이렉트한다.

   외형은 facemarket 랜딩(FacemarketLanding.module.css)의 디자인 언어를 따른다 —
   eyebrow + 큰 제목 + 리드문, 얇은 선 카드, 잉크색 pill CTA. 조회 함수·상태 라벨·
   네비게이션 목적지는 종전 그대로다. 바뀐 건 배치와 스타일뿐이다.

   재배치 원칙 두 가지:
   1) 다음에 할 일이 맨 위다. 이 화면에 온 사람의 질문은 "지금 뭘 해야 하나"이고,
      상태 배지는 그 답의 근거지 답이 아니다. 그래서 CTA 패널이 먼저, 상태 카드가 뒤다.
   2) 예전 화면의 정보는 하나도 버리지 않았다. 상태 배너 두 줄(`모델 · …`·`등록 · …`)은
      상태 카드의 제목+문장으로 옮겼고, 버튼 세 개와 데이터 삭제 링크도 그대로 있다.

   ⚠️ 이 컴포넌트는 facemarket 도메인 전용이 아니다. App.jsx 의 MODEL_SECTION_ROUTES 가
   ai.wearless.kr(셀러)에서는 ChromeLayout 아래에 붙는다 — 거기엔 `.fm-theme` 이 없어서
   `--fm-*` 토큰이 정의되지 않는다. 그래서 CSS 쪽 모든 토큰에 앱 기본값 폴백을 달아 뒀다
   (ModelPersonalization.module.css 의 ModelHub 블록 주석 참고). */
import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Button, ErrorState, Icon, useToast } from '@/components/ui.jsx';
import {
  cancelApplication, getApplicationConfig, getCurrentApplication, getCurrentEnrollment,
  listMyModels,
} from '@/lib/api/facemarket.js';
import s from './ModelPersonalization.module.css';

async function loadOptional(fn) {
  try { return await fn(); }
  catch (e) { if (e?.status === 404) return null; throw e; }
}

const MODEL_STATUS_LABEL = {
  pending: '본인 확인 진행 중',
  reverification_required: '재검증 필요',
  verified: '검증 완료',
};

const ENROLLMENT_STATUS_LABEL = {
  consent_pending: '동의 대기',
  identity_pending: '신분증 확인 대기',
  photos_pending: '사진 등록 대기',
  liveness_pending: '라이브 얼굴 확인 대기',
  processing: '얼굴 확인 처리 중',
  asset_building: '모델 준비 중',
  license_pending: '라이선스 조건 입력 대기',
  vc_pending: 'VC 발급 대기',
  passed: '등록을 마쳤어요',
  failed: '등록을 마치지 못했어요',
  cancelled: '등록을 중단했어요',
  expired: '등록 유효기간이 지났어요',
};

/* 모르는 상태 코드를 화면에 그대로 흘리지 않는다. DESIGN.md:309 "개발자 언어를 화면에
   노출하지 않는다" — 종전 폴백이 `|| enrollment.status` 라 매핑에 없는 값이 그대로
   떴다(실제로 'passed' 가 카드에 영문 그대로 나오는 걸 브라우저에서 확인했다).
   서버가 상태를 하나 추가하면 이 맵보다 먼저 배포되므로, 폴백은 반드시 있어야 하고
   그 폴백이 사람 말이어야 한다. */
function statusLabel(map, status, fallback) {
  return map[status] || fallback;
}

/* 상태를 색만으로 알리지 않는다(WCAG 1.4.1) — 아이콘 모양(체크/시계/경고/눈/선)과
   칩 라벨(완료·진행 중·…)과 아래 문장이 같은 뜻을 세 번 말한다. 색각 이상이나
   흑백 출력에서도 세 칸이 각각 어느 상태인지 형태로 구분된다. */
const STATE_VIEW = {
  done: { icon: 'check', chip: '완료', mark: 'hubMarkDone', chipClass: 'hubChipDone' },
  active: { icon: 'clock', chip: '진행 중', mark: 'hubMarkActive', chipClass: null },
  attention: { icon: 'alertTri', chip: '확인 필요', mark: 'hubMarkAttention', chipClass: 'hubChipAttention' },
  open: { icon: 'eye', chip: '확인 가능', mark: 'hubMarkActive', chipClass: null },
  todo: { icon: 'minus', chip: '아직', mark: 'hubMarkTodo', chipClass: null },
};

/* 아래 세 함수는 **표시용 파생**이다. 새로 조회하는 값이 없고, 위 라벨 맵과
   ownedModel·enrollment 두 값만 읽는다. 상태 머신을 옮겨 쓰지 않는다. */

// 1칸 — 모델 등록(enrollment). 진행 중 등록이 있으면 그 라벨이 곧 현재 위치다.
function registrationCell(ownedModel, enrollment) {
  if (enrollment) {
    return { state: 'active', text: statusLabel(ENROLLMENT_STATUS_LABEL, enrollment.status, '등록을 진행하고 있어요') };
  }
  // 모델이 verified 면 등록은 passed 로 끝난 것이다(PRD §7.2 — 라이선스 활성화와
  // 모델 verified, 등록 passed 가 같은 트랜잭션이다).
  if (ownedModel?.status === 'verified') return { state: 'done', text: '등록을 마쳤어요' };
  // getCurrentEnrollment 가 404 를 준 상태다 — "없다"까지만 말하고 이유는 짐작하지 않는다.
  if (ownedModel) return { state: 'todo', text: '진행 중인 등록이 없어요' };
  return { state: 'todo', text: '아직 시작하지 않았어요' };
}

// 2칸 — 내 모델. 모르는 코드도 사람 말로 떨어뜨린다(statusLabel 주석 참고).
function modelCell(ownedModel) {
  if (!ownedModel) return { state: 'todo', text: '아직 없어요' };
  const text = statusLabel(MODEL_STATUS_LABEL, ownedModel.status, '상태를 확인하고 있어요');
  if (ownedModel.status === 'verified') return { state: 'done', text };
  if (ownedModel.status === 'reverification_required') return { state: 'attention', text };
  return { state: 'active', text };
}

// 3칸 — 얼굴 라이선스. 이 화면은 라이선스를 **조회하지 않는다**. 그래서 "발급됨" 같은
// 단정 대신 지금 알 수 있는 것만 적는다: 조건 입력 단계인지, 확인하러 갈 수 있는지.
function licenseCell(ownedModel, enrollment, needsTerms) {
  if (needsTerms) {
    return { state: 'active', text: statusLabel(ENROLLMENT_STATUS_LABEL, enrollment.status, '등록을 진행하고 있어요') };
  }
  if (ownedModel?.status === 'verified' && !enrollment) {
    return { state: 'open', text: '조건과 QR 을 확인할 수 있어요' };
  }
  return { state: 'todo', text: '등록을 마치면 조건을 정해요' };
}

function HubHead() {
  return (
    <header className={s.hubHead}>
      <p className={s.hubEyebrow}>FaceMarket 모델</p>
      <h1 className={s.hubTitle}>등록 상태</h1>
      {/* 순서에서 '모바일 신분증'만 앞으로 옮겼다. 예전 문장은 신분증을 라이브 얼굴 뒤에
          뒀는데 실제 위저드는 STEP 2 가 신분증, STEP 6 이 라이브다(PRD §5 / 랜딩
          RegisterSection 의 레일 '동의·신분증·사진·체형·대표·라이브·완료' — 2026-09-01
          ModelRegister FLOW_STEPS 와 대조 확인). 같은 사이트의 두 화면이 서로 다른
          순서를 말하고 있었다. 나머지 표현은 그대로다. */}
      <p className={s.hubLead}>
        동의 → 모바일 신분증 → 정면·45도·측면 사진 → 라이브 얼굴 → 라이선스 순서로 안전하게 등록해요.
      </p>
    </header>
  );
}

function StatusCard({ cell, index, name }) {
  const view = STATE_VIEW[cell.state];
  return (
    <li className={s.hubCard}>
      <div className={s.hubCardTop}>
        <span aria-hidden="true" className={`${s.hubMark} ${s[view.mark]}`}>
          <Icon name={view.icon} size={16} stroke={2} />
        </span>
        <span className={s.hubCardName}>{name}</span>
        {/* 큰 세리프 숫자는 랜딩 .galleryIndexNow 의 결이다. Cormorant 는 라틴 전용이라
            숫자에만 쓴다(한글 제목에 쓰면 폴백 서체로 떨어진다 — 랜딩 CSS 주석 참고). */}
        <span aria-hidden="true" className={s.hubCardIndex}>{index}</span>
      </div>
      <span className={`${s.hubChip}${view.chipClass ? ` ${s[view.chipClass]}` : ''}`}>{view.chip}</span>
      <p className={s.hubCardText}>{cell.text}</p>
    </li>
  );
}

export function ModelHub() {
  const navigate = useNavigate();
  const { push } = useToast();
  const [phase, setPhase] = useState('loading'); // loading|ready|error
  const [ownedModel, setOwnedModel] = useState(null);
  const [enrollment, setEnrollment] = useState(null);
  const [application, setApplication] = useState(null);
  const [applicationRequired, setApplicationRequired] = useState(false);

  const load = useCallback(async () => {
    setPhase('loading');
    try {
      // 지원서·설정·등록을 함께 조회한다(404 는 "없음"으로 흡수, loadOptional).
      const [mine, cfg, app, enr] = await Promise.all([
        listMyModels(),
        loadOptional(getApplicationConfig),
        loadOptional(getCurrentApplication),
        loadOptional(getCurrentEnrollment),
      ]);
      setOwnedModel(mine?.[0] || null);
      setApplicationRequired(!!cfg?.applicationRequired);
      setApplication(app);
      setEnrollment(enr);
      setPhase('ready');
    } catch (e) {
      push?.(e.message, { icon: 'alertCircle' });
      setPhase('error');
    }
  }, [push]);

  useEffect(() => { load(); }, [load]);

  const onCancelApplication = useCallback(async () => {
    if (!application) return;
    try {
      await cancelApplication(application.id);
      push?.('지원을 취소했어요.', { icon: 'check' });
      load();
    } catch (e) { push?.(e.message, { icon: 'alertCircle' }); }
  }, [application, load, push]);

  /* 로딩·오류에서도 머리글을 유지한다. 예전에는 세 화면이 서로 다른 껍데기라
     불러오기가 끝나는 순간 제목이 튀어나왔다. */
  if (phase === 'loading') {
    return (
      <div className={s.hubPage}>
        <HubHead />
        <p className={s.hubLoading}>불러오는 중…</p>
      </div>
    );
  }
  if (phase === 'error') {
    return (
      <div className={s.hubPage}>
        <HubHead />
        <div className={s.hubNext}>
          <ErrorState desc="상태를 불러오지 못했어요." onRetry={load} />
        </div>
      </div>
    );
  }

  const enrollmentNeedsTerms = ['license_pending', 'vc_pending'].includes(enrollment?.status);
  const registrationPath = enrollmentNeedsTerms
    ? `/model/license?step=terms&enrollment=${encodeURIComponent(enrollment.id)}`
    : '/model/register';

  // 진행 중 등록·검증 모델이 있으면 기존 여정이 우선(승인 지원서는 이미 소비됨).
  const hasProgress = !!(ownedModel || enrollment);
  // 지원 여정 상태 — 진행 중 등록이 없을 때만 hubNext 를 차지한다.
  const appState = !hasProgress ? application?.status : null;
  const appUnderReview = appState === 'under_review';
  const appRejected = appState === 'rejected';
  const appApprovedIdle = appState === 'approved';
  const showApplicationNext = appUnderReview || appRejected || appApprovedIdle;

  // 등록 화면에 들어갈 수 있는가 — RequireApprovedApplication(modelSectionRoutes)·서버
  // create_enrollment 게이트와 **같은 판정**이다. 어긋나면 '이어가기' 버튼이 가드에 막혀
  // 이 화면으로 되돌아오는 왕복이 생긴다(2026-09-02 리뷰에서 실제로 잡힌 결함).
  const registrationAllowed = !applicationRequired
    || !!enrollment
    || application?.status === 'approved'
    || ['pending', 'verified', 'reverification_required'].includes(ownedModel?.status);

  // "완전 신규": 진행 중 등록·모델·활성 지원서 전부 없음.
  const hasActiveApplication = application?.status === 'under_review' || application?.status === 'approved';
  const isNew = !hasProgress && !hasActiveApplication;
  // 모델은 있는데 등록이 막힌 경우(정지된 모델 등) — 지원서부터 다시 받는다.
  const blockedNeedsApply = hasProgress && !registrationAllowed && !hasActiveApplication;
  // 신규 진입 목적지: 게이트 on 이면 지원서, off 면 기존 즉시 등록.
  const newEntryPath = applicationRequired ? '/model/apply' : '/model/register';

  /* 종전 분기와 **같은 조건**이다. 예전 코드의 "생성·라이선스 버튼" 블록 조건이
     `ownedModel?.status === 'verified' && !enrollment` 였고, "이어가기" 블록 조건이
     그 여집합(`status !== 'verified' || enrollment`)이었다. 여기서 이름만 붙였다. */
  const isDone = ownedModel?.status === 'verified' && !enrollment;

  return (
    <div className={s.hubPage}>
      <HubHead />

      {/* ---- 다음에 할 일 ---------------------------------------------------
          랜딩 .record 와 같은 옅은 파랑 블록. 넓은 화면에서는 글과 버튼이 좌우로
          갈라져 1440px 에서도 가운데가 비지 않는다(CSS .hubNext 미디어쿼리). */}
      <section className={s.hubNext}>
        <div className={s.hubNextHead}>
          <p className={s.hubNextEyebrow}>다음 단계</p>

          {/* 지원 여정(진행 중 등록이 없을 때). 이 화면이 지원 상태의 진실원천이다(메일 무관). */}
          {appUnderReview && <h2 className={s.hubNextTitle}>지원서를 검토하고 있어요</h2>}
          {appRejected && <h2 className={s.hubNextTitle}>다시 지원할 수 있어요</h2>}
          {appApprovedIdle && <h2 className={s.hubNextTitle}>지원이 승인됐어요</h2>}

          {appUnderReview && (
            <p className={s.hubNextBody}>
              제출한 지원서를 관리자가 검토하고 있어요. 승인되면 이메일과 이 화면으로 알려드려요.
            </p>
          )}
          {appRejected && (
            <p className={s.hubNextBody}>
              지원이 거절됐어요.{application?.rejectReason ? ` 사유: ${application.rejectReason}` : ''} 정보를 수정해 다시 지원해 주세요.
            </p>
          )}
          {appApprovedIdle && (
            <p className={s.hubNextBody}>
              신분증 인증부터 모델 등록을 이어가 주세요. 등록이 만료·중단돼도 승인은 유지돼요.
            </p>
          )}

          {/* 기존 여정(진행 중 등록·검증 모델) — 지원 상태를 표시 중이 아닐 때만. */}
          {!showApplicationNext && isNew && (
            <h2 className={s.hubNextTitle}>
              {applicationRequired ? '모델 지원서부터 작성해요' : '생체정보 처리 동의부터 시작해요'}
            </h2>
          )}
          {!showApplicationNext && !isNew && isDone && <h2 className={s.hubNextTitle}>내 모델로 컷을 만들 수 있어요</h2>}
          {!showApplicationNext && blockedNeedsApply && (
            <h2 className={s.hubNextTitle}>모델 지원서부터 작성해요</h2>
          )}
          {!showApplicationNext && !blockedNeedsApply && !isNew && !isDone && enrollmentNeedsTerms && (
            <h2 className={s.hubNextTitle}>마지막으로 라이선스 단계가 남았어요</h2>
          )}
          {!showApplicationNext && !blockedNeedsApply && !isNew && !isDone && !enrollmentNeedsTerms && (
            <h2 className={s.hubNextTitle}>등록을 이어서 마치면 돼요</h2>
          )}

          {!showApplicationNext && isNew && (
            <p className={s.hubNextBody}>
              {applicationRequired
                ? '지원서를 제출하면 관리자 검토 후 승인된 분만 모델 등록을 진행할 수 있어요.'
                : '아직 등록된 내 모델이 없어요. 생체정보 처리 동의부터 시작해 주세요.'}
            </p>
          )}
          {!showApplicationNext && blockedNeedsApply && (
            <p className={s.hubNextBody}>
              지금 계정으로는 등록을 이어갈 수 없어요. 지원서를 제출하면 관리자 검토 후 다시 진행할 수 있어요.
            </p>
          )}
          {!showApplicationNext && !blockedNeedsApply && !isNew && !isDone && (
            <p className={s.hubNextBody}>
              본인 확인과 라이선스 발급을 마치면 내 모델로 생성할 수 있어요.
              {/* PRD §7.3·§13-2 — holder 콜드부트가 ~2분이라 대기가 정상 경로에 있다.
                  "기다리면 된다"를 말해 주지 않으면 멈춘 줄 알고 탭을 닫는다. */}
              {enrollmentNeedsTerms && ' 발급에는 몇 분이 걸릴 수 있어요 — 기다리면 됩니다.'}
            </p>
          )}
          {!showApplicationNext && isDone && (
            <p className={s.hubNextBody}>
              내 모델로 컷을 만들거나, 발급한 얼굴 라이선스의 조건과 QR 을 확인할 수 있어요.
            </p>
          )}
        </div>

        {/* CTA 는 랜딩 pill 이다 — facemarketTheme.css 가 `.fm-theme .btn-primary` 를
            잉크색 pill 로 재정의해 둬서 Button 컴포넌트를 그대로 쓰면 된다.
            block(전폭)은 뺐다. 1344px 짜리 패널에서 가로로 늘어난 버튼은 CTA 가 아니라
            구분선처럼 읽힌다. 목적지·라벨은 종전과 같다.
            '이어가기' 는 예전에 secondary 였는데, 이 자리에서는 그게 유일한 다음 행동이라
            primary 로 올렸다(모양만 바뀐다).

            처음 오는 사람의 문구는 랜딩 CTA 와 같다(registerCta.js — 게이트 on '모델 지원하기',
            off '모델 등록하기'). 같은 행동이 화면마다 다른 이름을 갖지 않게 맞춘 것이다(#218).
            아래 '이어가기'·'계속하기' 는 그대로 둔다 — 같은 버튼이 글자를 바꾸는 게 아니라
            **어느 단계로 돌아가는지**를 알려 주는 라벨이라, 통일하면 오히려 정보가 사라진다. */}
        <div className={s.hubNextActions}>
          {appUnderReview && (
            <Button variant="secondary" onClick={onCancelApplication}>지원 취소</Button>
          )}
          {appRejected && (
            <Button variant="primary" iconRight="arrowRight" onClick={() => navigate('/model/apply')}>
              다시 지원하기
            </Button>
          )}
          {appApprovedIdle && (
            <Button variant="primary" iconRight="arrowRight" onClick={() => navigate('/model/register')}>
              모델 등록 계속하기
            </Button>
          )}

          {!showApplicationNext && isNew && (
            <Button variant="primary" iconRight="arrowRight" onClick={() => navigate(newEntryPath)}>
              {applicationRequired ? '모델 지원하기' : '모델 등록하기'}
            </Button>
          )}
          {!showApplicationNext && blockedNeedsApply && (
            <Button variant="primary" iconRight="arrowRight" onClick={() => navigate('/model/apply')}>
              모델 지원하기
            </Button>
          )}
          {!showApplicationNext && !blockedNeedsApply && !isNew && !isDone && (
            <Button variant="primary" iconRight="arrowRight" onClick={() => navigate(registrationPath)}>
              {enrollmentNeedsTerms ? '라이선스 조건 설정 이어가기' : '모델 등록 이어가기'}
            </Button>
          )}
          {!showApplicationNext && isDone && (
            <>
              <Button variant="primary" iconRight="arrowRight" onClick={() => navigate('/model/generate')}>
                내 모델로 생성하기
              </Button>
              <Button variant="secondary" iconRight="chevRight" onClick={() => navigate('/model/license')}>
                얼굴 라이선스 확인
              </Button>
            </>
          )}
        </div>
      </section>

      {/* ---- 상태 세 칸 -----------------------------------------------------
          예전 배너 두 줄이 여기 들어왔다. 라이선스 칸은 조회하는 값이 없어서
          "지금 알 수 있는 것"만 적는다(licenseCell 주석). 신규 사용자에게도 세 칸을
          그린다 — 앞으로 뭘 거치는지가 이 화면의 나머지 절반이다. */}
      <section className={s.hubStatus}>
        <h2 className={s.hubStatusLabel}>단계별 현황</h2>
        <ul className={s.hubGrid}>
          <StatusCard cell={registrationCell(ownedModel, enrollment)} index="01" name="모델 등록" />
          <StatusCard cell={modelCell(ownedModel)} index="02" name="내 모델" />
          <StatusCard
            cell={licenseCell(ownedModel, enrollment, enrollmentNeedsTerms)}
            index="03"
            name="얼굴 라이선스"
          />
        </ul>
      </section>

      {/* 삭제 링크는 실제 얼굴·신체 데이터가 있는 사람에게만 — 지원서만 낸 단계에는
          지울 생체 데이터가 없다(지원서 PII 는 지원 취소로 처리). */}
      {hasProgress && (
        <div className={s.hubFoot}>
          <Link to="/model/withdraw" className={s.hubFootLink}>
            <Icon name="trash" size={14} />얼굴·신체 데이터 삭제
          </Link>
        </div>
      )}
    </div>
  );
}

export default ModelHub;
