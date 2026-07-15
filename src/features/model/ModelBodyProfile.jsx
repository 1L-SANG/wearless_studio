/* =============================================================
   features/model — ④ 신체 입력 (/model/body)
   키·몸무게(필수) + 체형 pill(필수, 직접입력 배타) + 성별(선택, MVP 범위 —
   ux-flow §2). PUT 은 전체 교체(REPLACE, api-spec §3.3).
   ============================================================= */
import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, Chips, ErrorState, Field, useToast } from '@/components/ui.jsx';
import { getProfile, putBodyProfile } from '@/lib/api/personalization.js';
import s from './ModelPersonalization.module.css';

// 글자 폭 추정(em) — AnalysisForm.jsx 의 customCategory 트레일링 입력과 동일 패턴.
const chWidth = (str) => [...str].reduce((n, ch) => n + (/[가-힣]/.test(ch) ? 1 : 0.55), 0).toFixed(1);

const BODY_TYPES = [
  { value: 'slim', label: '마른' },
  { value: 'normal', label: '보통' },
  { value: 'muscular', label: '근육' },
  { value: 'chubby', label: '통통' },
];
const GENDERS = [
  { value: 'female', label: '여성' },
  { value: 'male', label: '남성' },
  { value: 'other', label: '기타' },
];

export function ModelBodyProfile() {
  const navigate = useNavigate();
  const { push } = useToast();
  const [phase, setPhase] = useState('loading'); // loading|ready|error
  const [height, setHeight] = useState('');
  const [weight, setWeight] = useState('');
  const [bodyType, setBodyType] = useState(null);
  const [customDraft, setCustomDraft] = useState('');
  const [gender, setGender] = useState(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setPhase('loading');
    try {
      const p = await getProfile();
      const b = p.body || {};
      setHeight(b.heightCm != null ? String(b.heightCm) : '');
      setWeight(b.weightKg != null ? String(b.weightKg) : '');
      setBodyType(b.bodyType || null);
      setCustomDraft(b.bodyType === 'custom' ? (b.bodyTypeCustom || '') : '');
      setGender(b.gender || null);
      setPhase('ready');
    } catch (e) {
      push?.(e.message, { icon: 'alertCircle' });
      setPhase('error');
    }
  }, [push]);

  useEffect(() => { load(); }, [load]);

  const isCustom = bodyType === 'custom';
  const h = Number(height);
  const w = Number(weight);
  const heightOk = height !== '' && h >= 100 && h <= 230;
  const weightOk = weight !== '' && w >= 30 && w <= 200;
  const bodyTypeOk = isCustom ? customDraft.trim().length > 0 && customDraft.trim().length <= 30 : !!bodyType;
  const valid = heightOk && weightOk && bodyTypeOk;

  const onSubmit = async () => {
    if (!valid) { push?.('필수값을 확인해주세요.', { icon: 'alertCircle' }); return; }
    setSaving(true);
    try {
      await putBodyProfile({
        heightCm: h, weightKg: w, bodyType,
        bodyTypeCustom: isCustom ? customDraft.trim() : null,
        gender: gender || null,
      });
      push?.('신체 정보를 저장했어요.', { icon: 'check' });
      navigate('/model');
    } catch (e) {
      push?.(e.message || '저장에 실패했어요.', { icon: 'alertCircle' });
    } finally {
      setSaving(false);
    }
  };

  if (phase === 'loading') return <div className="wizard narrow"><div className="surface">불러오는 중…</div></div>;
  if (phase === 'error') return <div className="wizard narrow"><div className="surface"><ErrorState desc="신체 정보를 불러오지 못했어요." onRetry={load} /></div></div>;

  return (
    <div className="wizard narrow">
      <div className="page-head">
        <h1>신체 정보를 입력해주세요</h1>
        <p>키와 몸무게는 착장 컷의 체형을 실제와 가깝게 맞추는 데만 쓰여요. 다른 목적으로 사용되지 않고, 언제든 삭제할 수 있어요.</p>
      </div>

      <div className="surface">
        <div className="basic-fields">
          <Field label="키" opt="필수" type="number" min={100} max={230} value={height}
            onChange={(e) => setHeight(e.target.value)} hint="100~230cm" />
          <Field label="몸무게" opt="필수" type="number" min={30} max={200} value={weight}
            onChange={(e) => setWeight(e.target.value)} hint="30~200kg" />
        </div>

        <div className={s.sectionLabel}>체형 (필수)</div>
        <Chips options={BODY_TYPES} value={isCustom ? null : bodyType}
          onChange={(v) => { setBodyType(v); setCustomDraft(''); }}
          trailing={
            <input
              className={`chip chip-input${isCustom ? ' on' : ''}`}
              value={customDraft} maxLength={30} placeholder="직접 입력"
              style={{ width: `calc(${chWidth(customDraft || '직접 입력')}em + 32px)` }}
              onChange={(e) => setCustomDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); }}
              onBlur={() => { if (customDraft.trim()) setBodyType('custom'); }}
            />
          } />

        <div className={s.sectionLabel}>성별 (선택)</div>
        <Chips options={GENDERS} value={gender} onChange={setGender} />

        <Button variant="primary" block onClick={onSubmit} disabled={saving || !valid} iconRight="arrowRight" style={{ marginTop: 22 }}>
          {saving ? '저장 중…' : '저장하고 상태 확인하기'}
        </Button>
      </div>
    </div>
  );
}

export default ModelBodyProfile;
