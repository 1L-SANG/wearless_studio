/* =============================================================
   모델 정보 섹션 — 내 얼굴이 어떻게 취급되는지.
   PRD §10 프라이버시 하드룰 7개를 모델 언어로 옮긴 것이다. 이 문장들은
   마케팅 카피가 아니라 코드가 실제로 지키는 규칙의 번역이라, 구현이
   바뀌면 여기도 같이 바뀌어야 한다.

   하드룰을 그대로 옮기면 안 되는 자리가 두 곳 있다. 룰 1(공개 주소 없음)은
   대표 이미지에는 해당하지 않고(presigned GET 1h), 룰 2(생체정보 0픽셀)는
   공개 검증 화면에 마스킹 이름·만 나이가 함께 뜨는 걸 가리지 않는다.
   둘 다 문장 안에 예외를 적어 뒀다 — 지우면 랜딩이 코드보다 세게 말하게 된다.

   '생체정보 처리 동의'는 동의문 버전 계약이라 다른 말로 바꾸지 않는다(하드룰 7).
   ============================================================= */
import { Icon } from '@/components/ui.jsx';
import s from '../FacemarketLanding.module.css';

const RULES = [
  {
    icon: 'lock',
    // 하드룰 1 은 등록 사진(face_front 등)에 대해서만 참이다. 대표 이미지는 예외 —
    // 셀러 카탈로그에 얼굴이 보여야 모델을 고를 수 있어서 의도적으로 노출한다.
    // coverImageUrl 은 비공개 R2 의 presigned GET(1h, facemarket.py `_cover_serving_url`)이라
    // 카드 HTML 에 무인증 주소가 그대로 실린다. 예외를 안 적으면 이 문장이 거짓말이 된다.
    title: '얼굴 사진은 공개 주소가 없어요 (대표 이미지만 예외)',
    body: '본인 확인에 올린 얼굴 사진은 공개 주소를 갖지 않고, 권한이 확인된 요청에만 그때그때 열립니다. 다만 셀러 카탈로그에 걸리는 대표 이미지는 1시간만 유효한 서명 주소로 나가므로, 그 주소를 받은 사람은 그동안 로그인 없이 볼 수 있습니다. 대표 이미지는 건너뛸 수 있는 선택 단계입니다.',
  },
  {
    icon: 'eyeOff',
    // 하드룰 원문은 "생체정보 0픽셀"이지 "조건과 유효 여부만"이 아니다. 공개 검증 응답은
    // 마스킹 이름(홍*동)과 만 나이도 싣는다(facemarket.py:1346-1349 → PublicVerify.jsx:89-93).
    title: 'QR 을 찍은 사람은 얼굴을 못 봐요',
    body: '공개 검증 화면에는 라이선스 조건과 유효 여부, 그리고 마스킹된 이름과 나이가 나옵니다. 얼굴은 한 픽셀도 렌더되지 않습니다.',
  },
  {
    icon: 'checkSquare',
    title: '검사를 통과하기 전엔 아무 데도 안 쓰여요',
    body: '올린 사진은 격리된 상태로 보관되다가, 품질과 본인 일치 검사를 통과한 뒤에야 쓰입니다.',
  },
  {
    icon: 'trash',
    title: '신분증 사진은 저장하지 않아요',
    body: '본인 확인에 쓴 신분증 초상은 처리하는 순간에만 메모리에 있고, 저장하거나 로그에 남기지 않습니다.',
  },
  {
    icon: 'link',
    title: '본인 확인값은 해시로만 남아요',
    body: '신원 식별값은 원본이 아니라 되돌릴 수 없는 형태로 저장됩니다.',
  },
  {
    icon: 'image',
    title: '검사 기록에 사진이 남지 않아요',
    body: '품질 검사 로그에는 통과 여부와 사유만 남습니다. 이미지도 파일명도 남기지 않습니다.',
  },
  {
    icon: 'info',
    // 코드가 실제로 하는 건 "등록 행에 동의문 버전 문자열을 함께 저장" 까지다
    // (facemarket_enrollment.py 의 fm_biometric_enrollments.consent_version 삽입).
    // 그 버전이 가리키는 동의문 원문도, 모델이 나중에 열어볼 화면도 레포에 없다 —
    // "나중에도 확인됩니다"는 하드룰 7(동의문 버전 계약)보다 넓은 약속이라 뺐다.
    title: '어느 버전에 동의했는지가 기록돼요',
    body: '생체정보 처리 동의는 동의문 버전과 함께 등록 기록에 남습니다.',
  },
];

// ctaLabel·onPrimary 는 RegisterSection 이 이미 쓰는 것과 **같은 prop 두 개**다
// (FacemarketLanding.jsx 의 cta.label / onPrimary). 새 이름을 만들지 마라 — 랜딩의
// CTA 세 곳(헤더·히어로·등록 섹션)이 전부 같은 핸들러를 쓰는 게 계약이다.
export function ModelInfoSection({ ctaLabel, onPrimary }) {
  return (
    <section className={s.section} id="model-info">
      <p className={s.eyebrow}>모델 정보</p>
      <h2 className={s.sectionTitle}>내 얼굴이 어떻게 다뤄지나요</h2>
      <p className={s.sectionLead}>
        얼굴은 생체정보라 일반 사진과 같은 규칙으로 다루지 않습니다.
        아래는 마케팅 문구가 아니라 서비스가 실제로 지키는 규칙입니다.
      </p>

      <ul className={s.ruleList}>
        {RULES.map((rule) => (
          <li className={s.rule} key={rule.title}>
            <Icon name={rule.icon} size={20} stroke={1.7} />
            <div>
              <h3 className={s.ruleTitle}>{rule.title}</h3>
              <p className={s.ruleBody}>{rule.body}</p>
            </div>
          </li>
        ))}
      </ul>

      <div className={s.exit}>
        <h3 className={s.exitTitle}>그만두고 싶을 때</h3>
        {/* 경로는 텍스트로만 적는다 — /model/license 는 RequireAuth + 소유 모델 가드 뒤라
            비로그인 방문자가 링크를 누르면 로그인 모달로 튕긴다(상단바 앵커와 같은 이유).

            경고: /model/withdraw 를 여기 적으면 안 된다. 그 화면은 개인화(personalization)
            도메인 전용이다 — ModelWithdraw.jsx 가 GET /v1/personalization/status 를 보고
            personalization_profiles 행이 없으면 "등록된 데이터가 없어요." 한 줄만 그린다.
            FaceMarket 7단계 위저드는 그 테이블에 행을 만들지 않으므로(facemarket.py 가 쓰는
            건 personalization_identity_verifications 하나뿐), FaceMarket 모델에게 그 화면은
            빈 화면이고 POST /v1/personalization:withdraw 도 404 다. facemarket 라우트에도
            등록 철회·삭제는 없다(있는 건 /licenses/{id}/revoke 와 진행 중 등록 cancel).
            등록 파기 진입점이 생기기 전까지 이 문단은 라이선스 해지까지만 약속한다.

            어휘: 여기서 '폐기'라고 쓰지 마라. 제품 화면의 말은 '해지'다 —
            ModelLicense.jsx:319 버튼 "해지", :161 확인창 "이 라이선스를 해지하면…",
            :152 상태 "해지됨", PublicVerify.jsx:25 "해지된 라이선스예요". PRD 는 '폐기'를
            쓰지만 PRD 는 사용자 대면 문자열이 아니다. 랜딩만 '폐기'라고 부르면, 되돌릴 수
            없는 조치를 앞둔 모델이 화면에서 그 단어를 못 찾고 멈춘다. */}
        <p className={s.exitBody}>
          발급한 라이선스는 로그인 후 모델 화면(/model/license)에서 언제든 해지할 수 있습니다.
          해지하면 그 즉시 무효로 표시되고, 셀러는 더 이상 그 라이선스로 컷을 만들 수 없습니다.
          다만 등록을 마친 뒤 모델 등록 자체를 지우는 화면은 아직 없습니다 —
          지금 쓸 수 있는 중단 수단은 라이선스 해지입니다.
        </p>
      </div>

      {/* 페이지의 마지막 CTA. 이 섹션은 프라이버시 규칙 7개 + '그만두고 싶을 때'라
          부정문으로 끝나는데, 그게 페이지의 끝이면 전환하려는 사람이 스크롤을 되돌려
          올라가야 한다(3라운드 판정: "마지막 CTA 뒤에 부정문 8개가 이어져 끝난다").
          고지를 줄여서 푸는 게 아니라 — 고지는 그대로 두고 — 버튼을 하나 더 둬서 푼다.
          지우려거든 섹션 순서(FacemarketLanding.jsx)부터 바꿔라.

          prop 이 없으면 그리지 않는다. 이 섹션은 랜딩 밖에서도 재사용될 수 있고,
          onPrimary 없는 버튼은 눌러도 아무 일이 없어 더 나쁘다. */}
      {ctaLabel && onPrimary ? (
        // 여백을 인라인으로 주는 이유: 이 스타일시트는 "앞 요소가 자기 아래 여백을 갖는다"
        // 규칙인데(.ruleList/.rail 이 margin-bottom 2.2rem), .exit 에는 그 여백이 없어
        // 버튼이 카드에 붙는다. 이번 라운드는 새 클래스를 만들지 않기로 해서 같은 2.2rem
        // 리듬만 맞춘다. module.css 에 자리가 생기면 클래스로 옮겨라.
        <div style={{ marginTop: '2.2rem' }}>
          <button className={s.heroCta} onClick={onPrimary} type="button">
            {ctaLabel}
            <Icon name="arrowRight" size={18} stroke={2} />
          </button>
        </div>
      ) : null}
    </section>
  );
}
