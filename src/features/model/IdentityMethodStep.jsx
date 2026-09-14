/* =============================================================
   IdentityMethodStep — 본인 확인 방법(모바일 신분증 vs 간편인증) 선택 화면.
   FM_IDENTITY_METHODS=mid 인 발표/롤백 모드에서는 이 화면 자체가 없어야 한다 — 한
   방법뿐이면 클릭 한 번을 아낀다(그 값이 이 컴포넌트가 받는 methods 배열의 길이 1).
   methods 파싱(import.meta.env.VITE_FM_IDENTITY_METHODS)은 ModelRegister.jsx 가 한다 —
   이 컴포넌트는 그 결과 배열만 받는다(단일 진실 원천을 부모에 두어, 등록을 실제로 만들 때
   쓰는 identityMethod 값과 이 화면이 보여주는 선택지가 어긋나지 않게 한다).
   ============================================================= */
import { useEffect } from 'react';
import s from './ModelRegister.module.css';

const METHOD_COPY = Object.freeze({
  mid: {
    label: '모바일 신분증으로 확인',
    hint: '모바일 신분증 앱이 있으면 가장 빠릅니다',
  },
  simple_auth: {
    label: '간편인증으로 확인',
    hint: 'PASS·카카오·네이버 등. 신분증을 찍어 올리는 단계가 있습니다',
  },
});

// simpleAuthUnavailableReason: VITE_CX_AUTH_CONFIG_URL 이 없을 때 부모가 채워 넣는 문구.
// 그 설정 없이 위젯을 열면 v1.0 경로(모바일 신분증용)를 타서 조용히 실패하므로, 버튼을
// 아예 숨기지 않고 비활성화한 채 이유를 화면에 남긴다(설정 문제를 사용자 탓처럼 보이지
// 않게, 그리고 운영자가 콘솔 없이도 원인을 볼 수 있게).
export default function IdentityMethodStep({ methods, onPick, simpleAuthUnavailableReason }) {
  useEffect(() => {
    if (methods.length === 1) onPick(methods[0]);
  }, [methods, onPick]);

  if (methods.length <= 1) return null;

  return (
    <div className="surface">
      <div className={s.stepHead}>
        <div>
          <div className={s.stepEyebrow}>본인 확인</div>
          <h2 className={s.stateTitle}>본인 확인 방법을 골라 주세요</h2>
        </div>
      </div>
      <div className={s.methodChoices}>
        {methods.includes('mid') && (
          <button type="button" className={s.methodChoice} onClick={() => onPick('mid')}>
            <span className={s.methodChoiceLabel}>{METHOD_COPY.mid.label}</span>
            <small className={s.methodChoiceHint}>{METHOD_COPY.mid.hint}</small>
          </button>
        )}
        {methods.includes('simple_auth') && (
          <button
            type="button"
            className={s.methodChoice}
            disabled={Boolean(simpleAuthUnavailableReason)}
            onClick={() => onPick('simple_auth')}
          >
            <span className={s.methodChoiceLabel}>{METHOD_COPY.simple_auth.label}</span>
            <small className={s.methodChoiceHint}>
              {simpleAuthUnavailableReason || METHOD_COPY.simple_auth.hint}
            </small>
          </button>
        )}
      </div>
    </div>
  );
}
