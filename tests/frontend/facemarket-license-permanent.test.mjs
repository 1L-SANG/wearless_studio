import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { validityLabel } from '../../src/features/model/modelProfilePreview.js';
import { formatValidity, formatValidUntil, toBrowseModel } from '../../src/features/facemarket-landing/data/publicModels.js';
import { normalizeSettlementRow } from '../../src/features/facemarket-landing/payoutData.js';

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
