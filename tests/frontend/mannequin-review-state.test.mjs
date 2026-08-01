import test from 'node:test';
import assert from 'node:assert/strict';

import {
  classifyMannequinReview,
  isNonRetryableMannequinRegenerateError,
  mannequinCanEnterStoryboard,
  mannequinRegenerateFailureNotice,
  mannequinReviewAcknowledgedForCut,
  mannequinReviewBlocksStoryboard,
  mannequinVersionAriaLabel,
  reviewReasonCopy,
} from '../../src/features/mannequin/reviewState.js';

test('cut without qc is normal and does not block storyboard entry', () => {
  const state = classifyMannequinReview({ id: 'old-good', version: 1 });

  assert.equal(state.level, 'normal');
  assert.equal(state.hardBlocked, false);
  assert.equal(state.visibleReview, false);
  assert.equal(state.badge, null);
});

test('regenerate outcome hard-blocks the selected cut with typed Korean copy', () => {
  const state = classifyMannequinReview({
    id: 'bad',
    qcScores: {
      outcome: 'regenerate',
      critical_errors: ['logo_changed'],
    },
  });

  assert.equal(state.level, 'blocked');
  assert.equal(state.hardBlocked, true);
  assert.match(state.title, /사용할 수 없어요/);
  assert.match(state.description, /로고/);
});

test('failed hybrid application hard-blocks even without regenerate outcome', () => {
  const state = classifyMannequinReview({
    id: 'hybrid-failed',
    qcScores: {
      mode: 'hybrid',
      applied: false,
      outcome: 'auto_pass',
      failureReason: 'all_edits',
    },
  });

  assert.equal(state.level, 'blocked');
  assert.equal(state.hardBlocked, true);
  assert.match(state.description, /되돌렸어요/);
});

test('real v6 nested hybridComposite failure hard-blocks the selected cut', () => {
  const cut = {
    qcScores: {
      outcome: 'needs_review',
      hybridComposite: {
        applied: false,
        needsReview: true,
        failureReason: 'geometry_carrier_mismatch',
      },
    },
  };
  const state = classifyMannequinReview(cut);

  assert.equal(state.level, 'blocked');
  assert.equal(state.hardBlocked, true);
  assert.equal(state.visibleReview, false);
  assert.match(state.description, /마네킹 구조 기준/);
  assert.equal(mannequinReviewBlocksStoryboard(cut), true);
});

test('needs_review outcome shows visible review but allows storyboard entry', () => {
  const state = classifyMannequinReview({
    id: 'review',
    qcScores: {
      outcome: 'needs_review',
      componentsNeedingReview: ['product_fidelity', 'image_quality'],
    },
  });

  assert.equal(state.level, 'review');
  assert.equal(state.hardBlocked, false);
  assert.equal(state.visibleReview, true);
  assert.equal(state.badge, '검토');
  assert.match(state.description, /상품 색상/);
  assert.match(state.description, /선명도/);
});

test('applied hybrid with review components is a visible review cut', () => {
  const state = classifyMannequinReview({
    id: 'applied-review',
    qcScores: {
      mode: 'hybrid',
      applied: true,
      outcome: 'auto_pass',
      needsReview: ['series_consistency'],
    },
  });

  assert.equal(state.level, 'review');
  assert.equal(state.hardBlocked, false);
  assert.equal(state.visibleReview, true);
});

test('nested applied hybridComposite components show visible review', () => {
  const cut = {
    id: 'cut-review-6',
    version: 6,
    qcScores: {
      outcome: 'auto_pass',
      hybridComposite: {
        applied: true,
        componentsNeedingReview: ['image_quality'],
      },
    },
  };
  const state = classifyMannequinReview(cut);

  assert.equal(state.level, 'review');
  assert.equal(state.hardBlocked, false);
  assert.equal(state.visibleReview, true);
  assert.match(state.description, /선명도/);
  assert.equal(mannequinVersionAriaLabel(cut), '버전 6 선택, 품질 검토 필요');
  assert.equal(mannequinCanEnterStoryboard(cut, null), false);
  assert.equal(mannequinCanEnterStoryboard(cut, 'other-cut'), false);
  assert.equal(mannequinCanEnterStoryboard(cut, 'cut-review-6'), true);
});

test('direct cut-level qc fields are classified without a nested qc envelope', () => {
  const state = classifyMannequinReview({
    id: 'direct-review',
    mode: 'hybrid',
    applied: true,
    componentsNeedingReview: ['physical_naturalness'],
  });

  assert.equal(state.level, 'review');
  assert.equal(state.visibleReview, true);
  assert.match(state.description, /착용 형태/);
});

test('reason copy falls back for unknown typed reasons', () => {
  assert.equal(reviewReasonCopy('new_reason'), '품질 검토가 필요한 항목이 있어요.');
});

test('version aria-label includes qc status for blocked, passed, and unreviewed cuts', () => {
  assert.equal(mannequinVersionAriaLabel({
    version: 7,
    qcScores: {
      outcome: 'needs_review',
      hybridComposite: {
        applied: false,
        failureReason: 'geometry_carrier_mismatch',
      },
    },
  }), '버전 7 선택, 품질 차단');
  assert.equal(mannequinVersionAriaLabel({
    version: 8,
    qcScores: { outcome: 'auto_pass' },
  }), '버전 8 선택, 품질 통과');
  assert.equal(mannequinVersionAriaLabel({ version: 1 }), '버전 1 선택, 품질 판정 없음');
});

test('storyboard entry acknowledgement is scoped to the exact selected cut', () => {
  const softReview = {
    id: 'soft-v6',
    qcScores: {
      outcome: 'needs_review',
      componentsNeedingReview: ['product_fidelity'],
    },
  };
  const hardBlocked = {
    id: 'blocked-v7',
    qcScores: {
      outcome: 'regenerate',
      critical_errors: ['logo_changed'],
    },
  };
  const passed = {
    id: 'passed-v8',
    qcScores: { outcome: 'auto_pass' },
  };
  const legacyNormal = { id: 'legacy-v1' };

  assert.equal(mannequinReviewAcknowledgedForCut(softReview, 'soft-v6'), true);
  assert.equal(mannequinReviewAcknowledgedForCut(softReview, 'passed-v8'), false);
  assert.equal(mannequinCanEnterStoryboard(softReview, null), false);
  assert.equal(mannequinCanEnterStoryboard(softReview, 'passed-v8'), false);
  assert.equal(mannequinCanEnterStoryboard(softReview, 'soft-v6'), true);
  assert.equal(mannequinCanEnterStoryboard(hardBlocked, 'blocked-v7'), false);
  assert.equal(mannequinCanEnterStoryboard(passed, null), true);
  assert.equal(mannequinCanEnterStoryboard(legacyNormal, null), true);
});

test('hybrid failed-closed regenerate error is non-retryable', () => {
  assert.equal(isNonRetryableMannequinRegenerateError({
    code: 'hybrid_composite_failed_closed',
    status: 503,
  }), true);
  assert.equal(isNonRetryableMannequinRegenerateError({
    code: 'job_failed',
    details: { error: 'hybrid_composite_failed_closed' },
    status: 503,
  }), true);
  assert.equal(isNonRetryableMannequinRegenerateError({ status: 503 }), false);
});

test('hybrid failed-closed error produces a persistent typed failure notice', () => {
  const notice = mannequinRegenerateFailureNotice({
    code: 'hybrid_composite_failed_closed',
    message: '마네킹컷 생성에 실패했어요.',
    details: {
      error: 'hybrid_composite_failed_closed',
      failureReason: 'geometry_carrier_mismatch',
      hybridComposite: {
        applied: false,
        failureReason: 'geometry_carrier_mismatch',
      },
    },
  });

  assert.equal(notice.level, 'blocked');
  assert.equal(notice.reason, 'geometry_carrier_mismatch');
  assert.match(notice.title, /합성 검증/);
  assert.match(notice.description, /마네킹 구조 기준/);
  assert.match(notice.note, /저장하지 않았고/);
  assert.match(notice.note, /크레딧도 차감되지 않았어요/);
});
