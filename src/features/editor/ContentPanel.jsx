/* =============================================================
   features/editor/ContentPanel.jsx — 정보 프리셋 (PRD §10.14 `내용 추가`)
   프레임 탭 안에 통합 렌더된다(별도 탭 없음). 사진이 필요 없는 표·글·안내를
   골라 정보 블록으로 넣는다. 목록은 중요도 순서(반드시 확인 → 판매에 도움 →
   필요할 때 추가) 단일 리스트로 전원 노출하고, targetGenders 는 '추천' 배지에만
   쓴다(UI 분기 금지). 상단에는 작은 스타일 토글(브랜드형/소호형) + 일괄 삽입.
   각 항목은 프레임 카드와 같은 문법의 스키매틱 썸네일로 내용을 식별한다.
   ============================================================= */
import { INFO_PRESET_TYPES } from '@/features/editor/presets/infoPresets.js';

const TIERS = [
  { id: 'must', label: '반드시 확인' },
  { id: 'boost', label: '판매에 도움' },
  { id: 'extra', label: '필요할 때 추가' },
];

/* 타입별 스키매틱 썸네일 — 프레임 미리보기(.frame-prev)와 같은 톤의 미니 도식.
   실제 블록 레이아웃의 축약이라 목록에서 무엇이 만들어질지 바로 보인다. */
function PresetThumb({ type }) {
  const G = '#b9b9be'; const D = '#6f6f76'; const F = '#e7e7ea';
  const svg = (children) => (
    <svg viewBox="0 0 100 40" style={{ width: '100%', height: '100%', display: 'block' }}>{children}</svg>
  );
  switch (type) {
    case 'size_table': return svg(<>
      <rect x="8" y="6" width="84" height="8" rx="1.5" fill={F} />
      {[16, 24, 32].map((y) => <line key={y} x1="8" y1={y} x2="92" y2={y} stroke={G} strokeWidth="0.8" />)}
      {[30, 52, 74].map((x) => <line key={x} x1={x} y1="6" x2={x} y2="34" stroke={F} strokeWidth="1" />)}
      <rect x="10" y="8" width="14" height="4" rx="1" fill={D} />
    </>);
    case 'required_notice': return svg(<>
      {[8, 15, 22, 29].map((y) => <g key={y}>
        <rect x="8" y={y} width="20" height="3.5" rx="1" fill={D} opacity=".55" />
        <rect x="34" y={y} width="58" height="3.5" rx="1" fill={F} stroke={G} strokeWidth="0.3" />
      </g>)}
    </>);
    case 'care': return svg(<>
      <rect x="8" y="7" width="26" height="5" rx="1.5" fill={D} />
      {[17, 24, 31].map((y) => <g key={y}>
        <circle cx="11" cy={y + 1.6} r="1.4" fill={G} />
        <rect x="16" y={y} width={76 - (y - 17)} height="3.2" rx="1" fill={F} />
      </g>)}
    </>);
    case 'policy': return svg(<>
      <rect x="8" y="6" width="22" height="4.5" rx="1" fill={D} />
      <rect x="8" y="13" width="84" height="3" rx="1" fill={F} />
      <rect x="8" y="18" width="70" height="3" rx="1" fill={F} />
      <rect x="8" y="26" width="22" height="4.5" rx="1" fill={D} opacity=".7" />
      <rect x="8" y="33" width="78" height="3" rx="1" fill={F} />
    </>);
    case 'header': return svg(<>
      <rect x="30" y="8" width="40" height="3" rx="1" fill={G} />
      <rect x="20" y="15" width="60" height="7" rx="1.5" fill={D} />
      <rect x="34" y="26" width="32" height="3" rx="1" fill={F} stroke={G} strokeWidth="0.3" />
      <line x1="44" y1="34" x2="56" y2="34" stroke={D} strokeWidth="1" />
    </>);
    case 'feature_icons': return svg(<>
      <rect x="8" y="4" width="26" height="3" rx="1" fill={D} />
      <rect x="8" y="10" width="84" height="16" rx="1.5" fill={F} stroke={G} strokeWidth="0.6" />
      <rect x="8" y="29" width="30" height="3.5" rx="1" fill={D} opacity=".7" />
      <rect x="8" y="35" width="72" height="2.6" rx="1" fill={F} stroke={G} strokeWidth="0.3" />
    </>);
    case 'fit_guide': return svg(<>
      {[10, 32, 54, 76].map((x, i) => <g key={x}>
        <rect x={x} y="8" width="16" height="20" rx="2.5" fill={i === 1 ? D : F} />
        <rect x={x + 3} y="31" width="10" height="2.6" rx="1" fill={G} />
      </g>)}
    </>);
    case 'size_matrix': return svg(<>
      <rect x="8" y="6" width="84" height="7" rx="1.5" fill={F} />
      {[13, 19.5, 26, 32.5].map((y) => <line key={y} x1="8" y1={y} x2="92" y2={y} stroke={G} strokeWidth="0.6" />)}
      {[26, 44, 62, 80].map((x) => <line key={x} x1={x} y1="6" x2={x} y2="32.5" stroke={F} strokeWidth="1" />)}
      {[[32, 15.5], [50, 15.5], [36, 22], [54, 22], [68, 28]].map(([x, y], i) => <rect key={i} x={x} y={y} width="6" height="2.6" rx="1" fill={D} opacity=".55" />)}
    </>);
    case 'model_info': return svg(<>
      <rect x="34" y="5" width="32" height="3.5" rx="1" fill={D} />
      {[12, 40, 68].map((x) => <g key={x}>
        <rect x={x} y="12" width="20" height="22" rx="2.5" fill={F} />
        <rect x={x + 4} y="17" width="12" height="2.8" rx="1" fill={D} opacity=".7" />
        <rect x={x + 3} y="22" width="14" height="2.2" rx="1" fill={G} />
        <rect x={x + 5} y="26" width="10" height="2.2" rx="1" fill={G} opacity=".7" />
      </g>)}
    </>);
    default: return svg(<rect x="8" y="8" width="84" height="24" rx="2" fill={F} />);
  }
}

export function ContentPanel({ recommendGender, onPick }) {
  return (
    <div>
      <div className="lbl" style={{ marginBottom: 6 }}>내용 추가</div>
      <p className="panel-sub" style={{ marginBottom: 12 }}>
        표·글·안내처럼 사진이 필요 없는 내용을 블록으로 넣어요.
        기본 구성(공지·헤더·특징·사이즈표·케어·고시)은 상세페이지 생성 때 자동으로 깔려요.
      </p>

      {TIERS.map((tier) => {
        const items = INFO_PRESET_TYPES.filter((p) => p.tier === tier.id);
        if (!items.length) return null;
        return (
          <div key={tier.id} style={{ marginBottom: 14 }}>
            <div className="lbl" style={{ marginBottom: 8 }}>{tier.label}</div>
            <div className="frame-list">
              {items.map((p) => {
                const rec = recommendGender && p.recommend === recommendGender;
                return (
                  <div key={p.type} className="frame-item" style={{ cursor: 'pointer' }} onClick={() => onPick(p.type)} title={p.desc}>
                    <div className="frame-prev" style={{ display: 'block', padding: 4 }}>
                      <PresetThumb type={p.type} />
                    </div>
                    <div className="fl" style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                      {p.label}
                      {rec && <i style={{ fontStyle: 'normal', fontSize: 10, fontWeight: 600, color: '#4f88c9', background: '#eef4fb', borderRadius: 5, padding: '1px 5px' }}>추천</i>}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
      <p className="hint">이미 넣은 블록은 블록 위 연필 버튼으로 내용을 다시 수정할 수 있어요.</p>
    </div>
  );
}

export default ContentPanel;
