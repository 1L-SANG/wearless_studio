import { useState } from 'react';
import { Button } from '@/components/ui.jsx';
import { MALL_LABELS, registrationVariants } from './mallRegisterDraft.js';
import styles from './MallRegisterModal.module.css';

export function MallRegisterFields({ draft, preflight, onChange, disabled }) {
  const context = preflight?.context || {}, assets = context.assets || [], catalogs = context.catalogs || {}, defaults = context.defaults || {};
  const [sizesText, setSizesText] = useState(() => draft.sizes.join(', '));
  const [sizeError, setSizeError] = useState('');
  const colors = context.colors || [], variants = registrationVariants(colors, draft.sizes);
  const issues = [...(preflight?.localIssues || []), ...(preflight?.items || []).flatMap((item) => item.issues || [])];
  const issueFor = (path) => issues.find((issue) => issue.path === path);
  const patch = (values) => onChange({ ...draft, ...values, confirmedSourcesByMall: {} });
  const input = (path, value, update, label) => <span><input aria-label={label} data-field={path} inputMode="numeric" value={value} disabled={disabled}
    aria-invalid={!!issueFor(path) || undefined} aria-describedby={issueFor(path) ? `mall-error-${path}` : undefined} onChange={(event) => update(event.target.value)} />
    {issueFor(path) && <small id={`mall-error-${path}`} className={styles.error}>{issueFor(path).message}</small>}</span>;
  const disclosure = (event) => event.currentTarget.querySelector('summary')?.setAttribute('aria-expanded', String(event.currentTarget.open));
  const taxExcluded = draft.selectedMalls.includes('cafe24') && catalogs.cafe24?.priceBasis === 'B';
  const singleTaxExcluded = taxExcluded && draft.selectedMalls.length === 1;
  const options = (catalog) => (catalog?.categories || []).map((category) => {
    const names = [category.name]; let parent = category.parentCategoryId;
    const seen = new Set([category.categoryId]);
    while (parent && !seen.has(parent)) { seen.add(parent); const row = catalog.categories.find((item) => item.categoryId === parent); if (!row) break; names.unshift(row.name); parent = row.parentCategoryId; }
    return { ...category, label: names.join(' › ') };
  });
  const saveSizes = async () => {
    const sizes = sizesText.split(',').map((size) => size.trim().replace(/\s+/g, ' ')).filter(Boolean);
    if (!sizes.length || new Set(sizes).size !== sizes.length) { setSizeError('중복이나 빈칸 없이 사이즈를 입력해 주세요'); return; }
    const removed = draft.sizes.filter((size) => !sizes.includes(size));
    if (removed.length && !globalThis.confirm(`${removed.join(', ')} 행의 실측값과 옵션 입력이 지워져요. 저장할까요?`)) return;
    setSizeError('');
    try { await context.onSaveSizes?.(sizes); } catch (error) { setSizeError(error.message); }
  };
  const zigzagDefaults = defaults.zigzag || {};
  const sales = { ...zigzagDefaults, ...draft.salesOverrides, shipping: { ...zigzagDefaults.shipping, ...draft.salesOverrides.shipping }, returns: { ...zigzagDefaults.returns, ...draft.salesOverrides.returns } };
  const updateSale = (key, value) => { const salesOverrides = { ...draft.salesOverrides }; if (value === '') delete salesOverrides[key]; else salesOverrides[key] = value; patch({ salesOverrides }); };
  return <fieldset className={styles.fields} disabled={disabled}>
    <section className={styles.zone}><h3>확인</h3>
      <label className={styles.row}><span>상품명</span><span><input data-field="productName" value={draft.productName} maxLength={100} aria-invalid={!!issueFor('productName') || undefined} aria-describedby={issueFor('productName') ? 'mall-error-productName' : undefined}
        onChange={(event) => patch({ productName: event.target.value })} />{issueFor('productName') && <small id="mall-error-productName" className={styles.error}>{issueFor('productName').message}</small>}</span></label>
      <div className={styles.row}><span>상세설명</span><span>상세 이미지 {context.publication?.files?.length || 0}장 · 지금 편집본</span></div>
      <details className={styles.disclosure} onToggle={disclosure}><summary aria-expanded="false" aria-controls="mall-images">대표 이미지와 추가 이미지 바꾸기</summary>
        <div id="mall-images">
          <label className={styles.row}><span>대표 이미지</span><select data-field="mainAssetId" value={draft.mainAssetId || ''} aria-invalid={!!issueFor('mainAssetId') || undefined}
            onChange={(event) => patch({ mainAssetId: event.target.value, additionalAssetIds: draft.additionalAssetIds.filter((id) => id !== event.target.value) })}>
            <option value="">대표 이미지를 골라 주세요</option>{assets.map((asset, index) => <option key={asset.assetId} value={asset.assetId}>이미지 {index + 1}</option>)}</select></label>
          <div className={styles.imageChoices}>{assets.map((asset, index) => <label key={asset.assetId}>
            <img src={asset.src} alt={`이미지 ${index + 1}`} className={styles.thumbnail} />
            <span><input type="checkbox" checked={draft.additionalAssetIds.includes(asset.assetId)} disabled={disabled || draft.mainAssetId === asset.assetId || !draft.additionalAssetIds.includes(asset.assetId) && draft.additionalAssetIds.length >= 4}
              onChange={(event) => patch({ additionalAssetIds: event.target.checked ? [...draft.additionalAssetIds, asset.assetId] : draft.additionalAssetIds.filter((id) => id !== asset.assetId) })} />추가 이미지 {index + 1}</span></label>)}</div>
        </div>
      </details>
      <div className={styles.row}><span>대표 이미지</span><span>{assets.find((asset) => asset.assetId === draft.mainAssetId) && <img className={styles.thumbnail} src={assets.find((asset) => asset.assetId === draft.mainAssetId).src} alt="대표 이미지" />}<small className={styles.notice}>카페24는 몰 이미지 크기 설정에 맞춰 잘라요.</small>{context.gifNotice && <small className={styles.notice}>GIF는 준비된 정지 이미지로 보내요.</small>}</span></div>
      <div className={styles.row}><span>추가 이미지</span><ol className={styles.imageOrder}>{draft.additionalAssetIds.map((id, index) => <li key={id}>
        <img src={assets.find((asset) => asset.assetId === id)?.src} className={styles.thumbnail} alt={`추가 이미지 ${index + 1}`} />
        <span>{index + 1}</span><div className="modal-actions"><button type="button" className="btn btn-ghost btn-sm" disabled={disabled || index === 0} aria-label={`추가 이미지 ${index + 1} 앞으로`} onClick={() => {
          const ids = [...draft.additionalAssetIds]; [ids[index - 1], ids[index]] = [ids[index], ids[index - 1]]; patch({ additionalAssetIds: ids });
        }}>앞으로</button><button type="button" className="btn btn-ghost btn-sm" disabled={disabled} aria-label={`추가 이미지 ${index + 1} 빼기`} onClick={() => patch({ additionalAssetIds: draft.additionalAssetIds.filter((item) => item !== id) })}>빼기</button></div></li>)}</ol></div>
      <div className={styles.row}><span>옵션</span><span>{colors.length ? colors.map((color) => color.name || color.label).join(', ') : '저장된 상품 색상을 확인해 주세요'} × {draft.sizes.join(', ')} · {variants.length}개
        {draft.sizes.length === 1 && draft.sizes[0] === 'FREE' && <small className={styles.notice}>사이즈표가 FREE 한 줄이에요. 사이즈가 있으면 고쳐 주세요.</small>}</span></div>
      <details className={styles.disclosure} onToggle={disclosure}><summary aria-expanded="false" aria-controls="mall-sizes">사이즈 고치기</summary><div id="mall-sizes">
        <label className={styles.row}><span>사이즈</span><input data-field="sizes" value={sizesText} onChange={(event) => setSizesText(event.target.value)} aria-describedby="mall-sizes-hint" /></label>
        <p id="mall-sizes-hint" className={styles.notice}>쉼표로 나눠 입력해요. 새 행의 실측은 비워 둬요.</p>{sizeError && <p role="alert" className={styles.error}>{sizeError}</p>}
        <div className="modal-actions"><Button onClick={saveSizes} disabled={disabled}>사이즈 저장</Button></div>
      </div></details>
      {draft.selectedMalls.map((mall) => <label className={styles.row} key={mall}><span>{MALL_LABELS[mall]} 카테고리</span><span><select data-field={`categories.${mall}`} value={draft.categories[mall]?.categoryId || ''}
        aria-invalid={!!issueFor(`categories.${mall}`) || undefined} onChange={(event) => {
          const categories = { ...draft.categories }; if (event.target.value) categories[mall] = { categoryId: event.target.value }; else delete categories[mall]; patch({ categories });
        }}><option value="">{catalogs[mall]?.complete ? '카테고리 선택' : '카테고리 확인 중'}</option>{options(catalogs[mall]).map((category) => <option key={category.categoryId} value={category.categoryId}>{category.label}</option>)}</select>
        {issueFor(`categories.${mall}`) && <small className={styles.error}>{issueFor(`categories.${mall}`).message}</small>}</span></label>)}
    </section>
    <section className={`${styles.zone} ${styles.inputZone}`}><h3>입력</h3>
      <label className={styles.row}><span>{singleTaxExcluded ? '카페24 상품가, 세금 제외' : '판매가'}</span><span className={styles.moneyField}>{input('basePrice', draft.basePrice, (value) => patch({ basePrice: value,
        ...(singleTaxExcluded ? { priceOverrides: { ...draft.priceOverrides, cafe24: { amount: value, basis: 'tax_excluded' } } } : {}) }), '판매가')}원</span></label>
      <details className={styles.disclosure} open={taxExcluded && !singleTaxExcluded || undefined} onToggle={disclosure}><summary aria-expanded={taxExcluded && !singleTaxExcluded ? 'true' : 'false'} aria-controls="mall-prices">몰마다 다르게</summary><div id="mall-prices">
        {draft.selectedMalls.map((mall) => <label className={styles.row} key={mall}><span>{MALL_LABELS[mall]}{catalogs[mall]?.priceBasis === 'B' ? ' 상품가, 세금 제외' : ' 판매가'}</span><span>
          {input(`priceOverrides.${mall}`, draft.priceOverrides[mall]?.amount ?? '', (value) => {
            const overrides = { ...draft.priceOverrides }; if (value === '') delete overrides[mall]; else overrides[mall] = { amount: value, basis: catalogs[mall]?.priceBasis === 'B' ? 'tax_excluded' : 'selling_price' }; patch({ priceOverrides: overrides });
          }, `${MALL_LABELS[mall]} 판매가`)}<small className={styles.notice}>{catalogs[mall]?.priceBasis === 'B' ? '세금을 더하기 전 금액을 직접 확인해 주세요.' : '비우면 공통 판매가를 써요.'}</small>
        </span></label>)}
      </div></details>
      <label className={styles.row}><span>재고</span><span className={styles.moneyField}>{input('defaultQuantity', draft.defaultQuantity, (value) => patch({ defaultQuantity: value }), '옵션마다 같은 재고')}옵션마다</span></label>
      <details className={styles.disclosure} onToggle={disclosure}><summary aria-expanded="false" aria-controls="mall-variants">옵션별 재고와 추가금액</summary><div id="mall-variants" className={styles.variantScroll}>
        <table><thead><tr><th>옵션</th><th>재고</th><th>추가금액</th></tr></thead><tbody>{variants.slice(0, 200).map((variant, index) => {
          const overrideIndex = draft.variantOverrides.findIndex((item) => item.variantKey === variant.variantKey);
          const override = draft.variantOverrides[overrideIndex];
          const path = overrideIndex >= 0 ? `variantOverrides.${overrideIndex}` : `variantOptions.${index}`;
          const update = (key, value) => patch({ variantOverrides: [...draft.variantOverrides.filter((item) => item.variantKey !== variant.variantKey),
            { variantKey: variant.variantKey, quantity: override?.quantity ?? draft.defaultQuantity, additionalAmount: override?.additionalAmount ?? 0, [key]: value }] });
          return <tr key={variant.variantKey}><td>{variant.color} · {variant.size}</td><td>{input(path, override?.quantity ?? draft.defaultQuantity, (value) => update('quantity', value), `${variant.color} ${variant.size} 재고`)}</td><td>{input(`${path}.additionalAmount`, override?.additionalAmount ?? 0, (value) => update('additionalAmount', value), `${variant.color} ${variant.size} 추가금액`)}</td></tr>;
        })}</tbody></table>
      </div><p className={styles.notice}>재고 0은 품절로 올라가요.{variants.length > 200 ? ' 옵션은 최대 200개까지 등록해요.' : ''}</p></details>
    </section>
    <p className={styles.notice}>재고는 몰끼리 자동으로 맞춰지지 않아요.</p>
    <section className={styles.zone} aria-label="몰별 배송과 진열">
      {draft.selectedMalls.includes('zigzag') && <p>지그재그 · {sales.shipping.feeType === 'FREE' ? '무료 배송' : sales.shipping.baseFee == null ? '배송비 확인 필요' : `${Number(sales.shipping.baseFee).toLocaleString()}원`}
        {sales.shipping.feeType === 'CONDITIONAL_FREE' ? ` · ${Number(sales.shipping.conditionalAmount || 0).toLocaleString()}원 이상 무료` : ''} · {sales.shippingDays || 2}일 발송 · {context.returnAddressName || '반품지 확인 필요'}</p>}
      {draft.selectedMalls.includes('cafe24') && <p>카페24는 몰 기본 배송 설정</p>}
      {draft.selectedMalls.filter((mall) => mall !== 'ably').map((mall) => <p key={mall}>{MALL_LABELS[mall]} · {(draft.salesOverrides.listingMode || defaults[mall]?.listingMode) === 'ON_SALE' ? '진열과 판매를 켜요' : '진열 안 함으로 올려요'}</p>)}
      <details className={styles.disclosure} onToggle={disclosure}><summary aria-expanded="false" aria-controls="mall-sales">배송과 진열 바꾸기</summary><div id="mall-sales">
        <label className={styles.row}><span>등록 직후</span><select value={draft.salesOverrides.listingMode || ''} onChange={(event) => updateSale('listingMode', event.target.value)}><option value="">몰마다 저장한 상태</option><option value="HIDDEN">진열 안 함</option><option value="ON_SALE">진열과 판매 켬</option></select></label>
        {draft.selectedMalls.includes('zigzag') && <>
          <label className={styles.row}><span>배송비</span><select value={sales.shipping.feeType || ''} onChange={(event) => updateSale('shipping', { ...sales.shipping, feeType: event.target.value })}><option value="" disabled>배송비 정책 선택</option><option value="FREE">무료</option><option value="CHARGED">고정 배송비</option><option value="CONDITIONAL_FREE">조건부 무료</option></select></label>
          {['baseFee', 'conditionalAmount', 'jejuFee', 'isolatedFee'].map((key) => <label className={styles.row} key={key}><span>{{ baseFee: '기본 배송비', conditionalAmount: '무료 기준 금액', jejuFee: '제주 추가비', isolatedFee: '도서산간 추가비' }[key]}</span>{input(`shipping.${key}`, sales.shipping[key] ?? '', (value) => updateSale('shipping', { ...sales.shipping, [key]: value === '' ? 0 : Number(value) }), key)}</label>)}
          <label className={styles.row}><span>발송 소요일</span>{input('shippingDays', sales.shippingDays ?? 2, (value) => updateSale('shippingDays', Number(value)), '발송 소요일')}</label>
          {['totalFee', 'partialFee', 'exchangeFee'].map((key) => <label className={styles.row} key={key}><span>{{ totalFee: '전체 반품비', partialFee: '부분 반품비', exchangeFee: '교환비' }[key]}</span>{input(`returns.${key}`, sales.returns[key] ?? '', (value) => updateSale('returns', { ...sales.returns, [key]: Number(value) }), key)}</label>)}
        </>}
      </div></details>
    </section>
  </fieldset>;
}
