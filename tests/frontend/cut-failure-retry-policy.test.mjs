import test from 'node:test';
import assert from 'node:assert/strict';
import { failedCutPresentation } from '../../src/lib/cutFailureMessages.js';

const independentReasons = [
  'no_garment_photo', 'matching_asset_unavailable', 'no_angle_photo',
  'identity_scope_mismatch', 'real_model_studio_only', 'pose_direction_incompatible',
  'invalid_cut_spec', 'detail_target_unavailable', 'invalid_detail_region',
  'detail_reference_required', 'detail_color_reference_required',
  'detail_source_unavailable', 'detail_target_color_changed', 'detail_target_source_changed',
];
const dependentReasons = ['similar_cut_source_failed', 'duplicate_cut_source_failed'];

for (const reason of independentReasons) {
  test(`${reason}: retry is available even when the saved flag is false`, () => {
    for (const genFailRetryable of [undefined, false, true]) {
      assert.deepEqual(failedCutPresentation({ genFailReason: reason, genFailRetryable }), {
        retryable: true, guidance: '저희 쪽에서 다시 만들 수 있어요',
      });
    }
  });
}

for (const reason of dependentReasons) {
  test(`${reason}: the source cut is required even when the saved flag is true`, () => {
    for (const genFailRetryable of [undefined, false, true]) {
      assert.deepEqual(failedCutPresentation({ genFailReason: reason, genFailRetryable }), {
        retryable: false, guidance: '원본 컷을 먼저 만들어 주세요',
      });
    }
  });
}

test('missing and unknown failure reasons keep retry available despite old saved flags', () => {
  for (const genFailReason of [undefined, null, '', 'provider_error', 'new_failure_reason']) {
    assert.deepEqual(failedCutPresentation({ genFailReason, genFailRetryable: false }), {
      retryable: true, guidance: '저희 쪽에서 다시 만들 수 있어요',
    });
  }
  assert.deepEqual(failedCutPresentation(), {
    retryable: true, guidance: '저희 쪽에서 다시 만들 수 있어요',
  });
});

test('failure guidance never asks the seller to upload, shoot, or select again', () => {
  for (const genFailReason of [...independentReasons, ...dependentReasons, undefined, 'unknown']) {
    assert.doesNotMatch(failedCutPresentation({ genFailReason }).guidance,
      /올려|업로드|촬영해|다시 선택|골라/);
  }
});
