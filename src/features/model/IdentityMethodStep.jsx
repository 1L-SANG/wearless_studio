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
import { isMobileLike, SIMPLE_AUTH_DEVICE_REASON } from './identityMethodConfig.js';

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
//
// 간편인증 승인은 어차피 폰 앱으로 온다. PC 로 시작하면 승인 때 폰으로, 신분증 촬영
// 때문에 또 폰으로 — 두 기기를 오가게 된다. 그래서 거친 포인터(coarse pointer, 손가락)가
// 없는 기기에서는 아래에서 isMobileLike() 로 이 이유를 하나 더 만들어, 부모가 넘긴
// simpleAuthUnavailableReason 과 같은 채널(같은 disabled·같은 힌트 자리)로 합쳐 보여준다 —
// 막을 이유가 두 가지라고 비활성화·힌트 표시를 두 벌 만들지 않는다. 문구(SIMPLE_AUTH_
// DEVICE_REASON)는 identityMethodConfig.js 에 있다 — ModelRegister.jsx 의 startEnrollment
// 도 같은 문구를 써야 하는 자리가 있어서(수단이 하나뿐이면 이 화면 자체가 안 뜨는 경로),
// 두 파일이 각자 문구를 들고 있다가 말이 갈리지 않게 한 곳에 둔다.

export default function IdentityMethodStep({ methods, onPick, simpleAuthUnavailableReason }) {
  useEffect(() => {
    if (methods.length === 1) onPick(methods[0]);
  }, [methods, onPick]);

  if (methods.length <= 1) return null;

  // 설정 부재가 기기 판별보다 먼저다 — 설정 자체가 없으면 폰이어도 위젯이 못 열리므로 그
  // 이유가 더 근본적이다(그리고 부모가 이미 계산해 준 값이라 다시 계산할 필요가 없다).
  // isMobileLike() 는 렌더마다 새로 읽는다: 마운트 시점 값을 state 로 캐시해 버리면 그
  // 값이 바뀌는 드문 경우(태블릿에 마우스를 붙이는 등)에도 옛 판정이 화면에 남는다.
  // 다만 pointer:coarse 는 화면 회전·창 리사이즈로는 바뀌지 않는 값이라, 그 두 이벤트에
  // 대해서는 애초에 "낡을" 값 자체가 없다.
  const simpleAuthReason = simpleAuthUnavailableReason
    || (isMobileLike() ? null : SIMPLE_AUTH_DEVICE_REASON);

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
            disabled={Boolean(simpleAuthReason)}
            onClick={() => onPick('simple_auth')}
          >
            <span className={s.methodChoiceLabel}>{METHOD_COPY.simple_auth.label}</span>
            <small className={s.methodChoiceHint}>
              {simpleAuthReason || METHOD_COPY.simple_auth.hint}
            </small>
          </button>
        )}
      </div>
    </div>
  );
}
