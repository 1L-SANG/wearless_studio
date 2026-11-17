import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';

import { axesFor, normalizeAnalysisFit } from '../../src/lib/fitAxes.js';
import * as fitAxes from '../../src/lib/fitAxes.js';
import { registeredFitExampleKeys } from '../../src/lib/fitExampleImages.js';

test('women top fit uses the same four-value vocabulary as men', () => {
  const values = (gender) => axesFor('top', gender).fit.map(({ value }) => value);
  assert.deepEqual(values('women'), ['slim', 'regular', 'semi_over', 'over']);
  assert.deepEqual(values('men'), ['slim', 'regular', 'semi_over', 'over']);
  assert.equal(registeredFitExampleKeys().includes('top-women-fit-tight'), false);
});

test('legacy tight analysis values load as slim without mutating stored data', () => {
  const legacy = {
    fit: 'tight',
    fitProfile: { category: 'top', gender: 'women', axes: { fit: 'tight', length: 'basic' } },
  };
  const normalized = normalizeAnalysisFit(legacy);
  assert.equal(normalized.fit, 'slim');
  assert.deepEqual(normalized.fitProfile.axes, { fit: 'slim', length: 'basic' });
  assert.equal(legacy.fitProfile.axes.fit, 'tight');
});

test('legacy top length values load with the intuitive semi names', () => {
  const legacy = {
    fitProfile: {
      category: 'pants',
      gender: 'women',
      axes: { cut: 'straight' },
      matchingFit: {
        fitCategory: 'top',
        axes: { length: 'crop_basic' },
      },
    },
  };
  const normalized = normalizeAnalysisFit(legacy);
  assert.equal(normalized.fitProfile.matchingFit.axes.length, 'semi_crop');
  assert.equal(legacy.fitProfile.matchingFit.axes.length, 'crop_basic');
});

test('current analysis values keep their object identity', () => {
  const current = { fit: 'regular', fitProfile: null };
  assert.equal(normalizeAnalysisFit(current), current);
});

test('상의 기장 축은 두 중간값을 포함한 5단계이고 남녀가 같다', () => {
  const values = (gender) => axesFor('top', gender).length.map(({ value }) => value);
  const expected = ['crop', 'semi_crop', 'basic', 'semi_long', 'long'];
  assert.deepEqual(values('women'), expected);
  assert.deepEqual(values('men'), expected);
});

test('상의 소매 기장 축은 소매 끝 지점 4단계이고 남녀가 같다', () => {
  const values = (gender) => axesFor('top', gender).sleeve.map(({ value }) => value);
  const expected = ['cap', 'cap_short', 'short', 'elbow'];
  assert.deepEqual(values('women'), expected);
  assert.deepEqual(values('men'), expected);
});

test('소매 기장 축은 fit·length 뒤에 온다 — 카탈로그 순서가 UI 스텝 순서다', () => {
  assert.deepEqual(Object.keys(axesFor('top', 'women')), ['fit', 'length', 'sleeve']);
  assert.deepEqual(Object.keys(axesFor('top', 'men')), ['fit', 'length', 'sleeve']);
});

test('default mannequin and matching catalogs retain every original entry byte for byte', () => {
  const catalogs = ['top', 'pants', 'skirt', 'dress', 'outer'].flatMap((category) =>
    ['women', 'men'].map((gender) => axesFor(category, gender)));
  assert.equal(createHash('sha256').update(JSON.stringify(catalogs)).digest('hex'), '2a3fdb0d3ba94057adbe6babed470103387ca83c22bfeeebb0a318a1b8ece5ab');
});

test('analysis catalog opts into new values without changing the default accessor', () => {
  assert.equal(typeof fitAxes.analysisAxesFor, 'function');
  assert.deepEqual(fitAxes.analysisAxesFor('top', 'women').length.map(({ value }) => value),
    ['crop', 'slight_crop', 'semi_crop', 'basic', 'semi_long', 'long']);
  for (const gender of ['women', 'men']) {
    assert.deepEqual(fitAxes.analysisAxesFor('outer', gender).sleeve.map(({ value }) => value),
      ['cap', 'cap_short', 'short', 'above_elbow', 'elbow', 'above_wrist', 'wrist', 'back_of_hand', 'fingers']);
    assert.equal(axesFor('outer', gender).sleeve, undefined);
    assert.equal(axesFor('dress', gender).sleeve, undefined);
  }
});

test('server and frontend mirror catalog values, labels, order and analysis scope', () => {
  const server = JSON.parse(execFileSync(process.env.PAGE_TEMPLATE_PYTHON || 'server/.venv/bin/python', ['-c',
    "import json,sys;sys.path.insert(0,'server');from app.agents.fit_axes import FIT_AXES;print(json.dumps(FIT_AXES))"], { encoding: 'utf8' }));
  const shape = (catalog) => Object.entries(catalog).map(([category, axes]) => [category,
    Object.entries(axes).map(([axis, genders]) => [axis, Object.entries(genders).map(([gender, entries]) => [gender,
      entries.map(({ value, label, scope, promptEn }) => ({ value, label, ...(scope ? { scope, promptEn } : {}) }))])])]);
  assert.deepEqual(shape(fitAxes.FIT_AXES), shape(server));
});

test('legacy analysis migration retains per-axis sources', () => {
  const profile = { category: 'top', gender: 'women', axes: { fit: 'tight', length: 'basic_long' },
    axisSources: { fit: 'seller', length: 'measure' } };
  const result = normalizeAnalysisFit({ fitProfile: profile });
  assert.deepEqual(result.fitProfile.axisSources, { fit: 'seller', length: 'measure' });
  assert.deepEqual(result.fitProfile.axes, { fit: 'slim', length: 'semi_long' });
  assert.equal(profile.axes.fit, 'tight');
});
