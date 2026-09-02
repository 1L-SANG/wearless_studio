"""지원서 리뉴얼 머지 전 리뷰(2026-09-02)에서 확정된 critical·high 결함의 회귀 가드.

전부 소스 계약 검사다 — 실 DB·R2 없이 도는 층에서 "그 코드가 그 자리에 있는가"를 못 박는다.
각 테스트의 독스트링이 무엇이 깨졌었는지를 적는다. 지우지 마라: 되돌아가면 프로덕션에서
얼굴 사진이 영구 잔존하거나(파기 영수증이 거짓이 된다), 이벤트 루프가 멈춘다.
"""
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
APPLICATIONS = (APP / "facemarket_applications.py").read_text()
ENROLLMENT = (APP / "facemarket_enrollment.py").read_text()
PURGE = (APP / "services" / "biometric_purge.py").read_text()
ENV_LOADER = (Path(__file__).resolve().parents[1] / "scripts" / "_env.py").read_text()


def test_every_r2_call_in_applications_is_offloaded_to_a_thread():
    """r2 는 동기 boto3 다(r2.py §5). 감싸지 않으면 25MB 업로드가 uvicorn 루프를 통째로 막아
    같은 태스크의 /healthz 까지 끊긴다 — 2026-08-26 ECS 동결과 같은 조건."""
    bare = re.findall(r"(?<!to_thread\()\br2\.(put_bytes|copy|delete|get_bytes)\(", APPLICATIONS)
    wrapped = re.findall(r"to_thread\(\s*r2\.(put_bytes|copy|delete|get_bytes)\b", APPLICATIONS)
    assert not bare, f"to_thread 없이 부르는 R2 호출이 남아 있다: {bare}"
    assert len(wrapped) >= 5, "R2 호출이 to_thread 로 감싸져 있어야 한다"


def test_submit_deletes_the_staging_originals_after_commit():
    """r2.copy 는 copy_object 라 원본이 남는다. 제출은 스테이징 '행'을 지우므로 행 기반 sweep 은
    그 오브젝트에 영영 도달하지 못한다 — 커밋 후 원본을 직접 지워야 고아가 안 쌓인다."""
    # submit_application 한 함수만 잘라낸다 — 파일 끝까지 보면 다른 함수의 commit 을 집는다.
    body = APPLICATIONS.split("async def submit_application")[1].split("@router.")[0]
    # 커밋 뒤 꼬리에 스테이징 원본 삭제 루프가 있어야 한다.
    tail = body.split("await conn.commit()")[-1]
    assert "for staged_row in staged.values():" in tail, "커밋 후 스테이징 원본 삭제 루프가 없다"
    assert "to_thread(r2.delete, staged_row" in tail


def test_duplicate_submission_cleans_up_copied_photos():
    """동시 이중 제출(UniqueViolation)이면 행이 안 생긴다. 이미 복사한 사본을 그대로 두면
    DB 참조가 없는 얼굴 사진이 남아 어떤 파기 경로도 도달하지 못한다."""
    block = APPLICATIONS.split("except UniqueViolation:")[1].split("raise _err")[0]
    assert "photo_keys.values()" in block and "r2.delete" in block


def test_account_delete_purge_anonymizes_application_pii():
    """마이그레이션의 auth.users cascade 는 프로덕션에서 발화하지 않는다(이 레포는 auth.users
    행을 지우지 않는다). 파기가 직접 지우지 않으면 실명·생년월일이 무기한 남는다."""
    block = PURGE.split('if reason == "account_delete" and scope["user_id"] is not None:')[1]
    assert "update fm_model_applications" in block
    for column in ("contact_email", "applicant_name", "birthdate", "phone", "bio"):
        assert column in block, f"{column} 이 익명화되지 않는다"
    assert "delete from fm_model_application_photo_staging" in block


def test_withdrawal_purge_leaves_applications_alone():
    """생체정보 '철회'는 얼굴·신체 데이터를 지우는 것이지 심사 중인 지원서를 취소하는 게 아니다.
    철회로 지원서 사진을 지우면 관리자 화면에 빈 카드가 남아 승인 판단이 불가능해진다."""
    assert 'applications_in_scope = reason == "account_delete"' in PURGE
    for guard in (
        'applications_in_scope and scope["user_id"] and _has(schema, "fm_model_applications", "profile_image_r2_key")',
        'applications_in_scope and scope["user_id"] and _has(schema, "fm_model_applications", "photo_keys")',
    ):
        assert guard in PURGE, guard


def test_purge_prefix_sweep_covers_application_photo_paths():
    """DB 행 없이 남은 고아(제출 중 크래시·409 경합)는 key 수집으로 못 잡는다. 접두사 스윕이
    없으면 파기는 complete=True 로 끝나며 '완전 파기' 영수증이 거짓이 된다."""
    assert 'f"private/fm-application/staging/{user_id}/"' in PURGE
    assert 'f"private/fm-application/{application_id}/"' in PURGE


def test_identity_compare_is_gated_on_the_application_flag():
    """플래그를 false 로 되돌려도 application_id 가 박힌 in-flight enrollment 는 계속 대조되고,
    3회 누적이면 자동 거절 + 거절 메일까지 나갔다. '끄면 구 경로'가 되어야 한다."""
    assert 'if settings.fm_application_required and row["application_id"]:' in ENROLLMENT


def test_pending_models_are_exempt_from_the_application_gate():
    """fm_models 행은 신분증 인증 성공 시점에 pending 으로 먼저 생긴다. pending 을 면제하지 않으면
    사진 단계에서 이탈한 기존 사용자가 등록도 지원도 못 하는 막다른 골목에 갇힌다."""
    block = ENROLLMENT.split("legacy_exempt = model is not None and model[\"status\"] in (")[1].split(")")[0]
    assert '"pending"' in block and '"verified"' in block and '"reverification_required"' in block
    assert '"suspended"' not in block, "정지된 모델까지 면제하면 심사 우회로가 된다"


def test_admin_list_treats_stale_pending_email_as_unsent():
    """원장은 pending 으로 넣고 발송 뒤 sent/failed 로 바꾼다. 그 사이에 죽으면 pending 으로 굳는데,
    그러면 '미발송' 뱃지도 재발송 버튼도 안 떠서 메일 미발송 자체가 보이지 않는다."""
    assert "status = 'pending' and created_at < now() - interval '2 minutes'" in APPLICATIONS


def test_resend_preserves_the_original_email_type():
    """status 로만 종류를 고르면 신분증 3회 불일치 자동거절(auto_rejected)의 재발송이 관리자
    거절 템플릿으로 바뀌어, 사유가 없는데 사유 자리가 빈 메일이 나간다."""
    assert 'email_type = row.get("last_email_type") or row["status"]' in APPLICATIONS
    assert '"auto_rejected"' in APPLICATIONS


def test_env_loader_is_inert_under_pytest():
    """scripts/_env.load_env() 가 import 시점에 server/.env 를 os.environ 에 심으면, 로컬
    Postgres 를 쓰라고 쓰여 있는 테스트가 원격 운영 계열 DB 를 향한다(2026-09-02 실측:
    전체 스위트 50건 실패, 더 나쁜 경우 원격 DB 에 테스트 데이터가 쓰인다)."""
    assert "_running_under_pytest" in ENV_LOADER
    assert "PYTEST_CURRENT_TEST" in ENV_LOADER


NOTIFY = (APP / "facemarket_notify.py").read_text()
DISPATCHER = (APP / "workers" / "dispatcher.py").read_text()


def test_slack_message_escapes_user_supplied_text():
    """region 은 지원자 자유입력이다. 이스케이프하지 않으면 알림에 `<https://evil/|검토 콘솔>`
    같은 가짜 mrkdwn 링크를 심을 수 있고, 바로 아래 진짜 콘솔 링크와 구분되지 않는다."""
    assert "_slack_escape" in NOTIFY
    assert "_slack_escape(region or '-')" in NOTIFY


def test_slack_delivery_failure_is_observable():
    """이 알림은 원장이 없어 로그가 유일한 관측점이다. 상태를 안 보면 웹훅 폐기(404/410)나
    레이트리밋(429)이 성공과 구분되지 않고 지원서 알림이 조용히 끊긴다."""
    assert "res.status_code >= 400" in NOTIFY


def test_terminal_application_pii_sweep_exists_and_is_scheduled():
    """스펙 11/3A: 거절·취소 지원서는 30일 뒤 익명화한다. sweep 이 없던 동안 실명·생년월일이
    무기한 남았다(승인 건은 운영 데이터로 유지 — 지우면 안 된다)."""
    assert "async def sweep_terminal_application_pii" in APPLICATIONS
    assert "status in ('rejected', 'cancelled')" in APPLICATIONS
    assert "sweep_terminal_application_pii" in DISPATCHER


def test_pii_sweep_never_touches_approved_applications():
    """승인 지원서는 운영 데이터다(스펙 11). 조건에 approved 가 섞이면 검토 이력이 사라진다."""
    body = APPLICATIONS.split("async def sweep_terminal_application_pii")[1].split("@router.")[0]
    assert "'approved'" not in body


def test_r2_keys_are_not_written_to_logs():
    """스테이징 키는 private/fm-application/staging/{user_id}/... 라 user_id 를 담는다.
    레포 원칙은 '키는 삭제 대상으로만 쓰고 로그엔 카운트만'이다."""
    for line in APPLICATIONS.splitlines():
        if "logger." in line and "r2_key" in line:
            raise AssertionError(f"R2 키를 로그에 남긴다: {line.strip()}")
