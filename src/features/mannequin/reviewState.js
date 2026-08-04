const OUTCOME_AUTO_PASS = 'auto_pass';
const OUTCOME_NEEDS_REVIEW = 'needs_review';
const OUTCOME_REGENERATE = 'regenerate';

const REASON_COPY = {
  product_fidelity: '상품 색상·패턴·로고 재현을 확인해 주세요.',
  physical_naturalness: '착용 형태나 드레이프가 자연스러운지 확인해 주세요.',
  image_quality: '선명도, 크롭, 이미지 깨짐을 확인해 주세요.',
  series_consistency: '다른 컷과 같은 세트처럼 보이는지 확인해 주세요.',
  critical_errors: '출고 전 반드시 수정이 필요한 결함이 감지됐어요.',
  full_body_crop: '마네킹 전신 구도가 잘렸어요.',
  missing_lower_body: '하체 일부가 누락됐어요.',
  ghost_or_artifact: '잔상이나 이미지 아티팩트가 보여요.',
  logo_changed: '로고나 그래픽이 원본과 다르게 보일 수 있어요.',
  color_changed: '상품 색상이 원본과 다르게 보일 수 있어요.',
  pattern_changed: '패턴이나 소재 결이 원본과 다르게 보일 수 있어요.',
  length_changed: '상품 기장이 원본과 다르게 보일 수 있어요.',
  fit_changed: '상품 핏이 의도와 다르게 보일 수 있어요.',
  body_changed: '마네킹 체형이나 자세가 어색할 수 있어요.',
  composition_changed: '컷 구성이 기존 버전과 달라졌어요.',
  composition: '마네킹 위치·크롭·여백을 확인해 주세요.',
  garment_structure: '카라·소매·단추·주머니 등 의류 구조를 확인해 주세요.',
  color_fidelity: '원본과 상품 색상이 일치하는지 확인해 주세요.',
  pattern_fidelity: '체크·스트라이프의 방향·간격·반복 주기를 확인해 주세요.',
  style_consistency: '마네킹·카메라·조명·배경의 시리즈 일관성을 확인해 주세요.',
  protected_detail: '로고·프린팅·자수 등 보호 디테일을 확인해 주세요.',
  advanced_structure: '복잡한 의류 구조는 자동 승인하지 않고 직접 확인해야 해요.',
  material: '레이스·시스루·스팽글 등 복잡 소재의 질감을 직접 확인해 주세요.',
  manual_review_required: '복잡 소재 또는 구조 상품이라 사용자 확인이 필요해요.',
  all_edits: '보정 결과가 품질 기준을 낮춰 원본 생성본으로 되돌렸어요.',
  bust_only: '가슴 볼륨 보정 결과만 되돌렸어요.',
  budget_exhausted: '검토 예산이 끝나 마지막 후보를 남겼어요.',
  loop_exhausted: '자동 재생성 한도에 도달해 마지막 후보를 남겼어요.',
  geometry_carrier_mismatch: '마네킹 구조 기준과 합성 결과가 맞지 않아 이 버전은 사용할 수 없어요.',
};

const dedupe = (items) => [...new Set(items.filter(Boolean).map(String))];

function reviewEnvelope(cut) {
  const nested = cut?.qcScores || cut?.qc || cut?.qualityControl || cut?.review || null;
  if (nested) return nested;
  if (!cut || typeof cut !== 'object') return null;
  const hasDirectQc = [
    'outcome',
    'applied',
    'componentsNeedingReview',
    'needsReview',
    'critical_errors',
    'failureReasons',
    'failureReason',
    'reasons',
    'hybridComposite',
  ].some((key) => Object.prototype.hasOwnProperty.call(cut, key));
  return hasDirectQc ? cut : null;
}

function hasReviewComponent(qc) {
  if (!qc || typeof qc !== 'object') return false;
  if (qc.needsReview === true) return true;
  if (Array.isArray(qc.componentsNeedingReview) && qc.componentsNeedingReview.length > 0) return true;
  if (Array.isArray(qc.needsReview) && qc.needsReview.length > 0) return true;
  return false;
}

function reasonKeys(qc) {
  if (!qc || typeof qc !== 'object') return [];
  const hybrid = qc.hybridComposite && typeof qc.hybridComposite === 'object'
    ? qc.hybridComposite
    : null;
  const structured = qc.structuredQC && typeof qc.structuredQC === 'object'
    ? qc.structuredQC
    : null;
  const structuredChecks = Array.isArray(structured?.checks) ? structured.checks : [];
  const structuredWarnings = Array.isArray(structured?.warnings) ? structured.warnings : [];
  const warnedUnavailableChecks = new Set(structuredWarnings
    .filter((warning) => String(warning).startsWith('qc_unavailable:'))
    .map((warning) => String(warning).slice('qc_unavailable:'.length)));
  return dedupe([
    ...(Array.isArray(qc.componentsNeedingReview) ? qc.componentsNeedingReview : []),
    ...(Array.isArray(qc.needsReview) ? qc.needsReview : []),
    ...(Array.isArray(qc.critical_errors) ? qc.critical_errors : []),
    ...(Array.isArray(qc.failureReasons) ? qc.failureReasons : []),
    ...(Array.isArray(qc.reasons) ? qc.reasons : []),
    ...(Array.isArray(qc.series_inconsistencies) ? ['series_consistency'] : []),
    ...(Array.isArray(hybrid?.componentsNeedingReview) ? hybrid.componentsNeedingReview : []),
    ...(Array.isArray(hybrid?.needsReview) ? hybrid.needsReview : []),
    ...(Array.isArray(structured?.criticalErrors) ? structured.criticalErrors : []),
    ...structuredWarnings,
    ...structuredChecks
      .filter((check) => ['fail', 'unavailable', 'error', 'timeout'].includes(check?.status))
      .filter((check) => !warnedUnavailableChecks.has(String(check?.check || '')))
      .map((check) => check?.check),
    ...(hybrid?.needsReview === true && !hybrid?.failureReason ? ['hybridComposite'] : []),
    hybrid?.failureReason,
    hybrid?.reason,
    qc.failureReason,
    qc.reason,
  ]);
}

export function reviewReasonCopy(reason) {
  const key = String(reason);
  if (key.startsWith('qc_unavailable:')) {
    const check = key.slice('qc_unavailable:'.length);
    return `${REASON_COPY[check] || '자동 품질 검사를 완료하지 못했어요.'} 자동 판정 대신 직접 확인해 주세요.`;
  }
  if (key.startsWith('specialized_qc_unavailable:')) {
    const check = key.slice('specialized_qc_unavailable:'.length);
    return `${REASON_COPY[check] || '전문 품질 검사 결과를 확인할 수 없어요.'} 자동 승인하지 않았어요.`;
  }
  return REASON_COPY[key] || '품질 검토가 필요한 항목이 있어요.';
}

export function mannequinReviewBlocksStoryboard(cut) {
  return classifyMannequinReview(cut).hardBlocked;
}

export function mannequinReviewAcknowledgedForCut(cut, acknowledgedCutId) {
  if (!cut?.id) return false;
  return String(cut.id) === String(acknowledgedCutId || '');
}

export function mannequinCanEnterStoryboard(cut, acknowledgedCutId) {
  const reviewState = classifyMannequinReview(cut);
  if (reviewState.hardBlocked) return false;
  if (reviewState.visibleReview) return mannequinReviewAcknowledgedForCut(cut, acknowledgedCutId);
  return true;
}

export function mannequinVersionAriaLabel(cut) {
  const reviewState = classifyMannequinReview(cut);
  const base = `버전 ${cut?.version} 선택`;
  if (reviewState.hardBlocked) return `${base}, 품질 차단`;
  if (reviewState.visibleReview) return `${base}, 품질 검토 필요`;
  if (reviewState.badge === '통과') return `${base}, 품질 통과`;
  return `${base}, 품질 판정 없음`;
}

export function isNonRetryableMannequinRegenerateError(error) {
  const status = Number(error?.status) || 0;
  const message = String(error?.message || '');
  return error?.code === 'hybrid_composite_failed_closed'
    || error?.details?.error === 'hybrid_composite_failed_closed'
    || status === 402
    || message.includes('크레딧')
    || (status >= 400 && status < 500);
}

export function mannequinRegenerateFailureNotice(error) {
  const details = error?.details && typeof error.details === 'object'
    ? error.details
    : null;
  const hybrid = details?.hybridComposite && typeof details.hybridComposite === 'object'
    ? details.hybridComposite
    : null;
  const reason = details?.failureReason || hybrid?.failureReason || null;
  const failedClosed = error?.code === 'hybrid_composite_failed_closed'
    || details?.error === 'hybrid_composite_failed_closed';

  if (failedClosed) {
    return {
      level: 'blocked',
      title: '합성 검증에 실패했어요.',
      description: reviewReasonCopy(reason),
      note: '새 버전은 저장하지 않았고, 크레딧도 차감되지 않았어요.',
      reason,
    };
  }

  return {
    level: 'blocked',
    title: '새 버전을 만들지 못했어요.',
    description: error?.message || '마네킹 재생성에 실패했어요. 다시 시도해 주세요.',
    note: '',
    reason,
  };
}

export function classifyMannequinReview(cut) {
  const qc = reviewEnvelope(cut);
  if (!qc) {
    return {
      level: 'normal',
      hardBlocked: false,
      visibleReview: false,
      badge: null,
      title: '',
      description: '',
      reasons: [],
    };
  }

  const outcome = String(qc.outcome || cut?.outcome || '').trim();
  const hybrid = qc.hybridComposite && typeof qc.hybridComposite === 'object'
    ? qc.hybridComposite
    : null;
  const applied = hybrid?.applied ?? qc.applied ?? cut?.applied;
  const hasNestedHybrid = hybrid != null;
  const hybridFailed = applied === false && (
    hasNestedHybrid
    || qc.mode === 'hybrid'
    || cut?.mode === 'hybrid'
    || qc.hybrid === true
    || cut?.hybrid === true
  );
  const hardBlocked = outcome === OUTCOME_REGENERATE || hybridFailed;
  const visibleReview = !hardBlocked && (
    outcome === OUTCOME_NEEDS_REVIEW
    || (applied === true && (hasReviewComponent(hybrid) || hasReviewComponent(qc)))
  );
  const reasons = reasonKeys(qc);

  if (hardBlocked) {
    return {
      level: 'blocked',
      hardBlocked: true,
      visibleReview: false,
      badge: '차단',
      title: '이 버전은 상세페이지에 사용할 수 없어요.',
      description: reasons.length
        ? reasons.map(reviewReasonCopy).join(' ')
        : '품질 기준을 통과하지 못해 다른 버전을 선택하거나 다시 생성해 주세요.',
      reasons,
    };
  }

  if (visibleReview) {
    return {
      level: 'review',
      hardBlocked: false,
      visibleReview: true,
      badge: '검토',
      title: '이 버전은 확인 후 사용해 주세요.',
      description: reasons.length
        ? reasons.map(reviewReasonCopy).join(' ')
        : '자동 검토에서 확인이 필요한 항목이 표시됐어요.',
      reasons,
    };
  }

  return {
    level: outcome === OUTCOME_AUTO_PASS ? 'passed' : 'normal',
    hardBlocked: false,
    visibleReview: false,
    badge: outcome === OUTCOME_AUTO_PASS ? '통과' : null,
    title: '',
    description: '',
    reasons,
  };
}
