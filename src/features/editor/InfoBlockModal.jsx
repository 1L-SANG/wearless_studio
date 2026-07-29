/* =============================================================
   features/editor/InfoBlockModal.jsx — 정보 블록 입력 폼 (PRD §10.14)
   프리셋 타입별 폼으로 block.info(정본)를 편집한다. 제출하면 에디터가
   buildInfoBlock 으로 elements 를 통째로 재생성한다(수동 수정 대체).
   ============================================================= */
import { Fragment, useState } from 'react';
import { Button, Icon, IconButton, Modal } from '@/components/ui.jsx';
import { CARE_COPY_LIBRARY, CARE_LABEL_SENTENCE, INFO_PRESET_TYPES, careFamilyFor } from '@/features/editor/presets/infoPresets.js';

const inp = { width: '100%', boxSizing: 'border-box', padding: '8px 10px', border: '1px solid #e5e5e3', borderRadius: 8, fontSize: 14, background: '#fff', color: '#0e0d14' };
const inpSm = { ...inp, padding: '6px 8px', fontSize: 13 };
const rowGap = { display: 'flex', gap: 8, alignItems: 'center' };

function Field({ label, hint, children }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div className="lbl" style={{ marginBottom: 6 }}>{label}</div>
      {children}
      {hint && <p className="hint" style={{ marginTop: 6 }}>{hint}</p>}
    </div>
  );
}

function Chip({ on, children, onClick }) {
  return (
    <button onClick={onClick} style={{ padding: '6px 12px', borderRadius: 999, fontSize: 13, cursor: 'pointer',
      border: on ? '1.5px solid #0e0d14' : '1px solid #e5e5e3', background: on ? '#0e0d14' : '#fff', color: on ? '#fff' : '#4a4a45', fontWeight: on ? 600 : 400 }}>
      {children}
    </button>
  );
}

/* ---------- 타입별 폼 ---------- */

function SizeTableForm({ info, setInfo, ctx }) {
  const schema = (ctx.measurementSchema && ctx.measurementSchema[ctx.clothingType]) || info.columns;
  const labels = ctx.measurementLabels || {};
  const toggleCol = (key) => setInfo((f) => ({ ...f, columns: f.columns.includes(key) ? f.columns.filter((k) => k !== key) : [...schema.filter((k) => f.columns.includes(k) || k === key)] }));
  const setRow = (i, patch) => setInfo((f) => ({ ...f, rows: f.rows.map((r, j) => (j === i ? { ...r, ...patch } : r)) }));
  const setVal = (i, key, v) => setRow(i, { values: { ...info.rows[i].values, [key]: v } });
  return (
    <>
      <Field label="측정 항목" hint="의류 종류에 맞는 항목만 골라요.">
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {schema.map((key) => <Chip key={key} on={info.columns.includes(key)} onClick={() => toggleCol(key)}>{labels[key] || key}</Chip>)}
        </div>
      </Field>
      <Field label="사이즈별 실측 (cm)">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {info.rows.map((row, i) => (
            <div key={i} style={rowGap}>
              <input style={{ ...inpSm, width: 76, flexShrink: 0 }} value={row.label} placeholder="사이즈" onChange={(e) => setRow(i, { label: e.target.value })} />
              {info.columns.map((key) => (
                <input key={key} style={inpSm} type="number" inputMode="decimal" placeholder={labels[key] || key}
                  value={row.values[key] ?? ''} onChange={(e) => setVal(i, key, e.target.value === '' ? null : e.target.value)} />
              ))}
              <IconButton name="trash" size="sm" title="행 삭제" onClick={() => setInfo((f) => ({ ...f, rows: f.rows.filter((_r, j) => j !== i) }))} />
            </div>
          ))}
        </div>
        <Button variant="ghost" size="sm" icon="plus" style={{ marginTop: 8 }}
          onClick={() => setInfo((f) => ({ ...f, rows: [...f.rows, { label: '', values: {} }] }))}>사이즈 추가</Button>
      </Field>
      <Field label="하단 안내 문구">
        <input style={inp} value={info.note} onChange={(e) => setInfo((f) => ({ ...f, note: e.target.value }))} />
      </Field>
      <label style={{ ...rowGap, fontSize: 13.5, color: '#4a4a45', cursor: 'pointer' }}>
        <input type="checkbox" checked={!!info.withDiagram} onChange={(e) => setInfo((f) => ({ ...f, withDiagram: e.target.checked }))} />
        실측 다이어그램 자리 추가 (빈 이미지 칸 — 도식 이미지를 넣을 수 있어요)
      </label>
    </>
  );
}

function NoticeForm({ info, setInfo }) {
  const setField = (i, value) => setInfo((f) => ({ ...f, fields: f.fields.map((x, j) => (j === i ? { ...x, value } : x)) }));
  return (
    <Field label="법정 고시 항목" hint="비워두면 '정보 입력 필요'로 표시돼요 — 판매 전 꼭 채워주세요.">
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {info.fields.map((f, i) => (
          <div key={f.key} style={rowGap}>
            <span style={{ width: 190, flexShrink: 0, fontSize: 13, color: '#4a4a45' }}>{f.label}</span>
            <input style={inpSm} value={f.value || ''} onChange={(e) => setField(i, e.target.value)} />
          </div>
        ))}
      </div>
    </Field>
  );
}

function CareForm({ info, setInfo, ctx }) {
  const pickFamily = (family) => setInfo((f) => ({ ...f, family, text: [...CARE_COPY_LIBRARY[family].lines, CARE_LABEL_SENTENCE].join('\n') }));
  return (
    <>
      <Field label="소재 유형" hint="고르면 표준 관리 문구로 다시 채워요. 아래에서 자유롭게 수정하세요.">
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {Object.entries(CARE_COPY_LIBRARY).filter(([k]) => k !== 'generic').map(([key, v]) => (
            <Chip key={key} on={info.family === key} onClick={() => pickFamily(key)}>{v.label}</Chip>
          ))}
        </div>
      </Field>
      <Field label="관리 문구 (줄마다 불릿으로 표시)" hint="케어라벨 확인 문장은 항상 포함돼요.">
        <textarea style={{ ...inp, minHeight: 130, resize: 'vertical', lineHeight: 1.6 }} value={info.text}
          onChange={(e) => setInfo((f) => ({ ...f, text: e.target.value }))} />
      </Field>
    </>
  );
}

function PolicyForm({ info, setInfo }) {
  const setSection = (i, patch) => setInfo((f) => ({ ...f, sections: f.sections.map((s, j) => (j === i ? { ...s, ...patch } : s)) }));
  return (
    <Field label="안내 섹션" hint="배송·교환·반품 표준 문구가 미리 채워져 있어요. 마켓 정책에 맞게 고쳐 쓰세요.">
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {info.sections.map((s, i) => (
          <div key={i} style={{ border: '1px solid #eee', borderRadius: 10, padding: 10 }}>
            <div style={{ ...rowGap, marginBottom: 6 }}>
              <input style={{ ...inpSm, fontWeight: 600 }} value={s.title} onChange={(e) => setSection(i, { title: e.target.value })} />
              <IconButton name="trash" size="sm" title="섹션 삭제" onClick={() => setInfo((f) => ({ ...f, sections: f.sections.filter((_s, j) => j !== i) }))} />
            </div>
            <textarea style={{ ...inpSm, minHeight: 74, resize: 'vertical', lineHeight: 1.6 }} value={s.body} onChange={(e) => setSection(i, { body: e.target.value })} />
          </div>
        ))}
      </div>
      <Button variant="ghost" size="sm" icon="plus" style={{ marginTop: 8 }}
        onClick={() => setInfo((f) => ({ ...f, sections: [...f.sections, { title: '안내', body: '' }] }))}>섹션 추가</Button>
    </Field>
  );
}

function HeaderForm({ info, setInfo }) {
  return (
    <>
      <Field label="국문 상품명"><input style={inp} value={info.nameKo} onChange={(e) => setInfo((f) => ({ ...f, nameKo: e.target.value }))} /></Field>
      <Field label="영문 상품명 (선택)"><input style={inp} placeholder="Wild Pop Color Roll-up T" value={info.nameEn} onChange={(e) => setInfo((f) => ({ ...f, nameEn: e.target.value }))} /></Field>
      <Field label="아이브로우 문구"><input style={inp} value={info.eyebrow} onChange={(e) => setInfo((f) => ({ ...f, eyebrow: e.target.value }))} /></Field>
    </>
  );
}

function FeatureIconsForm({ info, setInfo }) {
  const setItem = (i, patch) => setInfo((f) => ({ ...f, items: f.items.map((x, j) => (j === i ? { ...x, ...patch } : x)) }));
  return (
    <Field label="특징 3가지" hint="분석에서 뽑은 핵심 장점이 미리 채워져요.">
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {info.items.slice(0, 3).map((it, i) => (
          <div key={i} style={rowGap}>
            <span style={{ width: 60, flexShrink: 0, fontSize: 12, color: '#898989' }}>POINT {i + 1}</span>
            <input style={inpSm} placeholder="특징 (예: 롤업 배색 소매)" value={it.title} onChange={(e) => setItem(i, { title: e.target.value })} />
            <input style={inpSm} placeholder="짧은 설명 (선택)" value={it.desc} onChange={(e) => setItem(i, { desc: e.target.value })} />
          </div>
        ))}
      </div>
    </Field>
  );
}

function FitGuideForm({ info, setInfo, ctx }) {
  const fits = ctx.fits || [];
  return (
    <Field label="이 상품의 핏" hint="선택한 핏이 도식에서 강조돼요.">
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {fits.map((f) => <Chip key={f.value} on={info.current === f.value} onClick={() => setInfo((x) => ({ ...x, current: x.current === f.value ? null : f.value }))}>{f.label}</Chip>)}
      </div>
    </Field>
  );
}

function SizeMatrixForm({ info, setInfo }) {
  const setCell = (r, c, v) => setInfo((f) => ({ ...f, cells: f.cells.map((row, i) => (i === r ? row.map((x, j) => (j === c ? v : x)) : row)) }));
  const setHeight = (r, v) => setInfo((f) => ({ ...f, heights: f.heights.map((x, i) => (i === r ? v : x)) }));
  const setWeight = (c, v) => setInfo((f) => ({ ...f, weights: f.weights.map((x, i) => (i === c ? v : x)) }));
  return (
    <>
      <Field label="키 × 몸무게 추천 사이즈" hint="칸에 추천 사이즈(S/M/L…)를 적어요. 기본값은 일반적인 상의 기준이에요.">
        <div style={{ overflowX: 'auto' }}>
          <div style={{ display: 'grid', gridTemplateColumns: `90px repeat(${info.weights.length}, 1fr)`, gap: 4, minWidth: 480 }}>
            <span style={{ fontSize: 12, color: '#898989', alignSelf: 'center' }}>cm / kg</span>
            {info.weights.map((w, c) => <input key={c} style={inpSm} value={w} onChange={(e) => setWeight(c, e.target.value)} />)}
            {info.heights.map((h, r) => (
              <Fragment key={r}>
                <input style={inpSm} value={h} onChange={(e) => setHeight(r, e.target.value)} />
                {info.weights.map((_w, c) => (
                  <input key={c} style={{ ...inpSm, textAlign: 'center' }} value={info.cells[r]?.[c] || ''} onChange={(e) => setCell(r, c, e.target.value)} />
                ))}
              </Fragment>
            ))}
          </div>
        </div>
      </Field>
      <Field label="하단 안내 문구">
        <input style={inp} value={info.note} onChange={(e) => setInfo((f) => ({ ...f, note: e.target.value }))} />
      </Field>
    </>
  );
}

function ModelInfoForm({ info, setInfo }) {
  const setModel = (i, patch) => setInfo((f) => ({ ...f, models: f.models.map((m, j) => (j === i ? { ...m, ...patch } : m)) }));
  return (
    <Field label="모델 스펙 (최대 3명)">
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {info.models.map((m, i) => (
          <div key={i} style={rowGap}>
            <input style={{ ...inpSm, width: 110 }} placeholder="이름 (MODEL A)" value={m.name} onChange={(e) => setModel(i, { name: e.target.value })} />
            <input style={inpSm} placeholder="키 (167cm)" value={m.height} onChange={(e) => setModel(i, { height: e.target.value })} />
            <input style={inpSm} placeholder="착용 사이즈 (M)" value={m.size} onChange={(e) => setModel(i, { size: e.target.value })} />
            <IconButton name="trash" size="sm" title="삭제" onClick={() => setInfo((f) => ({ ...f, models: f.models.filter((_m, j) => j !== i) }))} />
          </div>
        ))}
      </div>
      {info.models.length < 3 && (
        <Button variant="ghost" size="sm" icon="plus" style={{ marginTop: 8 }}
          onClick={() => setInfo((f) => ({ ...f, models: [...f.models, { name: `MODEL ${'ABC'[f.models.length] || ''}`.trim(), height: '', size: '' }] }))}>모델 추가</Button>
      )}
    </Field>
  );
}

const FORMS = {
  size_table: SizeTableForm,
  required_notice: NoticeForm,
  care: CareForm,
  policy: PolicyForm,
  header: HeaderForm,
  feature_icons: FeatureIconsForm,
  fit_guide: FitGuideForm,
  size_matrix: SizeMatrixForm,
  model_info: ModelInfoForm,
};

export function InfoBlockModal({ type, initialInfo, ctx, editing, onClose, onSubmit }) {
  const [info, setInfo] = useState(initialInfo);
  const meta = INFO_PRESET_TYPES.find((p) => p.type === type) || { label: '내용' };
  const Form = FORMS[type];
  if (!Form) return null;
  return (
    <Modal onClose={onClose} wide>
      <div style={{ maxHeight: '72vh', overflowY: 'auto', paddingRight: 4 }}>
        <div style={{ marginBottom: 16 }}>
          <div className="lbl" style={{ color: '#898989', marginBottom: 4 }}>{editing ? '내용 수정' : '내용 추가'}</div>
          <h3 style={{ margin: 0, fontSize: 19 }}>{meta.label}</h3>
        </div>
        <Form info={info} setInfo={setInfo} ctx={ctx} />
      </div>
      <div className="modal-actions" style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
        <Button variant="quiet" onClick={onClose}>취소</Button>
        <Button variant="primary" icon="check" onClick={() => onSubmit(info)}>{editing ? '업데이트' : '블록 추가'}</Button>
      </div>
      {editing && <p className="hint" style={{ marginTop: 10 }}>업데이트하면 이 블록의 요소가 새 내용으로 다시 만들어져요 — 캔버스에서 직접 고친 부분은 대체돼요.</p>}
    </Modal>
  );
}

export default InfoBlockModal;
