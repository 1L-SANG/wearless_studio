/* =============================================================
   모델 등록 섹션 — 4단계 레일 미리보기.
   진행 레일을 미리 보여주는 건 장식이 아니다. 순차 KYC 라 몇 단계인지
   모르고 들어가면 중간에 이탈한다. 중간 저장이 되는 것도 여기 적는다.
   레일 순서·라벨은 ModelRegister.jsx 의 4단계(본인확인·사진·조건·증서, 2026-09-11 Phase C)와
   같아야 한다. 라벨은 그쪽이 정본이고, note 는 그 단계가 **실제로** 하는 일이다.

   본인확인(OACX 모바일 신분증)은 서울 리전 프록시로 우회한다. 그 우회는 env 값 하나에
   매달려 있다. `CX_TRANS_BASE_URL` 이 지워지면 기본값(cx.raonsecure.co.kr 직접 호출)으로
   떨어지고, us-east-1 egress 는 방화벽에 막혀 ConnectTimeout → cx_verify_failed
   "본인확인에 실패했어요." 로 1단계가 전원 차단된다. 그 값을 지우려면 여기도 같이 봐라.
   ============================================================= */
import { Icon } from '@/components/ui.jsx';
import s from '../FacemarketLanding.module.css';

const STEPS = [
  { label: '본인확인', note: '동의 3건과 모바일 신분증 인증' },
  { label: '사진', note: '얼굴 8장, 상반신 5장, 전신 5장' },
  { label: '조건', note: '쓸 수 있는 옷 종류' },
  { label: '증서', note: '얼굴 사용 증서 발급' },
];

export function RegisterSection({ ctaLabel, onPrimary }) {
  return (
    <section className={s.section} id="register">
      <p className={s.eyebrow}>모델 등록</p>
      <h2 className={s.sectionTitle}>네 단계면 끝납니다</h2>
      <p className={s.sectionLead}>
        본인 확인이 필요한 절차라 순서대로 진행합니다. 체형은 선택이고,
        중간에 나갔다가 이어서 할 수 있습니다.
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

      {/* 증서 발급(4단계)은 opendid holder 콜드부트로 몇 분 걸릴 수 있다(PRD §7.3).
          예고가 없으면 멈춘 줄 알고 탭을 닫고, 등록이 vc_pending 으로 남는다.
          PRD §13-2 "대기 화면이 제품의 일부다"를 랜딩에서도 지키는 문장이라 지우지 마라. */}
      <p className={s.sectionLead}>
        증서 발급에는 몇 분이 걸릴 수 있어요. 기다리면 됩니다.
        등록을 마치면 우리가 사진을 검수하고 테스트컷을 보내요.
      </p>

      <button className={s.heroCta} onClick={onPrimary} type="button">
        {ctaLabel}
        <Icon name="arrowRight" size={18} stroke={2} />
      </button>
    </section>
  );
}
