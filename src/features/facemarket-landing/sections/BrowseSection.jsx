/* =============================================================
   모델 둘러보기 — /models 의 본문. 카드 격자 + 상세 창.

   레퍼런스(사용자 스크린샷)는 검은 바탕의 모델 에이전시 로스터인데, 배치만 가져오고 색은
   이 사이트 것을 쓴다 — 랜딩·라이선스·등록이 전부 밝은 화면이라 여기만 검으면 다른 서비스로
   읽힌다.

   목록 = **실제 등록 모델(앞)** + 가상 예시(뒤). 실모델은 GET /v1/facemarket/public/models 에서
   온다 — 모델이 /model/confirm 에서 확대샷·전신샷을 고르고 공개에 동의한 순간부터 여기 선다
   (2026-09-07 오너 지시: "확정하면 모델 리스트에 정식 등록"). 서버가 죽어 있어도 예시는 그대로
   보인다 — 공개 페이지가 서버 사정으로 텅 비면 안 되므로 실패는 조용히 삼킨다.

   카드 아래에는 이름(주)·키·체형(보조; 예시는 키·몸무게)이 항상 보이고, 마우스를 올리면 **사진
   안쪽 아래**에서 검정 pill "모델 정보 확인"이 살짝 떠오른다(2026-09-04 오너 지시). **버튼은 hover
   로만 존재하지 않는다**: 카드 자체가 button 이라 터치·키보드로도 같은 창이 열린다.
   ============================================================= */
import { useEffect, useState } from 'react';
import { Icon } from '@/components/ui.jsx';
import { BROWSE_MODELS } from '../data/browseModels.js';
import { fetchPublicModels, fromExampleModel } from '../data/publicModels.js';
import { ModelDetailDialog } from './ModelDetailDialog.jsx';
import s from '../BrowseModels.module.css';

const EXAMPLE_MODELS = Object.freeze(BROWSE_MODELS.map(fromExampleModel));

export function BrowseSection() {
  const [openId, setOpenId] = useState(null);
  const [realModels, setRealModels] = useState([]);

  useEffect(() => {
    const controller = new AbortController();
    fetchPublicModels({ signal: controller.signal })
      .then((items) => { if (!controller.signal.aborted) setRealModels(items); })
      .catch(() => { /* 실패해도 예시 카드는 남는다 — 위 머리말 */ });
    return () => controller.abort();
  }, []);

  const hasReal = realModels.length > 0;
  const models = hasReal ? [...realModels, ...EXAMPLE_MODELS] : EXAMPLE_MODELS;
  const openModel = models.find((model) => model.id === openId) || null;

  return (
    <section aria-labelledby="fm-browse-title" className={s.browse}>
      <header className={s.browseHead}>
        <span className={s.eyebrow}>모델 둘러보기</span>
        <h1 className={s.browseTitle} id="fm-browse-title">등록된 얼굴을 조건과 함께 봅니다</h1>
        {/* 고지는 제목 바로 밑이다 — 격자보다 위에 있어야 사진을 보기 전에 읽힌다.
            카드마다 '예시' 배지가 또 붙는 건 이미지만 잘려 공유되는 경우 때문이다.
            실모델이 하나라도 서면 "전부 예시"는 거짓이 되므로 문구를 바꾼다. */}
        <p className={s.browseNotice}>
          <Icon name="info" size={14} stroke={2} />
          {hasReal
            ? '‘예시’ 표시가 있는 카드는 가상 모델이에요. 그 카드의 이름·키·몸무게·라이선스 조건도 예시 값이에요.'
            : '아래는 전부 가상 모델 예시입니다. 이름·키·몸무게·라이선스 조건도 예시 값이고, 실제 등록된 모델이 아닙니다.'}
        </p>
      </header>

      <ul className={s.grid}>
        {models.map((model) => (
          <li className={s.card} key={`${model.kind}-${model.id}`}>
            <button
              aria-label={`${model.name} 모델 정보 보기`}
              className={s.cardMedia}
              onClick={() => setOpenId(model.id)}
              type="button"
            >
              <img alt={model.alt} className={s.cardImage} loading="lazy" src={model.closeup} />
              {model.kind === 'example' && <span aria-hidden="true" className={s.cardBadge}>예시</span>}
              {/* hover·focus 에서 사진 안쪽 아래로 떠오르는 검정 pill. aria-hidden 인 이유는 위
                  aria-label 이 같은 말을 이미 하고 있어서다 — 안 그러면 "모델 정보"를 두 번 읽는다. */}
              <span aria-hidden="true" className={s.cardPill}>
                모델 정보 확인
                <Icon name="arrowRight" size={14} stroke={2} />
              </span>
            </button>
            <div className={s.cardMeta}>
              <p className={s.cardName}>{model.name}</p>
              {model.spec && <p className={s.cardSpec}>{model.spec}</p>}
            </div>
          </li>
        ))}
      </ul>

      {openModel && <ModelDetailDialog model={openModel} onClose={() => setOpenId(null)} />}
    </section>
  );
}
