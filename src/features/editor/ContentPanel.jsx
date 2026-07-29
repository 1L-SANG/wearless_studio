/* =============================================================
   features/editor/ContentPanel.jsx — '내용' 탭 (PRD §10.14 `내용 추가`)
   사진이 필요 없는 표·글·안내를 목록에서 골라 정보 블록으로 넣는다.
   목록은 중요도 순서(반드시 확인 → 판매에 도움 → 필요할 때 추가) 단일
   리스트로 전원 노출하고, targetGenders 는 '추천' 배지에만 쓴다(UI 분기 금지).
   상단에는 작은 스타일 토글(브랜드형/소호형) + 정보 템플릿 일괄 삽입 버튼.
   ============================================================= */
import { Icon, Button } from '@/components/ui.jsx';
import { INFO_PRESET_TYPES, INFO_TEMPLATES } from '@/features/editor/presets/infoPresets.js';

const TIERS = [
  { id: 'must', label: '반드시 확인' },
  { id: 'boost', label: '판매에 도움' },
  { id: 'extra', label: '필요할 때 추가' },
];

export function ContentPanel({ recommendGender, templateStyle, onTemplateStyle, onApplyTemplate, onPick }) {
  return (
    <div>
      <p className="panel-sub" style={{ marginBottom: 14 }}>표·글·안내처럼 사진이 필요 없는 내용을 블록으로 넣어요.</p>

      {/* 정보 템플릿 — 작은 토글 + 일괄 삽입. 컷 블록은 건드리지 않는다. */}
      <div style={{ border: '1px solid #e5e5e3', borderRadius: 12, padding: 14, marginBottom: 18, background: '#fafafa' }}>
        <div className="lbl" style={{ marginBottom: 8 }}>정보 템플릿</div>
        <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
          {Object.entries(INFO_TEMPLATES).map(([key, t]) => {
            const on = templateStyle === key;
            return (
              <button key={key} onClick={() => onTemplateStyle(key)}
                style={{ flex: 1, padding: '7px 0', borderRadius: 8, fontSize: 13, fontWeight: on ? 600 : 400, cursor: 'pointer',
                  border: on ? '1.5px solid #0e0d14' : '1px solid #e5e5e3', background: on ? '#0e0d14' : '#fff', color: on ? '#fff' : '#4a4a45' }}>
                {t.label}
              </button>
            );
          })}
        </div>
        <Button variant="primary" size="sm" icon="plus" onClick={onApplyTemplate} style={{ width: '100%' }}>
          템플릿으로 한 번에 추가
        </Button>
        <p className="hint" style={{ marginTop: 8 }}>정보 블록 세트만 넣고, 사진 블록은 그대로 둬요. 이미 있는 항목은 건너뛰어요.</p>
      </div>

      {TIERS.map((tier) => {
        const items = INFO_PRESET_TYPES.filter((p) => p.tier === tier.id);
        if (!items.length) return null;
        return (
          <div key={tier.id} style={{ marginBottom: 16 }}>
            <div className="lbl" style={{ marginBottom: 8 }}>{tier.label}</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {items.map((p) => {
                const rec = recommendGender && p.recommend === recommendGender;
                return (
                  <button key={p.type} onClick={() => onPick(p.type)}
                    style={{ display: 'flex', alignItems: 'center', gap: 10, textAlign: 'left', padding: '10px 12px',
                      border: '1px solid #e5e5e3', borderRadius: 10, background: '#fff', cursor: 'pointer' }}>
                    <span style={{ flexShrink: 0, width: 30, height: 30, borderRadius: 8, background: '#f5f5f5', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                      <Icon name="info" size={15} />
                    </span>
                    <span style={{ flex: 1, minWidth: 0 }}>
                      <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <b style={{ fontSize: 13.5, color: '#0e0d14' }}>{p.label}</b>
                        {rec && <i style={{ fontStyle: 'normal', fontSize: 10.5, fontWeight: 600, color: '#4f88c9', background: '#eef4fb', borderRadius: 5, padding: '1.5px 6px' }}>추천</i>}
                      </span>
                      <span style={{ display: 'block', fontSize: 11.5, color: '#898989', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.desc}</span>
                    </span>
                    <Icon name="plus" size={15} />
                  </button>
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
