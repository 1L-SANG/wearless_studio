/* =============================================================
   모델 둘러보기 — /models 의 본문. 제목 + 성별 탭 + 카드 격자 + 상세 창.

   2026-09-08 오너 확정:
     제목 "등록 모델 리스트"(작게), 그 아래 설명 한 줄. 위 눈썹 글자 없음.
     탭은 전체·여성·남성뿐. 정렬 없음.
     모델마다 흰 카드 하나: 사진, 이름 + 성별·나이대, "1건 10,000원 / 1개월 25,000원". 품목·검증 문구 없음.
     바탕은 흰색(회청 폐기), 상단바는 검정(LandingHeader 쪽 CSS).

   목록 = **실제 등록 모델(앞)** + 가상 예시(뒤). 실모델은 GET /v1/facemarket/public/models 에서
   온다 — 모델이 /model/confirm 에서 확대샷·전신샷을 고르고 공개에 동의한 순간부터 여기 선다.
   서버가 죽어 있어도 예시는 그대로 보인다 — 공개 페이지가 서버 사정으로 텅 비면 안 되므로
   실패는 조용히 삼킨다.

   카드 자체가 button 이라 터치·키보드로도 같은 창이 열린다. hover 로만 만들면 폰에서 상세를
   볼 길이 사라진다.
   ============================================================= */
import { useEffect, useState } from 'react';
import { Icon } from '@/components/ui.jsx';
import { BROWSE_MODELS } from '../data/browseModels.js';
import { fetchPublicModels, fromExampleModel } from '../data/publicModels.js';
import { ModelDetailDialog } from './ModelDetailDialog.jsx';
import s from '../BrowseModels.module.css';

const EXAMPLE_MODELS = Object.freeze(BROWSE_MODELS.map(fromExampleModel));
const TABS = [
  { key: 'all', label: '전체' },
  { key: '여성', label: '여성' },
  { key: '남성', label: '남성' },
];
const won = (n) => `${Number(n).toLocaleString('ko-KR')}원`;

export function BrowseSection() {
  const [openId, setOpenId] = useState(null);
  const [realModels, setRealModels] = useState([]);
  const [tab, setTab] = useState('all');

  useEffect(() => {
    const controller = new AbortController();
    fetchPublicModels({ signal: controller.signal })
      .then((items) => { if (!controller.signal.aborted) setRealModels(items); })
      .catch(() => { /* 실패해도 예시 카드는 남는다 — 위 머리말 */ });
    return () => controller.abort();
  }, []);

  const hasReal = realModels.length > 0;
  const all = hasReal ? [...realModels, ...EXAMPLE_MODELS] : EXAMPLE_MODELS;
  const models = tab === 'all' ? all : all.filter((model) => model.gender === tab);
  const openModel = all.find((model) => model.id === openId) || null;

  return (
    <section aria-labelledby="fm-browse-title" className={s.browse}>
      <header className={s.browseHead}>
        <h1 className={s.browseTitle} id="fm-browse-title">등록 모델 리스트</h1>
        <p className={s.browseLead}>본인확인과 라이선스 발급을 마친 모델만 올라와요. 사용 조건은 모델이 직접 정했어요.</p>
      </header>

      <div className={s.browseBar}>
        <div aria-label="성별로 보기" className={s.tabs} role="tablist">
          {TABS.map((item) => (
            <button
              aria-selected={tab === item.key}
              className={`${s.tab}${tab === item.key ? ` ${s.tabActive}` : ''}`}
              key={item.key}
              onClick={() => setTab(item.key)}
              role="tab"
              type="button"
            >
              {item.label}
            </button>
          ))}
        </div>
        <span className={s.browseCount}>{models.length}명</span>
      </div>

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
            <div className={s.cardBody}>
              <div className={s.cardRow}>
                <p className={s.cardName}>{model.name}</p>
                {(model.gender || model.ageBand) && (
                  <p className={s.cardMeta}>{[model.gender, model.ageBand].filter(Boolean).join(' · ')}</p>
                )}
              </div>
              {model.license?.unitPrice != null && (
                <p className={s.cardPrice}>
                  <b>1건 {won(model.license.unitPrice)}</b>
                  {model.license.monthlyPrice != null && <span> / 1개월 {won(model.license.monthlyPrice)}</span>}
                </p>
              )}
            </div>
          </li>
        ))}
      </ul>

      {/* 고지는 격자 아래. 실모델이 하나라도 서면 "전부 예시"는 거짓이 되므로 문구를 바꾼다. */}
      <p className={s.browseNotice}>
        <Icon name="info" size={14} stroke={2} />
        {hasReal
          ? '‘예시’ 표시가 있는 카드는 가상 모델이에요. 그 카드의 이름·조건도 예시 값이에요.'
          : '아래는 전부 가상 모델 예시입니다. 이름·조건도 예시 값이고, 실제 등록된 모델이 아닙니다.'}
      </p>

      {openModel && <ModelDetailDialog model={openModel} onClose={() => setOpenId(null)} />}
    </section>
  );
}
