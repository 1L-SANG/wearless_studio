import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { api } from '@/lib/api/index.js';
import { Button } from '@/components/ui.jsx';
import { mallDefaultsValues, mallDefaultsPatch, validateMallDefaults } from '@/lib/mallDefaults.js';
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

export function MallDefaultsForm({ mall, defaults = {}, onSaved }) {
  const { session } = useAuth();
  const addresses = useQuery({ queryKey: ['mall-addresses', session?.user?.id, mall, defaults.revision],
    queryFn: () => api.listZigzagAddresses(), enabled: mall === 'zigzag', retry: false });
  const [values, setValues] = useState(() => mallDefaultsValues(mall, defaults));
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
      const current = await api.getMallDefaults(mall);
      if (active.current) { savedRef.current = current; setRevision(current.revision); setNeedsReload(false); setFailure('다른 창에서 기본 정보를 바꿨어요. 입력한 값은 남아 있어요. 확인 후 다시 저장해 주세요'); }
    } catch {
      if (active.current) { setNeedsReload(true); setFailure('최신 정보를 불러오지 못했어요. 입력한 값은 남아 있어요'); }
    }
  };
  const save = async (event) => {
    event.preventDefault();
    if (locked.current || needsReload) return;
    const validation = validateMallDefaults(mall, values, savedRef.current);
    setErrors(validation.errors); setNotice(''); setFailure('');
    if (!validation.ok) return;
    locked.current = true; setBusy(true);
    try {
      const result = await api.saveMallDefaults(mall, { baseRevision: revision, ...mallDefaultsPatch(mall, values) });
      if (active.current) { savedRef.current = result; setRevision(result.revision); setNotice('저장했어요'); onSaved?.(result); }
    } catch (error) {
      if (!active.current) return;
      if (error.code === 'defaults_conflict') await reloadRevision();
      else setFailure(error.message || '저장하지 못했어요. 다시 시도해 주세요');
    } finally { locked.current = false; if (active.current) setBusy(false); }
  };
  return <form className={styles.salesCard} onSubmit={save} noValidate aria-labelledby="sales-defaults-title" aria-busy={busy}>
    <h2 id="sales-defaults-title">판매 정보</h2>
    {(failure || Object.keys(errors).length > 0) && <div role="alert" tabIndex={-1} ref={summaryRef} className={styles.errorSummary}>
      <p>{failure || '입력한 항목을 확인해 주세요'}</p>
      {Object.entries(errors).map(([path, message]) => <a key={path} href={`#sales-${path.replace('.', '-')}`}
        onClick={(event) => { event.preventDefault(); document.getElementById(`sales-${path.replace('.', '-')}`)?.focus(); }}>{message}</a>)}
      {needsReload && <Button disabled={busy} onClick={reloadRevision}>최신 정보 다시 확인</Button>}
    </div>}
    <fieldset className={styles.formFields} disabled={busy}>
      {mall === 'zigzag' && <>
      <section className={styles.salesGroup}><div className={styles.groupHead}><h3>배송비</h3></div>
        {radios('shipping', 'feeType', '배송비 정책', [['FREE', '무료'], ['CHARGED', '고정'], ['CONDITIONAL_FREE', '조건부 무료']])}
        <div className={styles.fields}>
          {values.shipping.feeType !== 'FREE' && field('shipping', 'baseFee', '기본 배송비', '원')}
          {values.shipping.feeType === 'CONDITIONAL_FREE' && field('shipping', 'conditionalAmount', '무료 기준 금액', '원 이상')}
          {field('shipping', 'jejuFee', '제주 추가', '원')}{field('shipping', 'isolatedFee', '도서산간 추가', '원')}
        </div>
      </section>
      <section className={`${styles.salesGroup} ${styles.singleFieldGroup}`}><div className={styles.groupHead}><h3>발송 소요일</h3></div>
        <div className={styles.fields}>{field(null, 'shippingDays', '주문 뒤 발송까지', '일')}</div>
      </section>
      <section className={styles.salesGroup}><div className={styles.groupHead}><h3>반품비와 교환비</h3></div>
        <div className={styles.fields}>{field('returns', 'totalFee', '전체 반품', '원')}{field('returns', 'partialFee', '부분 반품', '원')}{field('returns', 'exchangeFee', '교환', '원')}</div>
      </section>
      <section className={styles.salesGroup}><div className={styles.groupHead}><h3>고시 공통값</h3></div>
        <div className={styles.fields}>{field('disclosure', 'country', '제조국')}{field('disclosure', 'manufacturer', '제조자')}
          {field('disclosure', 'asManager', 'A/S 책임자')}{field('disclosure', 'phone', '전화번호')}</div>
        <div className={styles.warrantyField}>{field('disclosure', 'warrantyText', '품질보증기준')}</div>
      </section>
      <section className={`${styles.salesGroup} ${styles.singleFieldGroup}`}><div className={styles.groupHead}><h3>반품지</h3></div>
        {addresses.isPending ? <p role="status" className={styles.help}>주소록을 가져오는 중이에요</p> : addresses.error
          ? <div role="alert"><p className={styles.error}>{addresses.error.message}</p><Button onClick={() => addresses.refetch()}>다시 확인</Button></div>
          : <label className={styles.field} htmlFor="mall-return-address"><span>반품지</span>
            <select id="mall-return-address" name="returnAddressId" value={values.addresses.returnAddressId}
              onChange={(event) => change('addresses', 'returnAddressId', event.target.value)}>
              <option value="">주소를 골라 주세요</option>
              {(addresses.data || []).map((address) => <option key={address.id} value={address.id}>{address.name} ({address.address})</option>)}
            </select></label>}
        <p className={styles.help}>지그재그 파트너센터 주소록에서 골라요.</p>
      </section>
      </>}
      {mall === 'cafe24' && <section className={`${styles.salesGroup} ${styles.singleFieldGroup}`}><div className={styles.groupHead}><h3>원산지 기본값</h3></div>
        {field('disclosure', 'country', '제조국')}
        <p className={styles.help}>에디터 고시에 제조국이 있으면 그 값을 써요.</p>
      </section>}
      <section className={styles.salesGroup}><div className={styles.groupHead}><h3>{mall === 'zigzag' ? '과세와 상품 출처' : '과세'}</h3></div>
        <div className={styles.fields}><div className={styles.field}>{mall === 'zigzag' && <span>과세</span>}{radios(null, 'taxType', '과세', [['TAX', '과세'], ['FREE', '면세']])}</div>
          {mall === 'zigzag' && <div className={styles.field}><span>상품 출처</span>{radios(null, 'productOrigin', '상품 출처', [['domestic', '국내 사입'], ['imported', '직접 수입']])}</div>}</div>
      </section>
      <section className={styles.salesGroup}><div className={styles.groupHead}><h3>등록 직후</h3></div>
        {radios(null, 'listingMode', '등록 직후', [['HIDDEN', '진열 안 함'], ['ON_SALE', '바로 판매']])}
        <p className={styles.help}>몰에서 확인한 뒤 켜면 사이즈나 재고가 빠진 채 주문받는 일이 없어요.</p>
      </section>
    </fieldset>
    <div className={`modal-actions ${styles.saveActions}`}><span className={styles.help} role="status">{notice}</span>
      <Button type="submit" variant="primary" disabled={busy || needsReload}>{busy ? '저장 중' : '저장'}</Button></div>
  </form>;
}
