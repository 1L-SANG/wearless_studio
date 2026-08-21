import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  ENROLLMENT_ANGLES,
  ENROLLMENT_STEPS,
  enrollmentReasonMessage,
  nextEnrollmentStep,
} from '../../src/features/model/biometricEnrollment.js';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');

test('retouched photos are presented front, 45 degrees, then side', () => {
  assert.deepEqual(ENROLLMENT_ANGLES.map(({ value }) => value), [
    'front', 'angle45', 'side',
  ]);
  assert.equal(ENROLLMENT_ANGLES[1].label, '45도');
  assert.match(ENROLLMENT_ANGLES[1].guide, /45도|반측면/);
  assert.equal(ENROLLMENT_ANGLES[2].label, '측면');
  assert.match(ENROLLMENT_ANGLES[2].guide, /90도|옆모습/);
});

test('server status restores the next safe enrollment step', () => {
  assert.deepEqual(ENROLLMENT_STEPS, [
    'consent', 'photos', 'liveness', 'identity', 'processing', 'terms', 'done',
  ]);
  assert.equal(nextEnrollmentStep(null), 'consent');
  assert.equal(nextEnrollmentStep({ status: 'photos_pending', photos: [] }), 'photos');
  assert.equal(nextEnrollmentStep({ status: 'liveness_pending', photos: [{}, {}, {}] }), 'liveness');
  assert.equal(nextEnrollmentStep({ status: 'processing', photos: [{}, {}, {}] }), 'processing');
  assert.equal(nextEnrollmentStep({ status: 'asset_building', photos: [{}, {}, {}] }), 'processing');
  assert.equal(nextEnrollmentStep({ status: 'license_pending', photos: [{}, {}, {}] }), 'terms');
  assert.equal(nextEnrollmentStep({ status: 'vc_pending', photos: [{}, {}, {}] }), 'terms');
  assert.equal(nextEnrollmentStep({ status: 'passed', photos: [{}, {}, {}] }), 'done');
  assert.equal(nextEnrollmentStep({ status: 'failed', reason: 'face_match_failed' }), 'failed');
});

test('raw biometric reasons collapse to actionable copy', () => {
  assert.equal(enrollmentReasonMessage('id_portrait_unavailable'), '신분증 사진을 확인할 수 없어요.');
  assert.equal(enrollmentReasonMessage('face_match_failed'), '얼굴 일치 확인에 실패했어요.');
  assert.equal(enrollmentReasonMessage('unknown-provider-detail'), '인증을 완료하지 못했어요. 다시 시도해 주세요.');
});

test('the browser wizard keeps raw authentication material in memory only', () => {
  const registerSource = read('../../src/features/model/ModelRegister.jsx');
  const livenessSource = read('../../src/features/model/FaceLivenessStep.jsx');

  assert.match(registerSource, /completeEnrollment\(enrollment\.id, \{ sessionId, token \}\)/);
  assert.match(registerSource, /새 생체 등록 시작/);
  assert.match(registerSource, /localStorage\.setItem\([^,]+,\s*deviceId\)/);
  assert.doesNotMatch(registerSource, /localStorage\.setItem\([^)]*(token|session|credentials|image)/i);
  assert.doesNotMatch(registerSource, /sessionStorage|indexedDB/i);
  assert.doesNotMatch(registerSource, /console\.(?:log|info|warn|error)/);
  assert.match(livenessSource, /FaceLivenessDetectorCore/);
  assert.match(livenessSource, /region="us-east-1"/);
  assert.match(livenessSource, /config=\{config\}/);
  assert.doesNotMatch(livenessSource, /localStorage|sessionStorage|indexedDB|console\./i);
});

test('a liveness interruption cancels the enrollment before a fresh retry', () => {
  const apiSource = read('../../src/lib/api/facemarket.js');
  const registerSource = read('../../src/features/model/ModelRegister.jsx');
  const finishIdentitySource = registerSource.slice(
    registerSource.indexOf('const finishIdentity'),
    registerSource.indexOf("if (step === 'loading')"),
  );

  assert.match(apiSource, /export function cancelEnrollment/);
  assert.match(registerSource, /await cancelEnrollment\(enrollmentId\)/);
  assert.match(registerSource, /setEnrollment\(null\)/);
  assert.match(registerSource, /enrollmentReasonMessage\('liveness_retry'\)/);
  assert.match(registerSource, /issuedLivenessEnrollmentRef/);
  assert.match(registerSource, /if \(enrollmentId\) cancelEnrollment\(enrollmentId\)\.catch/);
  assert.match(registerSource, /setStep\('cancel_failed'\)/);
  assert.match(registerSource, /등록 취소 다시 시도/);
  assert.doesNotMatch(registerSource, /서버 sweep이 재시도/);
  assert.match(finishIdentitySource, /catch(?: \(requestError\))? \{\s*await abandonLiveness\(\);/);
  assert.doesNotMatch(registerSource, /resetLiveness|reuse(?:Photos|Enrollment)/i);
});

test('enrollment terms and routes cannot revive direct face licensing', () => {
  const apiSource = read('../../src/lib/api/facemarket.js');
  const uploadSource = read('../../src/features/model/ModelFaceUpload.jsx');
  const licenseSource = read('../../src/features/model/ModelLicense.jsx');
  const hubSource = read('../../src/features/model/ModelHub.jsx');
  const appSource = read('../../src/App.jsx');

  assert.match(apiSource, /body:\s*\{ enrollmentId, allowedUse, forbiddenUse, unitPrice, validDays \}/);
  assert.doesNotMatch(apiSource, /fd\.append\(['"]face['"]/);
  assert.doesNotMatch(apiSource, /fd\.append\(['"]profile_id['"]/);
  assert.match(uploadSource, /angles = ENROLLMENT_ANGLES/);
  assert.match(licenseSource, /enrollmentId/);
  assert.doesNotMatch(licenseSource, /profileId|faceBlob/);
  assert.doesNotMatch(hubSource, /buildMyModelAssets|onBuildAssets|자산 생성 중/);
  assert.match(appSource, /function RequireOwnedModel\(\)/);
  assert.match(appSource, /path="generate" element=\{<RequireVerifiedModel \/>\}/);
});
