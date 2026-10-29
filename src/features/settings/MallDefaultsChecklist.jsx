import { useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api/index.js';
import { Button } from '@/components/ui.jsx';
import { salesDefaultsValues, salesDefaultsPatch, validateSalesDefaults } from '@/lib/mallDefaults.js';
import styles from './MallConnections.module.css';

function Field({ name, id, label, value, onChange, error, unit }) {
  return <label className={styles.field} htmlFor={id}><span>{label}</span>
    <span className={unit ? styles.unitField : undefined}>
      <input id={id} name={name} value={value ?? ''} inputMode={unit ? 'numeric' : name === 'phone' ? 'tel' : undefined}
        onChange={(event) => onChange(event.target.value)} aria-invalid={!!error} aria-describedby={error ? `${id}-error` : undefined} />
      {unit && <span>{unit}</span>}
    </span>
    {error && <span className={styles.error} id={`${id}-error`}>{error}</span>}
  </label>;
}

function RadioGroup({ name, id, label, value, onChange, options, error }) {
  return <fieldset className={styles.radios} id={id} aria-describedby={error ? `${id}-error` : undefined}>
    <legend className={styles.srOnly}>{label}</legend>
    <div className={styles.segments}>{options.map(([key, text]) => <label key={key}>
      <input type="radio" name={name} value={key} checked={value === key} onChange={() => onChange(key)} />
      <span>{text}</span>
    </label>)}</div>
    {error && <span className={styles.error} id={`${id}-error`}>{error}</span>}
  </fieldset>;
}

function Usage({ kind = 'zigzag' }) {
  return <span className={styles.uses}>
    {kind === 'all' ? <span>모든 몰</span> : <>
      <span><img src="/assets/malls/zigzag-48.png" alt="" />{kind === 'notice' ? '지그재그 고시' : '지그재그'}</span>
      {kind === 'shipping' && <span>카페24는 몰 설정</span>}
      {kind === 'notice' && <span><img src="/assets/malls/cafe24-48.png" alt="" />카페24 원산지</span>}
    </>}
  </span>;
}

export function SalesDefaultsCard({ defaults = {}, onSaved }) {
  const [values, setValues] = useState(() => salesDefaultsValues(defaults));
  const [revision, setRevision] = useState(defaults.revision ?? 0);
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [needsReload, setNeedsReload] = useState(false);
  const summaryRef = useRef(null), active = useRef(true), locked = useRef(false);
  const savedRef = useRef(defaults);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => { if (failure || Object.keys(errors).length) summaryRef.current?.focus(); }, [failure, errors]);
  const change = (section, name, value) => setValues((current) => section
    ? { ...current, [section]: { ...current[section], [name]: value } } : { ...current, [name]: value });
  const field = (section, name, label, unit) => {
    const path = section ? `${section}.${name}` : name;
    return <Field key={path} name={name} id={`sales-${path.replace('.', '-')}`} label={label} unit={unit}
      value={section ? values[section][name] : values[name]} onChange={(value) => change(section, name, value)} error={errors[path]} />;
  };
  const radios = (section, name, label, options) => <RadioGroup name={name} id={`sales-${section ? `${section}-` : ''}${name}`} label={label}
    value={section ? values[section][name] : values[name]} options={options} onChange={(value) => change(section, name, value)} />;
  const reloadRevision = async () => {
    try {
      const current = await api.getSalesDefaults();
      if (active.current) { savedRef.current = current; setRevision(current.revision); setNeedsReload(false); setFailure('다른 창에서 기본 정보를 바꿨어요. 입력한 값은 남아 있어요. 확인 후 다시 저장해 주세요'); }
    } catch {
      if (active.current) { setNeedsReload(true); setFailure('최신 정보를 불러오지 못했어요. 입력한 값은 남아 있어요'); }
    }
  };
  const save = async (event) => {
    event.preventDefault();
    if (locked.current || needsReload) return;
    const validation = validateSalesDefaults(values, savedRef.current);
    setErrors(validation.errors); setNotice(''); setFailure('');
    if (!validation.ok) return;
    locked.current = true; setBusy(true);
    try {
      const result = await api.saveSalesDefaults({ baseRevision: revision, ...salesDefaultsPatch(values) });
      if (active.current) { savedRef.current = result; setRevision(result.revision); setNotice('저장했어요'); onSaved?.(result); }
    } catch (error) {
      if (!active.current) return;
      if (error.code === 'defaults_conflict') await reloadRevision();
      else setFailure(error.message || '저장하지 못했어요. 다시 시도해 주세요');
    } finally { locked.current = false; if (active.current) setBusy(false); }
  };
  return <form className={styles.salesCard} onSubmit={save} noValidate aria-labelledby="sales-defaults-title" aria-busy={busy}>
    <h2 id="sales-defaults-title">판매 기본 정보</h2>
    <p className={styles.cardIntro}>한 번 저장하면 등록 창에서 상품마다 바꿀 수도 있어요.</p>
    {(failure || Object.keys(errors).length > 0) && <div role="alert" tabIndex={-1} ref={summaryRef} className={styles.errorSummary}>
      <p>{failure || '입력한 항목을 확인해 주세요'}</p>
      {Object.entries(errors).map(([path, message]) => <a key={path} href={`#sales-${path.replace('.', '-')}`}
        onClick={(event) => { event.preventDefault(); document.getElementById(`sales-${path.replace('.', '-')}`)?.focus(); }}>{message}</a>)}
      {needsReload && <Button disabled={busy} onClick={reloadRevision}>최신 정보 다시 확인</Button>}
    </div>}
    <fieldset className={styles.formFields} disabled={busy}>
      <section className={styles.salesGroup}><div className={styles.groupHead}><h3>배송비</h3><Usage kind="shipping" /></div>
        {radios('shipping', 'feeType', '배송비 정책', [['FREE', '무료'], ['CHARGED', '고정'], ['CONDITIONAL_FREE', '조건부 무료']])}
        <div className={styles.fields}>
          {values.shipping.feeType !== 'FREE' && field('shipping', 'baseFee', '기본 배송비', '원')}
          {values.shipping.feeType === 'CONDITIONAL_FREE' && field('shipping', 'conditionalAmount', '무료 기준 금액', '원 이상')}
          {field('shipping', 'jejuFee', '제주 추가', '원')}{field('shipping', 'isolatedFee', '도서산간 추가', '원')}
        </div>
      </section>
      <section className={styles.salesGroup}><div className={styles.groupHead}><h3>발송 소요일</h3><Usage /></div>
        <div className={styles.fields}>{field(null, 'shippingDays', '주문 뒤 발송까지', '일')}</div>
      </section>
      <section className={styles.salesGroup}><div className={styles.groupHead}><h3>반품비와 교환비</h3><Usage kind="shipping" /></div>
        <div className={styles.fields}>{field('returns', 'totalFee', '전체 반품', '원')}{field('returns', 'partialFee', '부분 반품', '원')}{field('returns', 'exchangeFee', '교환', '원')}</div>
      </section>
      <section className={styles.salesGroup}><div className={styles.groupHead}><h3>고시 공통값</h3><Usage kind="notice" /></div>
        <div className={styles.fields}>{field('disclosure', 'country', '제조국')}{field('disclosure', 'manufacturer', '제조자')}
          {field('disclosure', 'asManager', 'A/S 책임자')}{field('disclosure', 'phone', '전화번호')}</div>
        <div className={styles.warrantyField}>{field('disclosure', 'warrantyText', '품질보증기준')}</div>
      </section>
      <section className={styles.salesGroup}><div className={styles.groupHead}><h3>과세와 상품 출처</h3><Usage kind="all" /></div>
        <div className={styles.fields}><div className={styles.field}><span>과세</span>{radios(null, 'taxType', '과세', [['TAX', '과세'], ['FREE', '면세']])}</div>
          <div className={styles.field}><span>상품 출처</span>{radios(null, 'productOrigin', '상품 출처', [['domestic', '국내 사입'], ['imported', '직접 수입']])}</div></div>
      </section>
      <section className={styles.salesGroup}><div className={styles.groupHead}><h3>등록 직후</h3><Usage kind="all" /></div>
        {radios(null, 'listingMode', '등록 직후', [['HIDDEN', '진열 안 함'], ['ON_SALE', '바로 판매']])}
        <p className={styles.help}>몰에서 확인한 뒤 켜면 사이즈나 재고가 빠진 채 주문받는 일이 없어요.</p>
      </section>
    </fieldset>
    <div className={`modal-actions ${styles.saveActions}`}><span className={styles.help} role="status">{notice}</span>
      <Button type="submit" variant="primary" disabled={busy || needsReload}>{busy ? '저장 중' : '저장'}</Button></div>
  </form>;
}
