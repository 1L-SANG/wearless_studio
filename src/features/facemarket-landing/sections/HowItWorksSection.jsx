/* =============================================================
   서비스 설명 — 홈('/')의 리드 아래. 모델이 되기까지의 전체 흐름을 한 번에 보여 준다.

   ⚠️ 이 문구는 **아직 코드에 없는 절차를 설명한다.** 2026-09-02 기준 레포에는
   지원서 화면(/model/apply)도, 지원서 API 도, 관리자 검토 화면도 없다 — 지금 CTA 는
   곧바로 /model/register(7단계 등록)로 간다. 사용자가 준 기획서(모델 지원·검토 리뉴얼)를
   랜딩에 먼저 적어 둔 것이고, 기능이 붙기 전까지는 아래 셋이 서로 어긋난다:

     1) 이 섹션의 1·2·3단계(지원 → 검토 → 승인)
     2) 상단 CTA(registerCta.js)가 보내는 /model/register
     3) /register 안내 페이지의 "일곱 단계면 끝납니다"

   기능 플래그를 켤 때 **셋을 같이** 손봐라. 플래그가 꺼진 채로 오래 두면 이 페이지가
   하지도 않는 심사를 약속하는 상태가 된다.

   기획서에서 랜딩에 옮기지 않은 것들(내부 운영 사항이라 방문자에게 의미 없다):
   Slack 알림, admin.wearless.kr, 관리자 동시 처리 잠금, 메일 발송 실패 재시도,
   임시 프로필 사진 자동 삭제, 기능 플래그 자체.
   ============================================================= */
import s from '../FacemarketLanding.module.css';

const STEPS = [
  {
    label: '모델 지원',
    note: '이름·생년월일·지역·키 같은 기본 정보와 활동하고 싶은 카테고리, 포트폴리오·SNS, 프로필 사진 한 장을 냅니다.',
  },
  {
    label: '검토',
    note: '들어온 지원서를 한 건씩 확인합니다. 검토 중에는 지원서를 하나만 낼 수 있어요.',
  },
  {
    label: '결과 안내',
    note: '승인·거절을 메일로 알립니다. 메일이 안 와도 FaceMarket 화면에서 언제든 현재 상태를 봅니다.',
  },
  {
    label: '신분증 확인',
    note: '지원서에 쓴 이름·생년월일이 신분증과 같은지 확인합니다.',
  },
  {
    label: '모델 등록',
    note: '얼굴 사진을 등록하고, 얼굴 사용 조건을 정해 라이선스를 발급합니다.',
  },
];

export function HowItWorksSection() {
  return (
    <section className={s.section} id="how">
      <p className={s.eyebrow}>어떻게 진행되나요</p>
      <h2 className={s.sectionTitle}>지원하고, 검토를 거쳐, 모델이 됩니다</h2>
      {/* '검토를 거친 사람만'이라고 쓰는 이유 — 이 절차의 목적이 그것이다. 다만 '심사',
          '선발' 처럼 경쟁을 암시하는 말은 쓰지 않는다. 기준을 공개한 적이 없어서다. */}
      <p className={s.sectionLead}>
        누구나 바로 등록하는 대신, 지원서를 먼저 내고 검토를 거칩니다.
        확인된 사람만 얼굴을 등록하게 하려는 절차예요.
      </p>

      <ol className={s.rail}>
        {STEPS.map((step, index) => (
          <li className={s.railStep} key={step.label}>
            <span className={s.railNumber}>{index + 1}</span>
            <span className={s.railLabel}>{step.label}</span>
            <span className={s.railNote}>{step.note}</span>
          </li>
        ))}
      </ol>

      {/* 승인 상태가 남는다는 건 사용자에게 실질적인 안심 장치다(기획서 §4) — 등록을 하다
          말아도 지원서를 다시 쓰지 않는다. */}
      <p className={s.sectionLead}>
        승인을 받은 뒤에는 등록을 중간에 멈춰도 승인 상태가 남습니다.
        지원서를 다시 낼 필요 없이 이어서 하면 됩니다.
      </p>
    </section>
  );
}
