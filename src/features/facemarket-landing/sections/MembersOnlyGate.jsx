/* 모델 리스트 회원 전용 안내. /models 에서 로그인하지 않은 사람에게 목록 대신 보인다
   (2026-09-23 오너 결정: 등록된 셀러와 모델만 열람).

   뒤에는 목록 화면을 그대로 깔고 어둡게 덮는다. 사진 칸은 모자이크다. 목록이 거기 있다는
   느낌만 주고 얼굴은 알아볼 수 없게 한다(오너 지시). 배경 카드에는 **공개된 가상 예시 모델만**
   쓴다. 실제 등록 모델 사진을 불러오면 화면에서 모자이크를 해도 원본이 브라우저로 내려오기
   때문이다. 이름도 예시 데이터의 '김○○' 표기 그대로다.

   앞의 안내 카드는 404 화면(components/NotFound)과 같은 순서다. 그림, 머리글, 제목, 설명, 버튼.
   그림은 등록 화면 촬영 가이드와 같은 점토 캐릭터로 만들었다. 생성 프롬프트는
   documents/design-assets/facemarket-clay-members-only-prompt.json 에 있다.

   버튼은 '홈으로 돌아가기'다. '로그인하고 보기'는 로그인만 하면 누구나 본다는 뜻으로 읽혀서
   뺐다(2026-09-23 오너). 회원은 상단바에서 로그인하면 목록을 본다. */
import { useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import { BROWSE_MODELS } from '../data/browseModels.js';
import { fromExampleModel } from '../data/publicModels.js';
import { pricingParts } from '@/lib/facemarketPricing.js';
import b from '../BrowseModels.module.css';
import s from './MembersOnlyGate.module.css';

// 예시 데이터는 여성 10명 뒤에 남성 3명이라 앞에서 자르면 전부 여성이다. 실제 목록처럼 섞는다.
const WOMEN = BROWSE_MODELS.filter((model) => model.gender === 'female');
const MEN = BROWSE_MODELS.filter((model) => model.gender === 'male');
const BACKDROP_MODELS = Object.freeze(
  [WOMEN[0], MEN[0], WOMEN[1], WOMEN[2], MEN[1], WOMEN[3], MEN[2], WOMEN[4]].filter(Boolean).map(fromExampleModel),
);
// 모자이크 칸 수. 카드 비율(3:4)과 같게 둔다. 얼굴이 가로 네댓 칸이라 누군지는 읽히지 않는다.
export const MOSAIC_COLS = 12;
export const MOSAIC_ROWS = 16;

// 사진을 아주 작은 캔버스에 줄여 그리고, CSS 로 픽셀을 키워(image-rendering: pixelated) 모자이크를 만든다.
// 픽셀을 읽지 않으므로 다른 출처 이미지라도 캔버스 오염(taint) 문제가 없다.
export function MosaicImage({ src }) {
  const ref = useRef(null);
  useEffect(() => {
    const canvas = ref.current;
    const context = canvas?.getContext?.('2d');
    if (!context) return undefined;
    const image = new Image();
    image.decoding = 'async';
    image.onload = () => {
      const scale = Math.max(canvas.width / image.naturalWidth, canvas.height / image.naturalHeight);
      const width = image.naturalWidth * scale;
      const height = image.naturalHeight * scale;
      context.drawImage(image, (canvas.width - width) / 2, (canvas.height - height) / 2, width, height);
    };
    image.src = src;
    return () => { image.onload = null; };
  }, [src]);
  return <canvas className={s.mosaic} height={MOSAIC_ROWS} ref={ref} width={MOSAIC_COLS} />;
}

// 이름 칸도 같은 방식이다. 글자를 큰 캔버스에 쓴 뒤 10×3 칸으로 줄여 옮기고 CSS 로 키운다.
// 글자 자체는 DOM 에 두지 않는다. 화면을 긁어도 이름이 나오지 않게 하려고서다.
export const NAME_MOSAIC_COLS = 10;
export const NAME_MOSAIC_ROWS = 3;
export function MosaicText({ text }) {
  const ref = useRef(null);
  useEffect(() => {
    const canvas = ref.current;
    const context = canvas?.getContext?.('2d');
    if (!context || typeof document === 'undefined') return;
    const scratch = document.createElement('canvas');
    scratch.width = NAME_MOSAIC_COLS * 12;
    scratch.height = NAME_MOSAIC_ROWS * 12;
    const draw = scratch.getContext('2d');
    if (!draw) return;
    draw.fillStyle = getComputedStyle(canvas).color;
    draw.font = `600 ${scratch.height * 0.8}px sans-serif`;
    draw.textBaseline = 'middle';
    draw.fillText(text, 0, scratch.height / 2, scratch.width);
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.drawImage(scratch, 0, 0, canvas.width, canvas.height);
  }, [text]);
  return <canvas className={s.mosaicText} height={NAME_MOSAIC_ROWS} ref={ref} width={NAME_MOSAIC_COLS} />;
}

export function ListBackdrop() {
  const price = pricingParts();
  return (
    <div aria-hidden="true" className={s.listBehind} inert="">
      <header className={b.browseHead}>
        <p className={b.browseTitle}>등록 모델 리스트</p>
        <p className={b.browseLead}>본인확인과 라이선스 발급을 마친 모델만 올라와요.</p>
        <p className={b.browsePricing}>
          <span className={b.browsePricingLabel}>{price.label}</span>
          <b>{price.perCut}</b>
          <span className={b.browsePricingDim}>/</span>
          <b>{price.monthly}</b>
          <span className={b.browsePricingDim}>({price.cap})</span>
        </p>
      </header>
      <div className={b.browseBar}>
        <div className={b.tabs}>
          <span className={`${b.tab} ${b.tabActive}`}>전체</span>
          <span className={b.tab}>여성</span>
          <span className={b.tab}>남성</span>
        </div>
      </div>
      <ul className={b.grid}>
        {BACKDROP_MODELS.map((model) => (
          <li className={b.card} key={model.id}>
            <div className={`${b.cardMedia} ${s.mediaStill}`}><MosaicImage src={model.closeup} /></div>
            <div className={b.cardMeta}>
              <div className={b.cardName}><MosaicText text={model.name} /></div>
              {model.gender && <p className={b.cardSpec}>{model.gender}</p>}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function MembersOnlyGate() {
  return (
    <section aria-labelledby="fm-members-only-title" className={`${b.browse} ${s.stage}`}>
      <ListBackdrop />
      <div aria-hidden="true" className={s.scrim} />
      <div className={s.front}>
        <div className={s.panel}>
          <img
            alt=""
            className={s.art}
            decoding="async"
            height="720"
            src="/assets/facemarket/models-members-only.webp"
            width="720"
          />
          <p className={s.eyebrow}>MEMBERS ONLY</p>
          <h1 className={s.title} id="fm-members-only-title">모델 리스트는 회원만 볼 수 있어요</h1>
          <p className={s.desc}>
            등록된 셀러와 모델만 열람할 수 있어요.<br />
            모델로 활동하고 싶다면 지원부터 시작해 보세요.
          </p>
          <div className={s.actions}>
            <Link className="btn btn-primary btn-block" to="/">홈으로 돌아가기</Link>
            <Link className={s.secondary} to="/apply">모델로 지원하기</Link>
          </div>
        </div>
      </div>
    </section>
  );
}
