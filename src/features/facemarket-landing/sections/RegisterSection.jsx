/* =============================================================
   모델 등록 섹션 — 7단계 레일 미리보기.
   진행 레일을 미리 보여주는 건 장식이 아니다. 순차 KYC 라 몇 단계인지
   모르고 들어가면 중간에 이탈한다. 건너뛸 수 있는 단계도 여기 적는다.
   레일 순서·라벨은 ModelRegister.jsx 의 FLOW_STEPS 와 같아야 한다
   (동의·신분증·사진·체형·대표·라이브·완료 — 2026-09-01 대조 확인).
   라벨은 그쪽이 정본이고, note 는 그 단계가 **실제로** 하는 일이다.

   '일곱 단계면 끝납니다'는 맞는 말이다. 한때 이 자리에 "OACX 지역 차단으로 2단계에서
   전원이 막히니 손대지 마라"는 경고가 있었는데 **사실이 아니었다** — PRD §12 표가 낡은
   것이었고, prod 는 서울 리전 프록시로 이미 우회한다(copilot/api/manifest.yml 의
   `CX_TRANS_BASE_URL` → fm-cx-proxy, API GW→Lambda 가 cx.raonsecure.co.kr:18543 으로
   forward). 서버는 config.py `cx_trans_base_url` 로 그 값을 읽어 facemarket.py
   `_fetch_trans` 가 한국 IP 로 나간다. 2026-08-30·08-31 prod 신원확인 2건이 실제로 통과했다.

   ⚠️ 다만 그 우회는 env 값 하나에 매달려 있다. `CX_TRANS_BASE_URL` 이 지워지면 기본값
   (cx.raonsecure.co.kr 직접 호출)으로 떨어지고, us-east-1 egress 는 방화벽에 막혀
   ConnectTimeout → cx_verify_failed "본인확인에 실패했어요." 로 2단계가 다시 전원 차단된다.
   그때는 이 카피가 거짓이 되므로, 그 값을 지우려면 여기도 같이 봐라.
   ============================================================= */
import { Icon } from '@/components/ui.jsx';
import s from '../FacemarketLanding.module.css';

const STEPS = [
  { label: '동의', note: '생체정보 처리 동의' },
  { label: '신분증', note: '모바일 신분증으로 본인 확인' },
  { label: '사진', note: '얼굴 사진 3장' },
  { label: '체형', note: '선택 — 건너뛸 수 있어요' },
  { label: '대표', note: '선택 — 건너뛸 수 있어요' },
  // prod 는 FM_LIVENESS_ENABLED=false 라(copilot/api/manifest.yml) 카메라 라이브니스가
  // 돌지 않는다 — ModelRegister 가 서버 config 를 보고 이 칸을 조작 없이 지나간다.
  // "실제 본인인지 확인"이라고 적으면 하지 않는 검사를 약속하는 셈이다. 실제 본인 방어는
  // 2단계 모바일 신분증(OACX)과 사진↔신분증 초상 매칭이 한다.
  { label: '라이브', note: '본인 확인 마무리' },
  { label: '완료', note: '모델 준비' },
];

export function RegisterSection({ ctaLabel, onPrimary }) {
  return (
    <section className={s.section} id="register">
      <p className={s.eyebrow}>모델 등록</p>
      <h2 className={s.sectionTitle}>일곱 단계면 끝납니다</h2>
      <p className={s.sectionLead}>
        본인 확인이 필요한 절차라 순서대로 진행합니다. 체형과 대표 이미지는 건너뛸 수 있고,
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

      {/* 레일 7칸이 끝이 아니다 — 그 뒤에 화면이 하나 더 있고, 예고 없이 만나면 이탈한다.
          ModelRegister.jsx 의 완료 화면(step === 'terms')은 "모델 정보 등록 완료 /
          마지막으로 얼굴 사용 조건을 정해 주세요" 와 `/model/license?step=terms` 링크다.
          그 화면의 ModelLicense TermsStep 은 `deadline = Date.now() + 4 * 60 * 1000` 으로
          8초 간격 자동 재시도하며 '준비 중 → 진행 중'을 띄운다(opendid holder 콜드부트
          ~2분, PRD §7.3). 예고가 없으면 멈춘 줄 알고 탭을 닫고, 그 지점은 등록이
          vc_pending 이라 라이선스가 없는 상태로 남는다.
          PRD §13-2 "대기 화면이 제품의 일부다"를 랜딩에서도 지키는 문장이라 지우지 마라. */}
      <p className={s.sectionLead}>
        등록을 마치면 얼굴 사용 조건을 정해 라이선스를 발급합니다.
        발급에는 몇 분이 걸릴 수 있어요 — 기다리면 됩니다.
      </p>

      <button className={s.heroCta} onClick={onPrimary} type="button">
        {ctaLabel}
        <Icon name="arrowRight" size={18} stroke={2} />
      </button>
    </section>
  );
}
