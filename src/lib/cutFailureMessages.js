/* ADR-0017, 실패 이유별 셀러 안내를 빈 칸과 AI 탭에서 함께 쓴다. */
const SELLER_GUIDANCE = Object.freeze({
  no_garment_photo: '상품 사진이 더 필요해요',
  matching_asset_unavailable: '매칭 의류 사진을 다시 선택해 주세요',
  no_angle_photo: '이 각도의 모델 사진이 더 필요해요',
  identity_scope_mismatch: '이 컷에 맞는 모델이나 예시를 선택해 주세요',
  real_model_studio_only: '이 컷에 맞는 모델을 선택해 주세요',
  pose_direction_incompatible: '이 방향에 맞는 예시를 선택해 주세요',
  invalid_cut_spec: '콘티의 컷 설정을 확인해 주세요',
  detail_target_unavailable: '디테일 촬영 대상을 다시 선택해 주세요',
  invalid_detail_region: '디테일 촬영 영역을 다시 선택해 주세요',
  detail_reference_required: '디테일 상품 사진이 더 필요해요',
  detail_color_reference_required: '이 색상의 상품 사진이 더 필요해요',
  detail_source_unavailable: '상품 사진을 다시 올려 주세요',
  detail_target_color_changed: '디테일 촬영 색상을 다시 선택해 주세요',
  detail_target_source_changed: '디테일 촬영 대상을 다시 선택해 주세요',
  similar_cut_source_failed: '원본 컷을 먼저 만들어 주세요',
  duplicate_cut_source_failed: '원본 컷을 먼저 만들어 주세요',
});

export function failedCutPresentation(element = {}) {
  const reason = element.genFailReason;
  const retryable = element.genFailRetryable ?? !Object.hasOwn(SELLER_GUIDANCE, reason);
  return {
    retryable,
    guidance: SELLER_GUIDANCE[reason] || (retryable
      ? '눌러서 사진을 넣거나 AI 탭에서 다시 만들 수 있어요'
      : '상품 사진과 콘티 설정을 확인해 주세요'),
  };
}
