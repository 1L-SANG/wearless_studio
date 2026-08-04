import test from 'node:test';
import assert from 'node:assert/strict';

import {
  classifyMannequinReview,
  isNonRetryableMannequinRegenerateError,
  mannequinCanEnterStoryboard,
  mannequinDowngradeChoicesFromStoryboard,
  mannequinRegenerateFailureNotice,
  mannequinRequiresDowngradeChoice,
  mannequinDowngradeChoiceSucceededForCut,
  mannequinReviewAcknowledgedForCut,
  mannequinReviewBlocksStoryboard,
  mannequinVersionAriaLabel,
  reviewReasonCopy,
} from '../../src/features/mannequin/reviewState.js';

test('saved downgrade decisions hydrate per cut after reload', () => {
  const choices = mannequinDowngradeChoicesFromStoryboard([
    {
      id: 'hero',
      downgradeDecision: {
        type: 'product_hero',
        sourceCutId: 'cut-1',
        decidedAt: '2026-08-04T00:00:00.000Z',
      },
    },
    {
      id: 'fit',
      downgradeDecision: {
        type: 'fit_reference',
        sourceCutId: 'cut-2',
        decidedAt: '2026-08-04T01:00:00.000Z',
      },
    },
    {
      id: 'stale',
      downgradeDecision: {
        type: 'unsupported',
        sourceCutId: 'cut-3',
        decidedAt: '2026-08-04T02:00:00.000Z',
      },
    },
  ]);

  assert.deepEqual(choices['cut-1'], {
    cutId: 'cut-1',
    choice: 'product_hero',
    status: 'saved',
    decidedAt: '2026-08-04T00:00:00.000Z',
  });
  assert.deepEqual(choices['cut-2'], {
    cutId: 'cut-2',
    choice: 'fit_reference',
    status: 'saved',
    decidedAt: '2026-08-04T01:00:00.000Z',
  });
  assert.equal(choices['cut-3'], undefined);
});

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

test('structured QC warnings and failed checks are shown as concrete review reasons', () => {
  const state = classifyMannequinReview({
    id: 'structured-review',
    qcScores: {
      outcome: 'needs_review',
      structuredQC: {
        overallDecision: 'review',
        warnings: ['qc_unavailable:color_fidelity', 'manual_review_required',
          'specialized_qc_unavailable:material'],
        checks: [{ check: 'pattern_fidelity', status: 'fail', score: 0.2 }],
      },
    },
  });

  assert.equal(state.visibleReview, true);
  assert.match(state.description, /상품 색상/);
  assert.match(state.description, /사용자 확인/);
  assert.match(state.description, /체크·스트라이프/);
  assert.match(state.description, /레이스·시스루/);
});

test('an unavailable structured check is explained once even when warning and check rows overlap', () => {
  const state = classifyMannequinReview({
    id: 'unavailable-review',
    qcScores: {
      outcome: 'needs_review',
      structuredQC: {
        warnings: ['qc_unavailable:composition'],
        checks: [{ check: 'composition', status: 'unavailable', score: null }],
      },
    },
  });

  assert.equal(
    state.description.match(/마네킹 위치·크롭·여백을 확인해 주세요\./g)?.length,
    1,
  );
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

test('pattern fidelity failures require an explicit downgrade choice before storyboard entry', () => {
  const cut = {
    id: 'stripe-v3',
    qcScores: {
      outcome: 'needs_review',
      structuredQC: {
        checks: [{ check: 'pattern_fidelity', status: 'fail', score: 0.18 }],
      },
    },
  };

  assert.equal(mannequinRequiresDowngradeChoice(cut), true);
  assert.equal(mannequinCanEnterStoryboard(cut, 'stripe-v3'), false);
  assert.equal(mannequinCanEnterStoryboard(cut, 'stripe-v3', { cutId: 'stripe-v3', choice: 'fit_reference' }), true);
  assert.equal(mannequinDowngradeChoiceSucceededForCut(cut, { cutId: 'stripe-v3', choice: 'product_hero' }), true);
  assert.equal(mannequinDowngradeChoiceSucceededForCut(cut, { cutId: 'other', choice: 'product_hero' }), false);
});

test('unsupported or failed 2d texture projection requires downgrade choice even when visible review is acknowledged', () => {
  const cut = {
    id: 'projection-v4',
    qcScores: {
      outcome: 'needs_review',
      hybridComposite: {
        applied: true,
        needsReview: true,
        textureProjection: {
          ok: false,
          reason: 'unsupported_pattern',
        },
      },
    },
  };

  assert.equal(mannequinRequiresDowngradeChoice(cut), true);
  assert.equal(mannequinCanEnterStoryboard(cut, 'projection-v4'), false);
  assert.equal(mannequinCanEnterStoryboard(cut, 'projection-v4', { cutId: 'projection-v4', choice: 'fit_reference' }), true);
});

test('shadow hybrid metadata is observability only and never changes review or downgrade state', () => {
  const cut = {
    id: 'shadow-v5',
    version: 5,
    qcScores: {
      outcome: 'auto_pass',
      hybridComposite: {
        mode: 'shadow',
        applied: false,
        needsReview: true,
        failureReason: 'unsupported_pattern',
        textureProjection: {
          ok: false,
          reason: 'projection_low_confidence',
        },
      },
    },
  };
  const state = classifyMannequinReview(cut);

  assert.equal(state.level, 'passed');
  assert.equal(state.hardBlocked, false);
  assert.equal(state.visibleReview, false);
  assert.equal(state.badge, '통과');
  assert.equal(mannequinRequiresDowngradeChoice(cut), false);
  assert.equal(mannequinCanEnterStoryboard(cut, null), true);
  assert.equal(mannequinVersionAriaLabel(cut), '버전 5 선택, 품질 통과');
});

test('shadow texture projection telemetry does not create a downgrade inside enforced hybrid', () => {
  const cut = {
    id: 'projection-shadow-v1',
    qcScores: {
      outcome: 'auto_pass',
      hybridComposite: {
        mode: 'enforce',
        applied: true,
        deterministicPassed: true,
        textureProjection: {
          mode: 'shadow',
          ok: false,
          reason: 'projection_low_confidence',
        },
      },
    },
  };

  assert.equal(mannequinRequiresDowngradeChoice(cut), false);
  assert.equal(mannequinCanEnterStoryboard(cut, null), true);
  assert.deepEqual(classifyMannequinReview(cut).reasons, []);
});

test('structured pattern failure still requires downgrade even when hybrid shadow metadata is ignored', () => {
  const cut = {
    id: 'shadow-structured-v6',
    qcScores: {
      outcome: 'needs_review',
      hybridComposite: {
        mode: 'shadow',
        applied: false,
        needsReview: true,
        failureReason: 'unsupported_pattern',
      },
      structuredQC: {
        checks: [{ check: 'pattern_fidelity', status: 'fail', score: 0.1 }],
      },
    },
  };

  assert.equal(classifyMannequinReview(cut).hardBlocked, false);
  assert.equal(mannequinRequiresDowngradeChoice(cut), true);
  assert.equal(mannequinCanEnterStoryboard(cut, 'shadow-structured-v6'), false);
  assert.equal(mannequinCanEnterStoryboard(cut, 'shadow-structured-v6', { cutId: 'shadow-structured-v6', choice: 'product_hero' }), true);
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
  assert.deepEqual(notice.actions, ['product_hero', 'regenerate']);
});

test('generic regenerate failure offers regeneration only', () => {
  const notice = mannequinRegenerateFailureNotice({
    code: 'provider_failed',
    message: '새 버전을 만들지 못했어요.',
  });

  assert.deepEqual(notice.actions, ['regenerate']);
});

test('missing protected components explains why the generated candidate was blocked', () => {
  const notice = mannequinRegenerateFailureNotice({
    code: 'hybrid_composite_failed_closed',
    details: {
      failureReason: 'protected_component_missing',
      hybridComposite: { failureReason: 'protected_component_missing' },
    },
  });

  assert.match(notice.description, /카라·앞여밈/);
  assert.deepEqual(notice.actions, ['product_hero', 'regenerate']);
});

// ── §F: 하드 실패 UI 계약 ──────────────────────────────────────────────────────
// 카라·플래킷이 복원되지 않은 합성은 usable candidate 가 아니다. 실패 화면은 원본
// 실사 Hero 와 재생성만 제공하고, '핏 참고용' 우회로는 노출되지 않아야 한다.

test('hard composite failure offers exactly product_hero and regenerate', () => {
  const notice = mannequinRegenerateFailureNotice({
    code: 'hybrid_composite_failed_closed',
    details: { failureReason: 'protected_component_missing' },
  });
  assert.equal(notice.level, 'blocked');
  assert.deepEqual(notice.actions, ['product_hero', 'regenerate']);
  assert.ok(!notice.actions.includes('fit_reference'),
    '핏 참고용은 하드 실패의 우회로가 될 수 없다');
});

test('hard composite failure states that nothing was saved or charged', () => {
  const notice = mannequinRegenerateFailureNotice({
    code: 'hybrid_composite_failed_closed',
    details: { failureReason: 'interface_seam' },
  });
  assert.match(notice.note, /저장하지 않았고/);
  assert.match(notice.note, /차감되지 않았어요/);
});

test('hard failure notice carries no generated image reference', () => {
  const notice = mannequinRegenerateFailureNotice({
    code: 'hybrid_composite_failed_closed',
    details: { failureReason: 'protected_component_missing', imageUrl: 'https://x/y.png' },
  });
  const serialized = JSON.stringify(notice);
  assert.ok(!serialized.includes('http'), '실패 카드에 생성 이미지가 실리면 안 된다');
  assert.ok(!('imageUrl' in notice));
});

test('new composite failure reasons all render as blocked with the same two actions', () => {
  for (const reason of ['interface_seam', 'boundary_chroma_discontinuity',
    'drape_lost', 'chroma_cast_excessive', 'geometry_carrier_mismatch']) {
    const notice = mannequinRegenerateFailureNotice({
      code: 'hybrid_composite_failed_closed',
      details: { failureReason: reason },
    });
    assert.equal(notice.level, 'blocked', reason);
    assert.deepEqual(notice.actions, ['product_hero', 'regenerate'], reason);
  }
});
