"""관리자 판정과 감사 기록이 실제로 배선됐는지 — 소스 계약 검사.

되돌아가면: 새 라우트가 가드를 빼먹어도 테스트가 안 잡고, 관리자가 무엇을 했는지 원장에
남지 않는다. 두 사고 모두 조용해서 배포 뒤에는 발견되지 않는다.
"""
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
APPLICATIONS = (APP / "facemarket_applications.py").read_text()
ROUTES = (APP / "routes.py").read_text()
FACEMARKET = (APP / "facemarket.py").read_text()
CUTOVER = (APP / "facemarket_cutover.py").read_text()
ADMIN = (APP / "facemarket_admin.py").read_text()


def test_no_module_calls_repo_is_admin_directly():
    """admin_guard 하나만 부른다 — 문구·상태코드가 갈라지지 않게."""
    for name, source in (
        ("facemarket_applications.py", APPLICATIONS),
        ("routes.py", ROUTES),
        ("facemarket.py", FACEMARKET),
        ("facemarket_cutover.py", CUTOVER),
    ):
        assert "repo.is_admin" not in source, f"{name} 가 아직 repo.is_admin 을 직접 부른다"


def test_refund_routes_are_gated_and_audited():
    for route in ("approve_refund", "reject_refund"):
        body = ROUTES.split(f"async def {route}(")[1].split("@router.")[0]
        assert "await admin_guard.require_admin(conn, user_id)" in body, route
        assert "admin_guard.write_audit(" in body, route


def test_refund_audit_actions_are_named():
    assert '"refund.approve"' in ROUTES
    assert '"refund.reject"' in ROUTES


def test_application_decisions_are_audited():
    for route, action in (
        ("admin_approve_application", "application.approve"),
        ("admin_reject_application", "application.reject"),
        ("admin_resend_email", "application.resend_email"),
    ):
        body = APPLICATIONS.split(f"async def {route}(")[1].split("@router.")[0]
        assert "admin_guard.write_audit(" in body, route
        assert f'"{action}"' in body, action


def test_reject_audit_carries_the_reason_as_note():
    body = APPLICATIONS.split("async def admin_reject_application(")[1].split("@router.")[0]
    audit = body.split("write_audit(")[1]
    assert "note=" in audit


def test_audit_write_happens_before_commit():
    """조치와 같은 트랜잭션이어야 한다. 커밋 뒤에 쓰면 원장만 따로 커밋되거나 유실된다.

    명시적 conn.commit() 이 있는 라우트만 이 패턴으로 검사한다. admin_resend_email 은
    명시적 commit 이 없고 get_conn 스코프가 정상 종료 시 커밋한다 — 이 테스트로 잡을 수
    없다(그 사실은 호출부 주석으로 남긴다).
    """
    # 지원서·환불 라우트는 write_audit 을 라우트 본문 안에서 직접 부른다 — 텍스트 위치를
    # 그대로 비교할 수 있다.
    inline_cases = (
        (APPLICATIONS, "admin_approve_application"),
        (APPLICATIONS, "admin_reject_application"),
        (ROUTES, "approve_refund"),
        (ROUTES, "reject_refund"),
    )
    for source, route in inline_cases:
        body = source.split(f"async def {route}(")[1].split("@router.")[0]
        audit_at = body.index("write_audit(")
        commit_at = body.index("await conn.commit()")
        assert audit_at < commit_at, f"{route}: 감사 기록이 commit 뒤에 있다"

    # 관리자 콘솔의 승격/정지/해제 라우트는 write_audit 을 라우트 안이 아니라 위임한 순수
    # 함수(set_role·suspend_model·unsuspend_model) 안에서 부른다 — 그 함수들이 실제로
    # write_audit 을 쓰는지는 별도 동작 테스트(test_admin_staff.py·test_admin_model_suspend.py)가
    # FakeConn 으로 이미 확인한다. 여기서는 라우트가 그 함수 호출을 commit 보다 먼저 await
    # 하는지만 본다 — 파이썬은 await 를 순서대로 실행하므로, 함수 안에서 write_audit 이
    # 어디 있든 그 호출이 끝난 뒤에야 다음 줄인 commit 으로 넘어간다.
    delegated_cases = (
        ("admin_set_role", "set_role"),
        ("admin_suspend_model", "suspend_model"),
        ("admin_unsuspend_model", "unsuspend_model"),
    )
    for route, helper in delegated_cases:
        body = ADMIN.split(f"async def {route}(")[1].split("@router.")[0]
        call_at = body.index(f"await {helper}(")
        commit_at = body.index("await conn.commit()")
        assert call_at < commit_at, f"{route}: {helper} 호출이 commit 뒤에 있다"
