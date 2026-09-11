import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { validityLabel } from '../../src/features/model/modelProfilePreview.js';
import { formatValidity, formatValidUntil, toBrowseModel } from '../../src/features/facemarket-landing/data/publicModels.js';
import { normalizeSettlementRow } from '../../src/features/facemarket-landing/payoutData.js';
import { seoulDate } from '../../src/lib/datetime.js';
import { FACEMARKET_PRICING } from '../../src/lib/facemarketPricing.js';
import { transformWithEsbuild } from 'vite';
import { createElement, Fragment } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');

test('license issuance no longer contains a duration selector', () => {
  const source = read('src/features/model/ModelLicense.jsx');
  // 선택 UI(VALIDITY 칩, validDays 상태, 조건 단계의 유효기간 라벨)는 없어야 하지만,
  // 발급된 증서 카드가 "유효기간: 철회 시까지" 를 보여주는 것은 남는다.
  assert.doesNotMatch(source, /VALIDITY|validDays|TERM_STEPS\.validity|termLabel\}>유효기간/);
  assert.match(source, /철회 시까지/);
});

test('createLicense omits validDays from its request body', () => {
  const source = read('src/lib/api/facemarket.js');
  const create = source.slice(source.indexOf('export function createLicense('), source.indexOf('export function listLicenses('));
  assert.match(create, /body:\s*\{/);
  assert.doesNotMatch(create, /validDays/);
});

test('missing or nonfinite profile duration means until withdrawal', () => {
  for (const value of [null, undefined, NaN, Infinity, -Infinity]) {
    assert.equal(validityLabel(value), '철회 시까지');
  }
});

test('public model duration and expiry remain visible for permanent licenses', () => {
  assert.equal(formatValidity(null), '철회 시까지');
  assert.equal(formatValidUntil(null), '철회 시까지');
  const model = toBrowseModel({ id: 'permanent', closeupImageUrl: '/cover.webp', license: { validDays: null, validUntil: null } });
  assert.equal(model.license.validity, '철회 시까지');
  assert.equal(model.license.validUntilText, '철회 시까지');
});

test('settlement status does not expire a permanent active license', () => {
  const row = { licenseId: 'permanent', createdAt: '2026-09-11T00:00:00Z' };
  const license = { id: 'permanent', status: 'active', licenseValidUntil: null };
  const now = new Date('2036-09-11T00:00:00Z');
  assert.equal(normalizeSettlementRow(row, [license], now).status, '활성');
  assert.equal(normalizeSettlementRow(row, [{ ...license, status: 'revoked' }], now).status, '만료');
  assert.equal(normalizeSettlementRow(row, [{ ...license, licenseValidUntil: '2027-09-11T00:00:00Z' }], now).status, '만료');
});

test('seller model details retain the expiry row for permanent licenses and dated history', async () => {
  const source = read('src/features/analysis/AnalysisForm.jsx');
  // Compile the actual private component without loading the unrelated analysis form.
  const component = source.slice(source.indexOf('const _won ='), source.indexOf('// 글자 폭 추정'));
  const { code } = await transformWithEsbuild(component, 'ModelDetailModal.jsx', { jsx: 'transform' });
  for (const [validUntil, expected] of [[null, '철회 시까지'], ['2027-09-07T22:00:00Z', '2027. 9. 8.까지']]) {
    const states = [{ validUntil, allowedUse: [] }, 'ready', null];
    const Detail = new Function('React', 'useState', 'useEffect', 'seoulDate', 'FACEMARKET_PRICING', 'Modal', 'ModelThumb', 'Icon', 'Button', `${code}; return ModelDetailModal;`)(
      { createElement, Fragment }, () => [states.shift(), () => {}], () => {}, seoulDate, FACEMARKET_PRICING,
      ({ children }) => createElement('div', null, children), () => null, () => null, ({ children }) => createElement('button', null, children),
    );
    const html = renderToStaticMarkup(createElement(Detail, { model: { id: 'model-1', displayName: '모델' } }));
    assert.ok(html.includes(`<div class="lic-valid">${expected}</div>`), html);
  }
});
