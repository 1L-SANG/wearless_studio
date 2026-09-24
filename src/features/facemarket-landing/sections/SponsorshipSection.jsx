import { FACEMARKET_PRICING } from '@/lib/facemarketPricing.js';
import { MODEL_SHARE, formatKrw } from '../facemarketTerms.js';
import s from './SponsorshipSection.module.css';

/* 모델 활동 섹션. 홈 첫 화면(캐러셀) 바로 아래에 있다. 한 번 등록하면 하는 두 가지 활동을 보여 준다.

   2026-09-24 오너 결정 모음
   - 위 섹션과 구분되게 화면 가로 끝까지 흰 띠를 깐다(위는 연한 파란 배경).
   - 첫 활동 이름은 '의류 쇼핑몰 상세페이지에 모델로서 활용', 협찬에는 '(선택)'을 달지 않는다.
   - '촬영'은 '등록된 내 얼굴이 자동으로 활용돼요', '관리'는 '등록만 해 두면 수익이 자동으로 들어와요'.
     ('관리'는 원래 '범위'였다. 어떤 옷에 쓸지 내가 정한다는 내용은 아래 RightsSection 제목에 있다.)
   - '안전'은 외부 노출과 추적에 초점을 둔다. 근거: 내려받는 사진 파일에 모델·라이선스·확인 주소가 담긴
     출처 정보(C2PA)가 들어가고, 공개 확인 페이지(/verify/p/:id)가 있으며, 원본 사진은 공개 주소가 없다.
     이 기능이 바뀌면 문구도 같이 고친다.
   - '밋밋하다'는 피드백으로 카드 상자를 없애고, 활동마다 큰 무대 위에 실제 화면 모형을 띄운다.
     01 은 쇼핑몰 상세페이지 모형 + 정산 알림, 02 는 인스타그램 게시물 모형 + 협찬 도착 알림이다.
     게시물 프로필 사진도 01 과 같은 모델이라, 한 사람이 두 활동을 한다는 게 보인다.
     무대는 꾸밈이라 화면 읽기에서 빼고(aria-hidden), 같은 내용은 본문 글에 다 있다.
     무대 안 '예시 화면' 표시는 오너 요청으로 뺐다.
   - 모형 안 가격(상세페이지)과 '광고 |' 머리말(게시물)은 오너 요청으로 뺐다. 게시물의 '협찬' 표시는 남긴다.
   - 항목은 한 줄 글머리표다. 이름표(등록, 촬영 등) 옆에 내용을 이어 쓰고, 보조 설명은 두지 않는다.
     결제 금액의 70% 라는 근거는 바로 아래 RightsSection 과 FAQ 가 보여 준다.

   무대 사진: 01 은 오너가 준 실제 모델 착용컷(data/demoCuts.js 와 같은 파일)이다. 02 는 모델이 입지 않은
   협찬 예시 상품이다. 가상 모델 사진은 '실제 모델' 서비스라 협찬 예시에 쓰지 않는다.
   말덩이 묶음(.keep)은 휴대폰에서 줄이 말 사이에서만 바뀌게 한다(CSS 설명 참고).
   id="sponsorship" 은 상단바 '협찬 안내' 링크의 목적지라 바꾸지 않는다. */

/* 알림 아이콘 두 개는 ai.wearless.kr 계정 메뉴 아이콘(shell/ProfileMenuIcon.jsx)과 같은 결로 그렸다.
   채운 파스텔 모양에 같은 색을 어둡게 섞은 디테일. 정산은 메뉴의 '크레딧 사용 내역' 동전 더미와 같은 모양,
   협찬 도착은 택배 상자에 파란 확인 표시를 단 모양이다(2026-09-24 오너 선택: 정산 1, 협찬 6). */
function PayoutIcon() {
  return (
    <svg aria-hidden="true" className={`${s.toastIcon} ${s.iconAmber}`} viewBox="0 0 24 24">
      <path d="M10 6c0-2.2 2.7-4 6-4s6 1.8 6 4v11c0 2.2-2.7 4-6 4s-6-1.8-6-4Z" />
      <path className={s.iconLine} d="M12 7c2.2 1.3 5.8 1.3 8 0M12 12c2.2 1.3 5.8 1.3 8 0" />
      <path d="M2 13c0-2.2 2.7-4 6-4s6 1.8 6 4v6c0 2.2-2.7 4-6 4s-6-1.8-6-4Z" />
      <path className={s.iconLine} d="M4 13c2.2 1.3 5.8 1.3 8 0M4 18c2.2 1.3 5.8 1.3 8 0" />
    </svg>
  );
}

function ArrivedIcon() {
  return (
    <svg aria-hidden="true" className={s.toastIcon} viewBox="0 0 24 24">
      <g className={s.iconAmber}>
        <path d="M3 9.6h14v9.4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" />
        <rect height="4.8" rx="1.4" width="16.4" x="1.8" y="5.3" />
        <path className={s.iconSeam} d="M3.3 10.8h13.4" />
        <path className={s.iconDetail} d="M8.6 5.3h2.8v8.6l-1.4-.9-1.4.9Z" />
      </g>
      <g className={s.iconBlue}>
        <circle className={s.iconCutout} cx="18" cy="17" r="5.9" />
        <circle cx="18" cy="17" r="4.8" />
        <path className={s.iconCheck} d="m15.9 17.1 1.4 1.4 2.8-2.9" />
      </g>
    </svg>
  );
}

function Heart() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24">
      <path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z" />
    </svg>
  );
}

export function SponsorshipSection() {
  const perSetShare = FACEMARKET_PRICING.perCut * MODEL_SHARE;
  return (
    <section aria-labelledby="fm-sponsorship-title" className={s.section} id="sponsorship">
      <header className={s.head}>
        <p className={s.eyebrow}>모델 활동</p>
        <h2 className={s.title} id="fm-sponsorship-title">한 번 등록하면, <span className={s.keep}>두 가지로 활동해요</span></h2>
        <p className={s.lead}>상세페이지 모델로 쓰이면서, <span className={s.keep}>원하면 옷 협찬도 함께 받을 수 있어요.</span></p>
      </header>

      <div className={s.roles}>
        <article className={s.role}>
          <div aria-hidden="true" className={s.stage}>
            <div className={`${s.device} ${s.devicePdp}`}>
              <div className={s.pdp}>
                <div className={s.pdpMedia}><img alt="" decoding="async" loading="lazy" src="/models/demo/cut1.webp" /></div>
                <div className={s.pdpInfo}>
                  <span className={s.pdpShop}>DAILY STUDIO</span>
                  <span className={s.pdpName}>오버핏 울 블루종</span>
                </div>
              </div>
              <div className={`${s.toast} ${s.toastRight}`}>
                <PayoutIcon />
                <span className={s.toastText}>
                  <small>스튜디오컷 세트 1건 정산</small>
                  <strong>+{formatKrw(perSetShare)}</strong>
                </span>
              </div>
            </div>
          </div>

          <div className={s.body}>
            <span aria-hidden="true" className={s.index}>01</span>
            <h3 className={s.roleTitle}>의류 쇼핑몰 상세페이지에 <span className={s.keep}>모델로서 활용</span></h3>
            <p className={s.summary}>쇼핑몰 셀러가 내 얼굴로 <span className={s.keep}>AI 모델 사진을</span> 만들어, <span className={s.keep}>상품 상세페이지에 써요.</span></p>
            <ul className={s.points}>
              <li className={s.point}>
                <span className={s.pointLabel}>등록</span>
                <span className={s.pointText}>본인확인과 사진 18장, <span className={s.keep}>한 번이면 끝나요</span></span>
              </li>
              <li className={s.point}>
                <span className={s.pointLabel}>촬영</span>
                <span className={s.pointText}><span className={s.keep}>등록된 내 얼굴이</span> <span className={s.keep}>자동으로 활용돼요</span></span>
              </li>
              <li className={s.point}>
                <span className={s.pointLabel}>수익</span>
                <span className={s.pointText}>스튜디오컷 세트(3~5장)마다 <span className={s.keep}><strong>{formatKrw(perSetShare)}</strong>이 내 몫이에요</span></span>
              </li>
              <li className={s.point}>
                <span className={s.pointLabel}>관리</span>
                <span className={s.pointText}><span className={s.keep}>등록만 해 두면</span> <span className={s.keep}>수익이 자동으로 들어와요</span></span>
              </li>
              <li className={s.point}>
                <span className={s.pointLabel}>안전</span>
                <span className={s.pointText}>내 얼굴로 만든 사진은 <span className={s.keep}>어디서 쓰이든</span> <span className={s.keep}>추적할 수 있어요</span></span>
              </li>
            </ul>
          </div>
        </article>

        <article className={s.role}>
          <div aria-hidden="true" className={s.stage}>
            <div className={`${s.device} ${s.devicePost}`}>
              <div className={s.post}>
                <div className={s.postHead}>
                  <span className={s.avatar}><img alt="" decoding="async" loading="lazy" src="/models/demo/cut1.webp" /></span>
                  <span className={s.handle}>my.daily.look</span>
                  <span className={s.postBadge}>협찬</span>
                </div>
                <div className={s.postMedia}><img alt="" decoding="async" loading="lazy" src="/assets/sponsorship-knit-ivory-ghost.webp" /></div>
                <div className={s.postActions}><Heart /><span>좋아요 128개</span></div>
                <p className={s.postCaption}>○○몰에서 의류를 무상 제공받아 작성했어요</p>
              </div>
              <div className={`${s.toast} ${s.toastLeft}`}>
                <ArrivedIcon />
                <span className={s.toastText}>
                  <small>협찬 도착 · 아이보리 니트 1벌</small>
                  <strong><del>49,900원</del> 무료</strong>
                </span>
              </div>
            </div>
          </div>

          <div className={s.body}>
            <span aria-hidden="true" className={s.index}>02</span>
            <h3 className={s.roleTitle}>의류 협찬</h3>
            <p className={s.summary}>셀러가 보내준 옷을 입고 찍어서, <span className={s.keep}>내 인스타그램 피드에 올려요.</span> <span className={s.keep}>옷은 그대로 내 것이에요.</span></p>
            <ul className={s.points}>
              <li className={s.point}>
                <span className={s.pointLabel}>게시</span>
                <span className={s.pointText}>착용 사진을 <span className={s.keep}>인스타그램 피드에 1회 올려요</span></span>
              </li>
              <li className={s.point}>
                <span className={s.pointLabel}>기한</span>
                <span className={s.pointText}>옷을 받은 뒤 <span className={s.keep}>3일 이내 올리고,</span> <span className={s.keep}>30일 유지해요</span></span>
              </li>
              <li className={s.point}>
                <span className={s.pointLabel}>참여</span>
                <span className={s.pointText}>등록할 때 켜 두면 되고, <span className={s.keep}>언제든 끌 수 있어요</span></span>
              </li>
            </ul>
          </div>
        </article>
      </div>
    </section>
  );
}
