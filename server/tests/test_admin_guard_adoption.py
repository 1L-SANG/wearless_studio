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
    """조치와 같은 트랜잭션이어야 한다. 커밋 뒤에 쓰면 원장만 따로 커밋되거나 유실된다."""
    body = APPLICATIONS.split("async def admin_approve_application(")[1].split("@router.")[0]
    audit_at = body.index("write_audit(")
    commit_at = body.index("await conn.commit()")
    assert audit_at < commit_at, "감사 기록이 commit 뒤에 있다"
