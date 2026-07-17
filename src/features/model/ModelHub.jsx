/* =============================================================
   features/model — 모델 섹션 허브 (/model)
   FaceMarket 모델 온보딩(본인확인·라이선스)과 개인화(내 얼굴·신체) 단계를
   한 섹션에서 이어 보여주는 진입점. GET /v1/personalization/status 의
   canGenerate·blockers 를 체크리스트로 보여준다(api-spec §3.4). 완료
   (canGenerate:true)면 "내 모델로 생성하기" 진입, 아니면 부족한 단계로
   바로 이동할 수 있는 링크를 보여준다.
   ============================================================= */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Button, ErrorState, Icon, useToast } from '@/components/ui.jsx';
import { getStatus } from '@/lib/api/personalization.js';
import { buildMyModelAssets, listMyModels } from '@/lib/api/facemarket.js';
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
  const [assetsReady, setAssetsReady] = useState(false); // 실존 모델 그리드 자산 빌드 완료
  const [building, setBuilding] = useState(false);
  const pollRef = useRef(null);

  const load = useCallback(async () => {
    setPhase('loading');
    try {
      setStatus(await getStatus());
      // 자산 빌드 상태는 fm_models 카드의 assetsReady 로 확인(개인화 status 와 별개).
      try {
        const mine = await listMyModels();
        setAssetsReady(Boolean(mine?.[0]?.assetsReady));
      } catch { /* facemarket off·미검증 등 — 자산 섹션 비활성 */ }
      setPhase('ready');
    } catch (e) {
      push?.(e.message, { icon: 'alertCircle' });
      setPhase('error');
    }
  }, [push]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => () => { if (pollRef.current) clearTimeout(pollRef.current); }, []);

  // 얼굴 3장 → 2×2 그리드 자산 빌드. 얼굴 대조 QC 통과 시에만 등록되며, 완료는 assetsReady 폴링으로 판단.
  const onBuildAssets = useCallback(async () => {
    setBuilding(true);
    try {
      await buildMyModelAssets();
      const started = Date.now();
      const poll = async () => {
        try {
          const mine = await listMyModels();
          if (mine?.[0]?.assetsReady) {
            setAssetsReady(true);
            setBuilding(false);
            push?.('모델 자산이 준비됐어요. 이제 셀러가 선택할 수 있어요.', { icon: 'check' });
            return;
          }
        } catch { /* 일시 오류 — 계속 폴링 */ }
        if (Date.now() - started > 120000) { // 2분 타임아웃(QC 실패 등)
          setBuilding(false);
          push?.('자산 생성이 지연되고 있어요. 얼굴 사진이 동일인인지 확인 후 다시 시도해 주세요.', { icon: 'alertCircle' });
          return;
        }
        pollRef.current = setTimeout(poll, 2500);
      };
      pollRef.current = setTimeout(poll, 2500);
    } catch (e) {
      setBuilding(false);
      push?.(e.message, { icon: 'alertCircle' });
    }
  }, [push]);

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
            onClick={() => navigate(identityRequired ? '/model/register' : '/model/license?step=consent')}
          >
            {identityRequired ? '본인확인 시작하기' : '동의하고 시작하기'}
          </Button>
        </div>
      )}

      {!purging && !minorBlocked && !ageUnavailable && !isNew && (
        <>
          <div className="surface">
            <div className={s.sectionLabel}>진행 체크리스트</div>
            {/* 본인확인 이후 단계는 /model/license 의 단일 단계형 여정에서 진행한다. */}
            <StepLink to="/model/license?step=consent" icon="lock" title="필수 동의"
              desc="서비스이용·국외이전 동의" done={!has(BLOCKER.consent_missing)} locked={identityRequired} />
            <StepLink to="/model/license?step=face" icon="person" title="얼굴 3장 업로드"
              desc="정면·측면·45도 각도별 사진" done={!has(BLOCKER.photos_incomplete)} locked={identityRequired} />
            <StepLink to="/model/license?step=body" icon="shapes" title="신체 정보 입력"
              desc="키·몸무게·체형" done={!has(BLOCKER.body_profile_missing)} locked={identityRequired} />
            {/* 발급은 위 3단계가 끝나야(프로필 ready) 열린다 — 서버가 400 으로 막는 조건과 동일. */}
            <StepLink to="/model/license?step=terms" icon="checkSquare" title="얼굴 라이선스 발급"
              desc="사용 조건을 정하고 VC 로 발행" done={false}
              locked={identityRequired || !status.canGenerate} />

            {/* 셀러 마켓 노출용 그리드 자산(얼굴 3장 → 2×2 세드카드). 얼굴 대조 QC 통과 시 등록되고,
                셀러 카탈로그에서 선택 가능해진다(assetsReady). 프로필 완료(canGenerate) 후 열린다. */}
            {status.canGenerate && (
              assetsReady ? (
                <div className={s.banner}>
                  <Icon name="check" size={16} />
                  <span>모델 자산 준비 완료 — 셀러가 카탈로그에서 선택할 수 있어요.</span>
                </div>
              ) : (
                <div className={s.hubCta}>
                  <Button variant="secondary" block iconRight={building ? undefined : 'sparkles'}
                    onClick={onBuildAssets} disabled={building}>
                    {building ? '자산 생성 중… (얼굴 대조 확인)' : '셀러 마켓용 모델 자산 생성'}
                  </Button>
                  <p className="hint" style={{ marginTop: 8 }}>
                    내 얼굴 3장을 아이덴티티 시트로 만들어요. 본인 얼굴이 맞는지 자동 대조 후 등록돼요.
                  </p>
                </div>
              )
            )}

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
