"""ADR-0017 2026-10-11 개정의 두 갈래 분류. 이벤트와 저장 빈 칸은 같은 정보를 쓴다."""

CUT_FAILURE_REASONS = {
    **dict.fromkeys((
        "temporary_error", "provider_error", "face_pass_unavailable", "scene_qc_unavailable",
        "scene_qc_failed", "example_asset_unavailable", "confirmed_gpt_rejected",
        "confirmed_gpt_requires_virtual_model", "confirmed_gpt_forbids_model_substitution",
        "confirmed_gpt_requires_base_color", "confirmed_gpt_direction_sheets_unavailable",
        "confirmed_gpt_packet_invalid", "detail_qc_failed", "detail_qc_unknown",
        "detail_quality_unconfirmed", "detail_reference_unavailable", "detail_attempt_unavailable",
        "detail_checkpoint_unavailable", "detail_generation_unavailable", "detail_job_lease_lost",
        "attempt_budget_exhausted", "detail_attempt_state_changed", "detail_judgment_already_claimed",
        "detail_judgment_state_changed", "detail_judgment_retry_unavailable",
        "detail_judgment_retry_state_changed",
    ), "retryable"),
    **dict.fromkeys((
        "no_garment_photo", "matching_asset_unavailable", "no_angle_photo",
        "identity_scope_mismatch", "real_model_studio_only", "pose_direction_incompatible",
        "invalid_cut_spec", "detail_target_unavailable", "invalid_detail_region",
        "detail_reference_required", "detail_color_reference_required", "detail_source_unavailable",
        "detail_target_color_changed", "detail_target_source_changed",
    ), "retryable"),
    "similar_cut_source_failed": "dependent",
    "duplicate_cut_source_failed": "dependent",
}


def failure_info(reason: str) -> dict:
    if reason not in CUT_FAILURE_REASONS:
        reason = "confirmed_gpt_rejected" if str(reason).startswith("confirmed_gpt_") else "provider_error"
    return {"reason": reason, "retryable": CUT_FAILURE_REASONS[reason] == "retryable"}
