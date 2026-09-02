/* =============================================================
   라이선싱 설명 섹션.
   여기서 라이선스를 발급하지 않는다 — 발급 폼은 /model/license 그대로다.
   이 섹션은 "무엇을 정할 수 있고, 그게 어떻게 증명되는지"만 설명한다.

   문장은 전부 구현에 맞춰 낮춰 둔 상태다. 생체정보를 넘기라고 설득하는
   페이지라, 코드보다 세게 말하면 그냥 거짓말이 된다. 아래 네 곳은 특히
   구현이 바뀌기 전엔 되돌리지 마라 —
   - 조건 항목은 넷뿐이다(허용 품목·금지 품목·건당 단가·유효기간).
     ModelLicense.jsx:396-431 / facemarket.py `_clean_uses`. 채널 조건은 없다.
   - 공개 검증(GET /v1/facemarket/verify/{id})은 fm_licenses 화이트리스트
     SELECT 한 방이다(facemarket.py:1292-1350). VC 서명 대조도 체인 조회도 없다.
   - 온체인 정산은 record-only 다(facemarket_chain.py:3 "코인 이동 없음").
     지급 코드도, 모델이 자기 사용 기록을 볼 화면도 아직 없다.
   - 정산 기록의 단위는 **컷이 아니라 잡(상세페이지) 1건**이다.
     workers/detail_page_job.py 의 `payment_key=f"job:{job_id}"` / `total=unit_price`.
     "컷마다"로 되돌리면 모델이 컷 수만큼 곱해 기대하게 된다.
   - 체인 기록은 **무조건이 아니라 best-effort** 다. 무조건형("…마다 남습니다")으로
     되돌리지 마라 — facemarket.py `record_license_settlement` 는 docstring 부터
     "best-effort(생성 흐름 비파손)"이고, `app.state.fm_chain` 이 None 이면
     `settlement_skipped_no_chain` 으로 즉시 return None, RPC 실패 뒤 조회까지 실패하면
     `settlement_record_failed`/`settlement_record_unresolved` 로 return None 이다.
     `_mirror_settlement`(fm_settlements 삽입)는 온체인 성공 경로에서만 불린다 —
     즉 실패하면 체인에도 DB 에도 **아무 행이 안 생긴다**. 호출부
     workers/detail_page_job.py 는 잡을 성공 종결·과금한 **뒤에** 훅을 부르고 예외를
     삼키므로(`log.warning("facemarket settlement hook failed…")`), 컷은 배달되고
     셀러 크레딧은 차감된 채 기록만 없는 상태가 실재한다. 문장은 그 상한까지만 적는다.
   ============================================================= */
import { Icon } from '@/components/ui.jsx';
import {
  ALLOWED_BRAND_USE_CATEGORIES,
  FORBIDDEN_BRAND_USE_CATEGORIES,
} from '@/lib/brandUseCategories.js';
import s from '../FacemarketLanding.module.css';

const CARDS = [
  {
    // 'settings' 를 쓰지 마라 — ui.jsx 의 그 path 는 톱니바퀴가 아니라 **해**(원 + 방사선)라
    // 이름과 모양이 어긋나 있다. 20px 에서 보면 설정이 아니라 날씨 아이콘으로 읽힌다.
    // 'pencil' 은 "조건을 직접 쓴다"를 그대로 그린다.
    icon: 'pencil',
    title: '조건을 모델이 정한다',
    // 발급 폼이 받는 조건은 넷뿐이다 — 허용 품목·금지 품목·건당 단가·유효기간.
    // "어떤 채널에"는 제품에 없는 조건이라 뺐다(자사몰만·광고 제외 같은 걸 걸 수 없다).
    body: '어떤 품목에, 건당 얼마로, 얼마 동안 쓸 수 있는지 본인이 고릅니다. 브랜드가 따로 계약서를 들이밀지 않습니다.',
  },
  {
    icon: 'checkSquare',
    title: '허용 품목을 고른다',
    // 구분자는 ', ' 다 — 품목명 자체가 '속옷·란제리'처럼 '·' 를 품고 있어서
    // join('·') 로 이으면 2개가 4개로 읽힌다.
    body: `상의·아우터·데님 등 ${ALLOWED_BRAND_USE_CATEGORIES.length}개 품목 중에서 고릅니다. ${FORBIDDEN_BRAND_USE_CATEGORIES.join(', ')}는 선택지에 없습니다.`,
  },
  {
    icon: 'clock',
    title: '기간이 정해져 있다',
    // 발급 폼(ModelLicense.jsx VALIDITY)의 선택지는 90일·1년·2년 셋이다.
    body: '90일·1년·2년 중에서 고릅니다. 기간이 끝나면 그 라이선스로는 더 이상 컷을 만들 수 없습니다.',
  },
  {
    icon: 'lock',
    title: '서명된 자격증명으로 발급된다',
    body: '조건은 W3C Verifiable Credential 로 발급됩니다. 나중에 말을 바꿀 수 없는 형태로 남습니다.',
  },
  {
    icon: 'search',
    title: 'QR 하나로 누구나 확인한다',
    body: '구매자든 심사위원이든 로그인 없이 QR 을 찍어 유효한 라이선스인지 확인합니다. 그 화면에 얼굴은 나오지 않습니다.',
  },
  {
    icon: 'ban',
    // 제품 화면의 말은 '해지'다 — ModelLicense.jsx:319 버튼 "해지", :161 확인창
    // "이 라이선스를 해지하면…", :152 상태 "해지됨", PublicVerify.jsx:25 "해지된
    // 라이선스예요". PRD 는 '폐기'라고 쓰지만 PRD 는 사용자 대면 문자열이 아니다.
    // 랜딩만 '폐기'라고 부르면 모델이 /model/license 에서 그 버튼을 못 찾는다.
    // '폐기'로 되돌리려면 제품 버튼부터 바꿔라.
    title: '해지하면 즉시 무효가 된다',
    body: '마음이 바뀌면 해지합니다. 해지된 라이선스는 검증 화면에서 곧바로 무효로 표시됩니다.',
  },
];

const RECORD = [
  { icon: 'lock', title: 'VC 발급', body: '라이선스 조건이 서명된 자격증명(W3C VC)으로 발급됩니다.' },
  // 스펙은 이 칸을 '체인 앵커' 라고 불렀지만, 이 레포가 체인에 쓰는 건 사용 1건짜리
  // recordSettlement 뿐이다(facemarket_chain.py). 라이선스 자체의 지문을 올리는 코드는
  // 없으므로 '기록' 까지만 말한다.
  // 무조건형("생길 때마다 남습니다")에서 상한형으로 내렸다. 근거는 파일 상단 주석의
  // best-effort 항목이다 — 실패하면 체인에도 fm_settlements 에도 행이 없다. 같은 칸의
  // '공개 검증'이 이미 '지금은 Wearless 기록 조회라…'로 상한을 적는 선례를 따른다.
  { icon: 'layers', title: '체인 기록', body: '사용 1건이 생기면 그 기록을 OmniOne Chain 에 올립니다. 한 번 올라간 기록은 지우거나 고칠 수 없습니다. 다만 올리기가 실패한 건은 기록이 남지 않습니다.' },
  // 공개 검증은 Wearless DB 화이트리스트 조회다 — VC 서명 대조도, 체인 조회도 하지 않는다.
  // "누구나 원본을 대조한다"로 읽히지 않게 조회라는 걸 문장에 남긴다.
  { icon: 'eye', title: '공개 검증', body: 'QR 주소만 알면 인증 없이 조건과 유효 여부를 조회합니다. 지금은 Wearless 기록 조회라, 서명·체인 대조까지 보여주지는 않습니다.' },
  // record-only = 코인 이동 없음. 지급(payout) 코드도, 모델이 자기 사용 기록·금액을 보는
  // 화면도 아직 없다. "정산됩니다"는 지키지 못하는 약속이라 지운다.
  // 기록 단위는 **컷이 아니라 잡(상세페이지) 1건**이다 — workers/detail_page_job.py 가
  // `payment_key=f"job:{job_id}"`, `total=int(license_row["unit_price"])` 로 잡 하나에
  // 정산 1건만 남기고, fm_settlements 는 payment_id UNIQUE 라 두 번째 행이 생길 수도 없다.
  // 셀러 과금만 컷 수 비례(`charge = min(len(cut_assets) * per_cut, reserved)`)라
  // "컷마다"로 적으면 모델이 컷 수만큼 곱해 기대하게 된다.
  // 칸 제목은 '사용 기록' 이다 — '사용 기록이 남는다'로 되돌리지 마라. 제목까지 무조건형이면
  // 바로 아래 본문의 예외("체인 기록에 실패한 건은 남지 않고")와 같은 칸 안에서 어긋난다.
  // 앞 세 칸(VC 발급·체인 기록·공개 검증)도 전부 명사구라 형태도 이쪽이 맞다.
  // 'coins' 는 20px 에서 뭉개진다 — 원 하나에 곡선 셋이 겹쳐 알아볼 수 없는 덩어리가 된다
  // (브라우저 실측). 이 칸이 말하는 건 돈이 아니라 **쌓이는 기록**이라 목록이 더 맞기도 하다.
  { icon: 'listOrdered', title: '사용 기록', body: '기록의 단위는 내 얼굴이 쓰인 상세페이지 한 건입니다 — 그 안에서 컷이 몇 장 나오든 사용 1건과 모델 몫 금액이 함께 올라갑니다. 체인 기록에 실패한 건은 남지 않고, 실제 지급 기능도 아직 준비 중입니다.' },
];

/* ctaLabel·onPrimary 는 ModelInfoSection·RegisterSection 과 같은 계약이다 — 같은 버튼이
   파일마다 다른 prop 이름을 갖지 않게. 이 섹션이 자기 라우트(/licensing)를 갖게 되면서
   페이지 끝에 전환 지점이 필요해졌다. prop 이 없으면 버튼을 그리지 않는다(눌러도 아무
   일 없는 버튼이 더 나쁘다). */
export function LicensingSection({ ctaLabel, onPrimary }) {
  return (
    <section className={s.section} id="licensing">
      <p className={s.eyebrow}>라이선싱</p>
      <h2 className={s.sectionTitle}>내 얼굴, 내가 정한 조건으로만</h2>
      {/* "그 문서를 누구나 확인" 이 아니라 "그 조건을" 이다 — 공개되는 건 VC 문서 자체가
          아니라 발급 시점 조건과 유효 여부뿐이다(verify 화이트리스트 응답). */}
      <p className={s.sectionLead}>
        얼굴을 넘기는 게 아니라 조건을 붙여 빌려주는 겁니다.
        무엇을 허용했는지가 기록으로 남고, 그 조건을 누구나 확인할 수 있습니다.
      </p>

      <div className={s.cardGrid}>
        {CARDS.map((card) => (
          <article className={s.card} key={card.title}>
            <Icon name={card.icon} size={22} stroke={1.7} />
            <h3 className={s.cardTitle}>{card.title}</h3>
            <p className={s.cardBody}>{card.body}</p>
          </article>
        ))}
      </div>

      <div className={s.record}>
        <div className={s.recordHead}>
          <h3 className={s.recordTitle}>확인할 수 있는 기록으로 남습니다</h3>
          {/* 두 문장의 확실성이 서로 다르다. 앞 문장(발급된 라이선스 = 서명된 자격증명,
              조건은 fm_licenses + VC 로 남는다)은 무조건 참이고, 사용 기록은 체인 기록이
              성공한 건에 한한다. "사용될 때마다 기록이 쌓입니다"로 되돌리지 마라 —
              파일 상단 best-effort 항목 참고. */}
          <p className={s.recordLead}>
            발급된 라이선스는 서명된 자격증명이 되고, 사용 기록은 체인에 올립니다.
            나중에 조건을 두고 다툴 일이 생겨도 무엇을 허용했는지가 남아 있습니다.
          </p>
        </div>
        <ol className={s.recordSteps}>
          {RECORD.map((step, index) => (
            <li className={s.recordStep} key={step.title}>
              <span className={s.recordNumber}>{String(index + 1).padStart(2, '0')}</span>
              <Icon name={step.icon} size={20} stroke={1.7} />
              <h4 className={s.recordStepTitle}>{step.title}</h4>
              <p className={s.recordStepBody}>{step.body}</p>
            </li>
          ))}
        </ol>
      </div>

      {ctaLabel && onPrimary ? (
        // 여백 규칙은 ModelInfoSection 의 CTA 와 같다(앞 요소가 자기 아래 여백을 갖는
        // 스타일시트인데 .record 에는 그 여백이 없다). 클래스가 생기면 둘 다 옮겨라.
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
