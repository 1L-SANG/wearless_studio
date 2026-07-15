/* =============================================================
   features/model — 모델 섹션 허브 (/model)
   FaceMarket 모델 온보딩(본인확인·라이선스)과 개인화(내 얼굴·신체) 단계를
   한 섹션에서 이어 보여주는 진입점. GET /v1/personalization/status 의
   canGenerate·blockers 를 체크리스트로 보여준다(api-spec §3.4). 완료
   (canGenerate:true)면 "내 모델로 생성하기" 진입, 아니면 부족한 단계로
   바로 이동할 수 있는 링크를 보여준다.
   ============================================================= */
import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Button, ErrorState, Icon, useToast } from '@/components/ui.jsx';
import { getStatus } from '@/lib/api/personalization.js';
import s from './ModelPersonalization.module.css';

const BLOCKER = {
  identity_verification_required: 'identity_verification_required',
  // 본인확인은 마쳤는데 연령을 파생할 수 없는 상태(CX 가 생년월일 미반환 등). 재인증해도 같은
  // 결과라 /model/register 로 되돌리면 무한 왕복이 된다 → 종결 안내.
  identity_age_unavailable: 'identity_age_unavailable',
  minor_blocked: 'minor_blocked',
  consent_missing: 'consent_missing',
  photos_incomplete: 'photos_incomplete',
  body_profile_missing: 'body_profile_missing',
  purge_in_progress: 'purge_in_progress',
};

// locked: 본인확인 미완 시 동의·사진·신체 단계를 잠근다(순서 강제) — 클릭 불가한 비-Link 렌더.
function StepLink({ to, icon, title, desc, done, locked }) {
  if (locked) {
    return (
      <div className={`${s.stepLink} ${s.stepLinkLocked}`} aria-disabled="true">
        <span className={s.stepIcon}>
          <Icon name="lock" size={17} />
        </span>
        <span className={s.stepBody}>
          <div className={s.stepTitle}>{title}</div>
          <div className={s.stepDesc}>{desc}</div>
        </span>
      </div>
    );
  }
  return (
    <Link to={to} className={s.stepLink}>
      <span className={`${s.stepIcon}${done ? ' ' + s.stepIconDone : ''}`}>
        <Icon name={done ? 'check' : icon} size={17} />
      </span>
      <span className={s.stepBody}>
        <div className={s.stepTitle}>{title}</div>
        <div className={s.stepDesc}>{desc}</div>
      </span>
      <span className={s.stepArrow}><Icon name="chevRight" size={16} /></span>
    </Link>
  );
}

export function ModelHub() {
  const navigate = useNavigate();
  const { push } = useToast();
  const [phase, setPhase] = useState('loading'); // loading|ready|error
  const [status, setStatus] = useState(null);

  const load = useCallback(async () => {
    setPhase('loading');
    try {
      setStatus(await getStatus());
      setPhase('ready');
    } catch (e) {
      push?.(e.message, { icon: 'alertCircle' });
      setPhase('error');
    }
  }, [push]);

  useEffect(() => { load(); }, [load]);

  if (phase === 'loading') return <div className="wizard narrow"><div className="surface">불러오는 중…</div></div>;
  if (phase === 'error') return <div className="wizard narrow"><div className="surface"><ErrorState desc="상태를 불러오지 못했어요." onRetry={load} /></div></div>;

  const blockers = status.blockers || [];
  const has = (code) => blockers.some((b) => b.code === code);
  const purging = status.status === 'purging' || has(BLOCKER.purge_in_progress);
  // 연령 게이트(T2-1) — 본인확인이 온보딩 첫 단계. 미완/미성년이면 동의·사진·신체 진입을 막는다.
  const minorBlocked = has(BLOCKER.minor_blocked);
  const identityRequired = has(BLOCKER.identity_verification_required);
  const ageUnavailable = has(BLOCKER.identity_age_unavailable);
  const isNew = status.status === 'none';

  return (
    <div className="wizard narrow">
      <div className="page-head">
        <h1>내 얼굴로 만드는 모델</h1>
        <p>내 얼굴·신체로 착장 컷을 만들고, 얼굴을 라이선스(VC)로 발행해요. 본인확인 → 동의 → 얼굴 3장 → 신체 정보 → 라이선스 순서로 진행돼요.</p>
      </div>

      {purging && (
        <div className={`${s.banner} ${s.bannerWarn}`}>
          <Icon name="alertTri" size={16} />
          <span>얼굴·신체 데이터 삭제가 진행 중이에요. 완료되면 새로 시작할 수 있어요.</span>
        </div>
      )}

      {!purging && minorBlocked && (
        <div className={`${s.banner} ${s.bannerWarn}`}>
          <Icon name="alertTri" size={16} />
          <span>만 19세 미만은 이 기능을 이용할 수 없어요.</span>
        </div>
      )}

      {/* 본인확인은 됐지만 연령 정보를 못 받은 상태 — 재인증을 유도하면 같은 결과로 무한 왕복하므로
          종결 안내한다(본인확인 단계로 되돌리지 않는다). */}
      {!purging && !minorBlocked && ageUnavailable && (
        <div className={`${s.banner} ${s.bannerWarn}`}>
          <Icon name="alertTri" size={16} />
          <span>
            본인확인은 완료됐지만 성인 여부를 확인할 수 있는 정보를 받지 못했어요.
            다시 인증해도 같은 결과라 고객센터로 문의해 주세요.
          </span>
        </div>
      )}

      {!purging && !minorBlocked && !ageUnavailable && isNew && (
        <div className="surface" style={{ textAlign: 'center' }}>
          <p className="hint" style={{ marginBottom: 16 }}>
            {identityRequired
              ? '아직 등록된 내 모델이 없어요. 본인확인(성인 인증)부터 시작해주세요.'
              : '아직 등록된 내 모델이 없어요. 동의부터 시작해주세요.'}
          </p>
          <Button
            variant="primary" iconRight="arrowRight"
            onClick={() => navigate(identityRequired ? '/model/register' : '/model/license')}
          >
            {identityRequired ? '본인확인 시작하기' : '동의하고 시작하기'}
          </Button>
        </div>
      )}

      {!purging && !minorBlocked && !ageUnavailable && !isNew && (
        <>
          <div className="surface">
            <div className={s.sectionLabel}>진행 체크리스트</div>
            {/* 동의·얼굴·신체는 /model/license 의 한 여정으로 이어진다(step02) — 링크를 전부
                거기로 보낸다. ModelLicense 가 blockers 를 읽어 **덜 끝난 단계에서 이어받으므로**
                어느 줄을 눌러도 사용자는 자기 자리로 간다. 단독 라우트(/model/consent 등)는
                살아있지만(직접 URL·기존 링크 호환) 허브는 여정을 정면에 세운다. */}
            <StepLink to="/model/register" icon="user" title="본인확인(성인 인증)"
              desc="모바일 신분증으로 성인 여부 확인" done={!identityRequired} />
            <StepLink to="/model/license" icon="lock" title="필수 동의"
              desc="서비스이용·국외이전 동의" done={!has(BLOCKER.consent_missing)} locked={identityRequired} />
            <StepLink to="/model/license" icon="person" title="얼굴 3장 업로드"
              desc="정면·측면·45도 각도별 사진" done={!has(BLOCKER.photos_incomplete)} locked={identityRequired} />
            <StepLink to="/model/license" icon="shapes" title="신체 정보 입력"
              desc="키·몸무게·체형" done={!has(BLOCKER.body_profile_missing)} locked={identityRequired} />
            {/* 발급은 위 3단계가 끝나야(프로필 ready) 열린다 — 서버가 400 으로 막는 조건과 동일. */}
            <StepLink to="/model/license" icon="checkSquare" title="얼굴 라이선스 발급"
              desc="사용 조건을 정하고 VC 로 발행" done={false}
              locked={identityRequired || !status.canGenerate} />

            {status.canGenerate ? (
              <div className={s.hubCta}>
                <Button variant="primary" block iconRight="arrowRight" onClick={() => navigate('/model/generate')}>
                  내 모델로 생성하기
                </Button>
              </div>
            ) : (
              <p className="hint" style={{ marginTop: 16 }}>위 항목을 모두 마치면 내 모델로 생성할 수 있어요.</p>
            )}
          </div>

          <Link to="/model/withdraw" className={s.footerLink}>
            <Icon name="trash" size={13} />얼굴·신체 데이터 삭제
          </Link>
        </>
      )}

      {purging && (
        <Link to="/model/withdraw" className={s.footerLink}>
          <Icon name="chevRight" size={13} />삭제 진행 상태 확인
        </Link>
      )}
    </div>
  );
}

export default ModelHub;
