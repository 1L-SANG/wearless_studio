/* FaceMarket 모델 등록 상태와 다음 안전한 진입점만 보여주는 허브. */
import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Button, ErrorState, Icon, useToast } from '@/components/ui.jsx';
import { getCurrentEnrollment, listMyModels } from '@/lib/api/facemarket.js';
import s from './ModelPersonalization.module.css';

const MODEL_STATUS_LABEL = {
  pending: '본인 확인 진행 중',
  reverification_required: '재검증 필요',
  verified: '검증 완료',
};

const ENROLLMENT_STATUS_LABEL = {
  photos_pending: '사진 등록 대기',
  liveness_pending: '라이브 얼굴 확인 대기',
  processing: '얼굴 확인 처리 중',
  asset_building: '모델 준비 중',
  license_pending: '라이선스 조건 입력 대기',
  vc_pending: 'VC 발급 대기',
};

export function ModelHub() {
  const navigate = useNavigate();
  const { push } = useToast();
  const [phase, setPhase] = useState('loading'); // loading|ready|error
  const [ownedModel, setOwnedModel] = useState(null);
  const [enrollment, setEnrollment] = useState(null);

  const load = useCallback(async () => {
    setPhase('loading');
    try {
      const mine = await listMyModels();
      setOwnedModel(mine?.[0] || null);
      try { setEnrollment(await getCurrentEnrollment()); }
      catch (requestError) {
        if (requestError?.status !== 404) throw requestError;
        setEnrollment(null);
      }
      setPhase('ready');
    } catch (e) {
      push?.(e.message, { icon: 'alertCircle' });
      setPhase('error');
    }
  }, [push]);

  useEffect(() => { load(); }, [load]);

  if (phase === 'loading') return <div className="wizard narrow"><div className="surface">불러오는 중…</div></div>;
  if (phase === 'error') return <div className="wizard narrow"><div className="surface"><ErrorState desc="상태를 불러오지 못했어요." onRetry={load} /></div></div>;

  const isNew = !ownedModel && !enrollment;
  const enrollmentNeedsTerms = ['license_pending', 'vc_pending'].includes(enrollment?.status);
  const registrationPath = enrollmentNeedsTerms
    ? `/model/license?step=terms&enrollment=${encodeURIComponent(enrollment.id)}`
    : '/model/register';

  return (
    <div className="wizard narrow">
      <div className="page-head">
        <h1>내 얼굴로 만드는 모델</h1>
        <p>동의 → 정면·45도·측면 사진 → 라이브 얼굴 → 모바일 신분증 → 라이선스 순서로 안전하게 등록해요.</p>
      </div>

      {isNew && (
        <div className="surface" style={{ textAlign: 'center' }}>
          <p className="hint" style={{ marginBottom: 16 }}>
            아직 등록된 내 모델이 없어요. 생체정보 처리 동의부터 시작해 주세요.
          </p>
          <Button
            variant="primary" iconRight="arrowRight"
            onClick={() => navigate('/model/register')}
          >
            모델 등록 시작하기
          </Button>
        </div>
      )}

      {!isNew && (
        <>
          <div className="surface">
            <div className={s.sectionLabel}>FaceMarket 등록 상태</div>
            {ownedModel && (
              <div className={s.banner}>
                <Icon name={ownedModel.status === 'verified' ? 'check' : 'person'} size={16} />
                <span>모델 · {MODEL_STATUS_LABEL[ownedModel.status] || ownedModel.status}</span>
              </div>
            )}
            {enrollment && (
              <div className={s.banner} style={{ marginTop: 8 }}>
                <Icon name="lock" size={16} />
                <span>등록 · {ENROLLMENT_STATUS_LABEL[enrollment.status] || enrollment.status}</span>
              </div>
            )}

            {(ownedModel?.status !== 'verified' || enrollment) && (
              <div className={s.hubCta}>
                <Button variant="secondary" block iconRight="arrowRight" onClick={() => navigate(registrationPath)}>
                  {enrollmentNeedsTerms ? '라이선스 조건 설정 이어가기' : '안전한 모델 등록 이어가기'}
                </Button>
              </div>
            )}

            {ownedModel?.status === 'verified' && !enrollment && (
              <div className={s.hubCta}>
                <Button variant="secondary" block iconRight="chevRight" onClick={() => navigate('/model/license')}>
                  얼굴 라이선스 확인
                </Button>
              </div>
            )}

            {ownedModel?.status === 'verified' && !enrollment ? (
              <div className={s.hubCta}>
                <Button variant="primary" block iconRight="arrowRight" onClick={() => navigate('/model/generate')}>
                  내 모델로 생성하기
                </Button>
              </div>
            ) : (
              <p className="hint" style={{ marginTop: 16 }}>본인 확인과 라이선스 발급을 마치면 내 모델로 생성할 수 있어요.</p>
            )}
          </div>

          <Link to="/model/withdraw" className={s.footerLink}>
            <Icon name="trash" size={13} />얼굴·신체 데이터 삭제
          </Link>
        </>
      )}
    </div>
  );
}

export default ModelHub;
