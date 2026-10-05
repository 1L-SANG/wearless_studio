import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  analysisForCutFitProfile,
  cutFitProfileRecord,
} from '../../src/features/mannequin/cutFitProfile.js';

test('버전 핏 기록은 null 사진 그대로와 기록 없는 옛 컷을 구분한다', () => {
  const current = { fitProfile: { axes: { fit: 'over' } } };
  const photoAsIs = { fitProfileRecorded: true, fitProfile: null };
  const legacy = {};

  assert.deepEqual(cutFitProfileRecord(photoAsIs), { recorded: true, profile: null });
  assert.equal(analysisForCutFitProfile(current, photoAsIs).fitProfile, null);
  assert.equal(analysisForCutFitProfile(current, legacy), current);
});

test('썸네일 선택과 진행은 선택 버전의 fitProfile을 저장한다', () => {
  const source = readFileSync(
    new URL('../../src/features/mannequin/Mannequin.jsx', import.meta.url),
    'utf8',
  );
  const choose = source.slice(
    source.indexOf('const chooseCut = (cutId) =>'),
    source.indexOf('const applyToneRenderToSelectedCut'),
  );
  const proceed = source.slice(
    source.indexOf('const continueWithSelectedVersion = async'),
    source.indexOf('const onContinueCurrentVersion ='),
  );

  assert.match(choose, /cutFitProfileRecord\(cut\)/);
  assert.match(choose, /api\.saveAnalysis\(projectId, \{ fitProfile: record\.profile \}\)/);
  assert.match(proceed, /const profile = record\.recorded \? record\.profile : legacyProfile/);
  assert.match(proceed, /await selectMannequin\(selected\.id\)/);
  assert.match(proceed, /await api\.saveAnalysis\(projectId, \{ fitProfile: profile \}\)/);
});

test('재시도 소진 패널에서도 현재 버전 진행 버튼이 노출된다', () => {
  const source = readFileSync(
    new URL('../../src/features/mannequin/Mannequin.jsx', import.meta.url),
    'utf8',
  );

  assert.match(source, /selected && \(!showWaitPanel \|\| waitFailure\)/);
  assert.match(source, /onClick=\{waitFailure \? onContinueCurrent : onContinue\}/);
  assert.match(source, /`현재 버전으로 진행 · \$\{aiCutCount/);
});
