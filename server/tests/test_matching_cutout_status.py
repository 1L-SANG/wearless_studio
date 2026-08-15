from app.services.matching_cutout import cutout_status_for


def test_ready_when_asset_is_cutout_derived():
    assert cutout_status_for(is_custom=True, image_meta={"type": "matchingCutout"},
                             has_active_job=False) == "ready"


def test_processing_when_job_active_and_not_yet_swapped():
    assert cutout_status_for(is_custom=True, image_meta={}, has_active_job=True) == "processing"


def test_none_for_seed_items():
    assert cutout_status_for(is_custom=False, image_meta={}, has_active_job=False) is None


def test_failed_when_no_job_and_not_cutout():
    # 잡이 끝났는데(active 아님) 여전히 원본이면 실패로 본다
    assert cutout_status_for(is_custom=True, image_meta={}, has_active_job=False) == "failed"
