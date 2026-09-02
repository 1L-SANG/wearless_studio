/* =============================================================
   모델 둘러보기 — /models 의 본문. 카드 격자 + 상세 창.

   레퍼런스(사용자 스크린샷)는 검은 바탕의 모델 에이전시 로스터인데, 배치만 가져오고 색은
   이 사이트 것을 쓴다 — 랜딩·라이선스·등록이 전부 밝은 화면이라 여기만 검으면 다른 서비스로
   읽힌다.

   카드 위에 마우스를 올리면 '모델 정보 보기'가 뜨지만, **버튼은 hover 로만 존재하지 않는다.**
   카드 자체가 button 이라 터치·키보드로도 같은 창이 열린다. hover 로만 만들면 폰에서
   상세를 볼 길이 사라진다.
   ============================================================= */
import { useState } from 'react';
import { Icon } from '@/components/ui.jsx';
import { BROWSE_MODELS } from '../data/browseModels.js';
import { ModelDetailDialog } from './ModelDetailDialog.jsx';
import s from '../BrowseModels.module.css';

export function BrowseSection() {
  const [openId, setOpenId] = useState(null);
  const openModel = BROWSE_MODELS.find((model) => model.id === openId) || null;

  return (
    <section aria-labelledby="fm-browse-title" className={s.browse}>
      <header className={s.browseHead}>
        <span className={s.eyebrow}>모델 둘러보기</span>
        <h1 className={s.browseTitle} id="fm-browse-title">등록된 얼굴을 조건과 함께 봅니다</h1>
        {/* 고지는 제목 바로 밑이다 — 격자보다 위에 있어야 사진을 보기 전에 읽힌다.
            카드마다 '예시' 배지가 또 붙는 건 이미지만 잘려 공유되는 경우 때문이다. */}
        <p className={s.browseNotice}>
          <Icon name="info" size={14} stroke={2} />
          아래는 전부 가상 모델 예시입니다. 이름·키·몸무게·라이선스 조건도 예시 값이고,
          실제 등록된 모델이 아닙니다.
        </p>
      </header>

      <ul className={s.grid}>
        {BROWSE_MODELS.map((model) => (
          <li className={s.card} key={model.id}>
            <button
              aria-label={`${model.name} 모델 정보 보기`}
              className={s.cardMedia}
              onClick={() => setOpenId(model.id)}
              type="button"
            >
              <img alt={model.alt} className={s.cardImage} src={model.portrait} />
              <span aria-hidden="true" className={s.cardBadge}>예시</span>
              {/* hover·focus 에서 떠오르는 덮개. aria-hidden 인 이유는 위 aria-label 이
                  같은 말을 이미 하고 있어서다 — 안 그러면 "모델 정보 보기"를 두 번 읽는다. */}
              <span aria-hidden="true" className={s.cardOverlay}>
                <span className={s.cardOverlayButton}>
                  모델 정보 보기
                  <Icon name="arrowRight" size={15} stroke={2} />
                </span>
              </span>
            </button>
            <div className={s.cardMeta}>
              <p className={s.cardName}>{model.name}</p>
              <p className={s.cardSpec}>{model.height}cm · {model.weight}kg</p>
            </div>
          </li>
        ))}
      </ul>

      {openModel && <ModelDetailDialog model={openModel} onClose={() => setOpenId(null)} />}
    </section>
  );
}
