import { useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api/index.js';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { Button, Icon } from '@/components/ui.jsx';
import { completedItems, validateDefaults } from '@/lib/mallDefaults.js';
import styles from './MallConnections.module.css';

const SECTIONS = [
  ['shipping', '배송비'], ['returns', '반품비와 교환비'],
  ['addresses', '출고지와 반품지'], ['disclosure', '고시 공통값'],
];

function Field({ section, name, label, values, onChange, errors, money = false }) {
  const id = `mall-${section}-${name}`;
  return <label className={styles.field} htmlFor={id}><span>{label}</span>
    <input id={id} name={name} value={values[name] ?? ''} inputMode={money ? 'numeric' : name === 'phone' ? 'tel' : undefined}
      onChange={(event) => onChange({ ...values, [name]: event.target.value })}
      aria-invalid={!!errors[name]} aria-describedby={errors[name] ? `${id}-error` : undefined} />
    {errors[name] && <span className={styles.error} id={`${id}-error`}>{errors[name]}</span>}
  </label>;
}

function RadioGroup({ name, label, options, values, onChange }) {
  return <fieldset className={styles.radios}><legend>{label}</legend>
    {options.map(([value, text]) => <label key={value}><input type="radio" name={name} value={value}
      checked={values[name] === value} onChange={() => onChange({ ...values, [name]: value })} />{text}</label>)}
  </fieldset>;
}

function AddressFields({ values, onChange, errors }) {
  const { session } = useAuth();
  const { data: addresses, isPending, error, refetch } = useQuery({ queryKey: ['zigzag-addresses', session?.user?.id],
    queryFn: () => api.listZigzagAddresses(), retry: false });
  if (isPending) return <p className={styles.help} role="status">주소록을 가져오는 중이에요</p>;
  if (error) return <div role="alert"><p className={styles.error}>{error.message}</p><Button onClick={refetch}>다시 확인</Button></div>;
  return <>
    <p className={styles.help}>파트너센터 주소록에서 가져와요</p>
    <div className={styles.fields}>
      {[['shippingAddressId', '출고지'], ['returnAddressId', '반품지']].map(([name, text]) => <label className={styles.field} key={name}>
        <span>{text}</span><select name={name} value={values[name] || ''} aria-invalid={!!errors[name]}
          onChange={(event) => onChange({ ...values, [name]: event.target.value })}>
          <option value="">주소를 골라 주세요</option>
          {(addresses || []).map((address) => <option key={address.id} value={address.id}>{address.name}, {address.address}</option>)}
        </select>{errors[name] && <span className={styles.error}>{errors[name]}</span>}
      </label>)}
    </div>
    {addresses?.length === 0 && <p className={styles.help}>새 주소는 파트너센터에서 추가해요</p>}
  </>;
}

function MallDefaultsSection({ section, defaults, onSaved, onDone }) {
  const [values, setValues] = useState(() => ({ ...(section === 'shipping' ? { feeType: 'CHARGED' } : {}),
    ...(defaults[section] || {}), ...(section === 'disclosure' ? { taxType: defaults.taxType || 'TAX', productOrigin: defaults.productOrigin || 'domestic' } : {}) }));
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState('');
  const [busy, setBusy] = useState(false);
  const formRef = useRef(null);
  const field = (name, label, money = false) => <Field key={name} section={section} name={name} label={label} money={money} values={values} onChange={setValues} errors={errors} />;
  const radios = (name, label, options) => <RadioGroup name={name} label={label} options={options} values={values} onChange={setValues} />;
  const save = async (event) => {
    event.preventDefault();
    if (busy) return;
    const validation = validateDefaults(section, values);
    setErrors(validation.errors);
    if (!validation.ok) { formRef.current?.elements.namedItem(Object.keys(validation.errors)[0])?.focus(); return; }
    const fields = { ...values };
    delete fields.taxType; delete fields.productOrigin;
    if (section === 'shipping') {
      if (fields.feeType === 'FREE') fields.baseFee = 0;
      if (fields.feeType !== 'CONDITIONAL_FREE') delete fields.conditionalAmount;
    }
    if (section === 'shipping' || section === 'returns') {
      for (const key of Object.keys(fields)) if (key !== 'feeType') {
        if (fields[key] === '' || fields[key] == null) delete fields[key];
        else fields[key] = Number(fields[key]);
      }
    }
    const payload = { [section]: fields, ...(section === 'disclosure' ? { taxType: values.taxType, productOrigin: values.productOrigin } : {}) };
    setBusy(true); setFailure('');
    try { onSaved?.(await api.saveZigzagDefaults(payload)); onDone(); }
    catch (error) { setFailure(error.message || '저장하지 못했어요. 잠시 뒤 다시 시도해 주세요'); }
    finally { setBusy(false); }
  };
  return <form ref={formRef} onSubmit={save} noValidate className={styles.sectionForm}>
    <fieldset disabled={busy} className={styles.formFields}>
      {section === 'shipping' && <>
        {radios('feeType', '배송비 정책', [['FREE', '무료'], ['CHARGED', '고정'], ['CONDITIONAL_FREE', '조건부 무료']])}
        <div className={styles.fields}>
          {values.feeType !== 'FREE' && field('baseFee', '기본 배송비', true)}
          {field('jejuFee', '제주 추가비', true)}{field('isolatedFee', '도서산간 추가비', true)}
          {values.feeType === 'CONDITIONAL_FREE' && field('conditionalAmount', '무료 조건 금액', true)}
        </div>
      </>}
      {section === 'returns' && <div className={styles.fields}>{field('partialFee', '부분 반품', true)}{field('totalFee', '전체 반품', true)}{field('exchangeFee', '교환', true)}</div>}
      {section === 'addresses' && <AddressFields values={values} onChange={setValues} errors={errors} />}
      {section === 'disclosure' && <>
        <div className={styles.fields}>{field('country', '제조국')}{field('manufacturer', '제조자')}{field('asManager', 'AS책임자')}{field('phone', '전화번호')}</div>
        {radios('productOrigin', '상품 출처', [['domestic', '국내 사입'], ['imported', '직접 수입']])}
        {radios('taxType', '과세 여부', [['TAX', '과세'], ['FREE', '면세']])}
      </>}
    </fieldset>
    {failure && <p className={styles.error} role="alert">{failure}</p>}
    <div className={`modal-actions ${styles.leftActions}`}><Button type="submit" variant="primary" disabled={busy}>
      {busy ? '저장 중' : '이 항목 저장'}</Button></div>
  </form>;
}

export function MallDefaultsChecklist({ defaults = {}, onSaved }) {
  const done = completedItems(defaults);
  const [expanded, setExpanded] = useState(() => SECTIONS.find(([id]) => !done.includes(id))?.[0] || null);
  const [collapsed, setCollapsed] = useState(false);
  return <section className={styles.checklist} aria-label="지그재그 기본 설정">
    <div className={styles.checklistHead}>
      <h3>{done.length === 4 ? '기본 설정 끝' : '처음 한 번 기본 설정'}</h3>
      <span className={styles.help}>4개 중 {done.length}개 끝</span>
      <button type="button" className={styles.textButton} onClick={() => setCollapsed(!collapsed)} aria-expanded={!collapsed}>
        {collapsed ? '기본 설정 열기' : '나중에 할게요'}</button>
    </div>
    <progress className={styles.progress} aria-label="기본 설정 진행" aria-valuenow={done.length} max={4} value={done.length} />
    {!collapsed && <>
      <p className={styles.help}>지금 안 채워도 돼요. 첫 등록 전에 한 번 물어요</p>
      <ol className={styles.checklistItems}>{SECTIONS.map(([id, label], index) => <li key={id}>
        <button className={styles.sectionToggle} type="button" aria-expanded={expanded === id} aria-controls={`mall-section-${id}`}
          onClick={() => setExpanded(expanded === id ? null : id)}>
          <span className={styles.check}>{done.includes(id) ? <Icon name="check" size={16} /> : index + 1}</span>
          <span>{label}</span><Icon name={expanded === id ? 'chevUp' : 'chevDown'} size={16} />
        </button>
        {expanded === id && <div id={`mall-section-${id}`}>
          <MallDefaultsSection key={id} section={id} defaults={defaults} onSaved={onSaved} onDone={() => setExpanded(null)} />
        </div>}
      </li>)}</ol>
    </>}
  </section>;
}
