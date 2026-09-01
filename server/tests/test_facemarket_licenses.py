"""얼굴 라이선스 라우트 테스트 (생성 멀티파트 · 목록 · 얼굴 게이트).

DB·R2를 페이크로 대체해 순수 로직만 검증:
  · 얼굴 바이트는 비공개 R2에만 저장, 응답에 face_image_key/원본 바이트 미노출
  · face_image_uri = 게이트 URL(공개 R2 URL 아님), digest = 'sha256-...'
  · 소유 스코프(다른 사용자 접근 404) · revoked/expired 접근 차단(404)
  · verified 모델 선행 필수(없으면 400)
"""

import asyncio
import contextlib
import copy
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from psycopg._queries import PostgresQuery
from psycopg.adapt import Transformer

from app import facemarket, holder_client
from app import facemarket_enrollment
from types import SimpleNamespace

from app.facemarket import LicenseCard, _cover_serving_url, _license_card


def _req_with_r2(r2):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(r2_face=r2)))


def test_cover_serving_url_transforms_private_key_to_signed_url():
    class _R2:
        def public_url(self, key):
            return f"https://signed.example/{key}?sig=x"
    req = _req_with_r2(_R2())
    # raw private 키 → presigned 서빙 URL(브라우저 <img src> 로드 가능)
    assert _cover_serving_url(req, "private/fm-profile/abc.png") == \
        "https://signed.example/private/fm-profile/abc.png?sig=x"
    # 키 없으면 None
    assert _cover_serving_url(req, None) is None
    assert _cover_serving_url(req, "") is None
    # r2_face 미설정 → None(그레이스풀, 크래시 아님)
    assert _cover_serving_url(_req_with_r2(None), "private/fm-profile/abc.png") is None
from app.main import create_app
from conftest import make_settings

NOW = datetime(2026, 7, 9, 12, 0, 0, tzinfo=timezone.utc)
ENROLLMENT_ID = "22222222-2222-2222-2222-222222222222"
OTHER_ENROLLMENT_ID = "33333333-3333-3333-3333-333333333333"
MODEL_ID = "11111111-1111-1111-1111-111111111111"
FRONT_ASSET_KEY = f"facemarket/models/{MODEL_ID}/enrollments/{ENROLLMENT_ID}/assets/face_front.png"
GRID_ASSET_KEY = f"facemarket/models/{MODEL_ID}/enrollments/{ENROLLMENT_ID}/assets/grid_sedcard.png"
APPROVED_FRONT_KEY = f"facemarket/models/{MODEL_ID}/enrollments/{ENROLLMENT_ID}/approved/front.png"
APPROVED_FRONT_DIGEST = "sha256-approved-front"
APPROVED_FRONT_BYTES = b"RIFF-current-approved-front-WEBP"
EVIDENCE_VERSION = "dev-gold-v1"
EXPECTED_PLACEHOLDER_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400" '
    b'viewBox="0 0 400 400"><rect width="400" height="400" fill="#efeef0"/>'
    b'<circle cx="200" cy="142" r="58" fill="#aaa8b2"/>'
    b'<path d="M92 352c12-82 55-124 108-124s96 42 108 124" '
    b'fill="#aaa8b2"/></svg>'
)
_LICENSE_KEYS = (
    "id", "model_id", "face_image_uri", "face_image_digest", "allowed_use",
    "forbidden_use", "unit_price", "license_valid_until", "status", "vc_id", "created_at",
)


def test_license_card_allows_missing_face_digest_during_reverification_cutover():
    card = LicenseCard.model_validate(
        {
            "id": "license-1",
            "model_id": MODEL_ID,
            "face_image_uri": "/v1/facemarket/licenses/license-1/face",
            "face_image_digest": None,
            "allowed_use": [],
            "forbidden_use": [],
            "unit_price": 5000,
            "license_valid_until": NOW,
            "status": "reverification_required",
            "created_at": NOW,
        }
    )

    assert card.face_image_digest is None


def test_license_card_helper_tolerates_missing_cover_and_passes_it_through():
    # RETURNING(단일 fm_licenses) row 엔 cover_image_url 이 없다 — KeyError 없이 None 이어야 한다.
    base = {
        "id": "license-1", "model_id": MODEL_ID,
        "face_image_uri": "/v1/facemarket/licenses/license-1/face",
        "face_image_digest": "sha256-x", "allowed_use": [], "forbidden_use": [],
        "unit_price": 5000, "license_valid_until": NOW, "status": "active",
        "vc_id": None, "created_at": NOW,
    }
    assert "cover_image_url" not in base
    assert _license_card(base)["cover_image_url"] is None
    # 목록(_L) row 는 m.cover_image_url 을 실어 온다 — 그대로 통과.
    with_cover = {**base, "cover_image_url": "private/fm-profile/abc.png"}
    assert _license_card(with_cover)["cover_image_url"] == "private/fm-profile/abc.png"


def _compile_psycopg_query(sql, params):
    query = PostgresQuery(Transformer())
    query.convert(sql, params)
    return query.query.decode()


class FakeR2Face:
    """app.state.r2_face 대역 — 바이트를 dict에 보관(put/get/delete)."""

    def __init__(self):
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.gets: list[str] = []

    def put_bytes(self, key, data, mime, cache=None):
        self.objects[key] = (data, mime)

    def get_bytes(self, key):
        self.gets.append(key)
        if key not in self.objects:
            raise KeyError(key)
        return self.objects[key][0]

    def delete(self, key):
        self.objects.pop(key, None)

    def public_url(self, key):
        # 실제 r2_face 는 public_base=None → presigned GET URL. 테스트는 결정적 대역.
        return f"https://signed.example/{key}?sig=test"


def _current_card(store, *, model_id=None, license_id=None, user_id=None, consent_version=None):
    license_row = (
        next((row for row in store["licenses"] if row["id"] == license_id), None)
        if license_id is not None
        else None
    )
    if license_id is not None and license_row is None:
        return None
    model = next(
        (
            row
            for row in store["models"]
            if (model_id is None or row["id"] == model_id)
            and (license_row is None or row["id"] == license_row["model_id"])
            and (user_id is None or row["user_id"] == user_id)
        ),
        None,
    )
    if model is None:
        return None
    enrollment = next(
        (
            row
            for row in store["enrollments"]
            if row["id"] == model.get("current_enrollment_id")
            and row["model_id"] == model["id"]
        ),
        None,
    )
    if enrollment is None:
        return None
    if license_row is None:
        license_row = next(
            (
                row
                for row in store["licenses"]
                if row["model_id"] == model["id"]
                and row.get("enrollment_id") == enrollment["id"]
            ),
            None,
        )
    front = next(
        (
            row
            for row in store["enrollment_photos"]
            if row["enrollment_id"] == enrollment["id"] and row["angle"] == "front"
        ),
        None,
    )
    assets = {
        row["view"]: row
        for row in store["assets"]
        if row["model_id"] == model["id"]
    }
    valid_until = (license_row or {}).get("license_valid_until")
    eligible = bool(
        license_row
        and front
        and model["status"] == "verified"
        and model.get("assets_status") == "ready"
        and license_row.get("enrollment_id") == enrollment["id"]
        and enrollment["status"] == "passed"
        and enrollment["decision"] == "passed"
        and enrollment["consent_version"] == consent_version
        and str(enrollment.get("match_policy_version") or "").strip()
        and license_row["status"] == "active"
        and (valid_until is None or valid_until > datetime.now(timezone.utc))
        and str(license_row.get("vc_id") or "").strip()
        and license_row["vc_id"] == enrollment["vc_id"]
        and front["storage_state"] == "approved"
        and front["mime_type"].startswith("image/")
        and str(front.get("r2_key") or "").strip()
        and str(license_row.get("face_image_key") or "").strip()
        and license_row["face_image_key"] == front["r2_key"]
        and all(
            str(assets.get(view, {}).get("r2_key") or "").strip()
            and assets[view].get("bucket") == "face"
            and assets[view].get("mime", "").startswith("image/")
            and assets[view].get("source_enrollment_id") == enrollment["id"]
            and assets[view].get("evidence_version") == enrollment["match_policy_version"]
            for view in ("face_front", "grid_sedcard")
        )
    )
    return (model, enrollment, license_row, front) if eligible else None


def _runtime_license_row(store, model_id, license_id=None):
    """resolve_model_license 의 left-join SQL 을 흉내낸다: 모델 존재만 있으면 row 는
    항상 나오고(license/enrollment 는 null 허용), license_id 가 지정되면 그 라이선스와
    정확히 매칭될 때만 row 가 나온다(WHERE l.id = %s 가 미스면 통째로 미스)."""
    model = next((m for m in store["models"] if m["id"] == model_id), None)
    if model is None:
        return None
    enrollment = next(
        (
            e for e in store["enrollments"]
            if e["id"] == model.get("current_enrollment_id") and e["model_id"] == model["id"]
        ),
        None,
    )
    if license_id is not None:
        license_row = next(
            (
                r for r in store["licenses"]
                if r["model_id"] == model["id"] and r["id"] == license_id
            ),
            None,
        )
        if license_row is None:
            return None
    else:
        license_row = next(
            (
                r for r in store["licenses"]
                if r["model_id"] == model["id"]
                and r.get("enrollment_id") == model.get("current_enrollment_id")
            ),
            None,
        )

    def _asset(view):
        return next(
            (
                a for a in store["assets"]
                if a["model_id"] == model["id"] and a["view"] == view
            ),
            None,
        )

    def _asset_ok(asset):
        return bool(
            asset
            and str(asset.get("r2_key") or "").strip()
            and asset.get("bucket") == "face"
            and str(asset.get("mime") or "").startswith("image/")
        )

    face_asset = _asset("face_front")
    grid_asset = _asset("grid_sedcard")
    has_face_front = _asset_ok(face_asset)
    has_grid_sedcard = _asset_ok(grid_asset)
    match_policy_version = (enrollment or {}).get("match_policy_version")
    assets_current_evidence = bool(
        str(match_policy_version or "").strip()
        and has_face_front
        and face_asset.get("source_enrollment_id") == model.get("current_enrollment_id")
        and face_asset.get("evidence_version") == match_policy_version
        and has_grid_sedcard
        and grid_asset.get("source_enrollment_id") == model.get("current_enrollment_id")
        and grid_asset.get("evidence_version") == match_policy_version
    )
    return {
        "id": (license_row or {}).get("id"),
        "model_id": model["id"],
        "_model_name_raw": model.get("display_name", ""),
        "status": (license_row or {}).get("status"),
        "license_valid_until": (license_row or {}).get("license_valid_until"),
        "unit_price": (license_row or {}).get("unit_price"),
        "vc_id": (license_row or {}).get("vc_id"),
        "allowed_use": (license_row or {}).get("allowed_use"),
        "forbidden_use": (license_row or {}).get("forbidden_use"),
        "model_status": model.get("status"),
        "assets_status": model.get("assets_status"),
        "current_enrollment_id": model.get("current_enrollment_id"),
        "license_enrollment_id": (license_row or {}).get("enrollment_id"),
        "enrollment_status": (enrollment or {}).get("status"),
        "match_policy_version": match_policy_version,
        "has_face_front": has_face_front,
        "has_grid_sedcard": has_grid_sedcard,
        "assets_current_evidence": assets_current_evidence,
        "gender": model.get("gender"),
        "height_bucket": model.get("height_bucket"),
        "body_type": model.get("body_type"),
    }


def _assert_current_card_sql(sql):
    for required in (
        "e.id = m.current_enrollment_id",
        "l.enrollment_id = e.id",
        "p.enrollment_id = e.id and p.angle = 'front'",
        "m.status = 'verified'",
        "m.assets_status = 'ready'",
        "e.status = 'passed'",
        "e.decision = 'passed'",
        "e.consent_version = %s",
        "nullif(btrim(e.match_policy_version), '') is not null",
        "l.status = 'active'",
        "l.license_valid_until > now()",
        "nullif(btrim(l.vc_id), '') is not null",
        "l.vc_id = e.vc_id",
        "p.storage_state = 'approved'",
        "p.mime_type like 'image/%%'",
        "nullif(btrim(p.r2_key), '') is not null",
        "nullif(btrim(l.face_image_key), '') is not null",
        "p.r2_key = l.face_image_key",
        "a.view = 'face_front'",
        "a.view = 'grid_sedcard'",
        "nullif(btrim(a.r2_key), '') is not null",
        "a.bucket = 'face'",
        "a.mime like 'image/%%'",
        "a.source_enrollment_id = e.id",
        "a.evidence_version = e.match_policy_version",
    ):
        assert required in sql


class FakeCursor:
    def __init__(self, store):
        self.store = store
        self._result = None
        self._many = None
        self.rowcount = -1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        s = " ".join(sql.split()).lower()
        params = params or ()
        models = self.store["models"]
        licenses = self.store["licenses"]
        self._result = None
        self._many = None
        self.rowcount = -1
        self.store.setdefault("sql", []).append(s)

        if s.startswith("select pg_advisory_xact_lock"):
            self._result = {"?column?": None}
        elif (
            "kind = 'personalization_purge'" in s
            and "status in ('pending', 'running')" in s
            and "ready_for_identity_delete" in s
        ):
            self._result = {"closed": self.store.get("account_closed", False)}
        elif "from fm_cutover_batches" in s and "status = any" in s:
            self._result = {"closed": False}
        elif s.startswith("select l.id::text as id, m.id::text as model_id"):
            self.store["runtime_license_sql"] = sql
            self.store["runtime_license_params"] = params
            model_id = params[0]
            license_id = params[1] if len(params) > 1 else None
            self._result = _runtime_license_row(self.store, model_id, license_id)
        elif s.startswith("select l.face_image_key, p.mime_type from fm_models m"):
            consent_version, license_id, user_id = params
            current = _current_card(
                self.store,
                license_id=license_id,
                user_id=user_id,
                consent_version=consent_version,
            )
            self._result = (
                {
                    "face_image_key": current[2]["face_image_key"],
                    "mime_type": current[3]["mime_type"],
                }
                if current
                else None
            )
        elif s.startswith("select 1 as eligible from fm_models m"):
            consent_version, model_id = params
            self.store["thumbnail_sql"] = s
            self.store["thumbnail_params"] = params
            self._result = (
                {"eligible": 1}
                if _current_card(
                    self.store,
                    model_id=model_id,
                    consent_version=consent_version,
                )
                else None
            )
        elif s.startswith("select a.r2_key, a.mime from fm_model_assets a"):
            model_id = params[0]
            model = next(
                (m for m in models if m["id"] == model_id and m["status"] == "verified"),
                None,
            )
            asset = next(
                (
                    a
                    for a in self.store["assets"]
                    if model and a["model_id"] == model_id and a["view"] == "face_front"
                ),
                None,
            )
            self._result = (
                {"r2_key": asset.get("r2_key"), "mime": asset.get("mime")}
                if asset
                else None
            )
        elif s.startswith("select m.id::text as id") and "from fm_models m" in s:
            self.store["catalog_sql"] = s
            rows = []
            for model in models:
                if params:
                    current = _current_card(
                        self.store,
                        model_id=model["id"],
                        consent_version=params[0],
                    )
                    if not current:
                        continue
                    license_row = current[2]
                else:
                    if model["status"] != "verified":
                        continue
                    license_row = next(
                        (
                            row
                            for row in licenses
                            if row["model_id"] == model["id"] and row["status"] == "active"
                        ),
                        {},
                    )
                rows.append(
                    {
                        "id": model["id"],
                        "display_name": model["display_name"],
                        "status": model["status"],
                        "cover_image_url": None,
                        "created_at": model.get("created_at", NOW),
                        "license_id": license_row.get("id"),
                        "unit_price": license_row.get("unit_price"),
                        "vc_id": license_row.get("vc_id"),
                        "has_active_license": bool(license_row),
                        "assets_ready": model.get("assets_status") == "ready",
                        "face_thumb_uri": (
                            f"/v1/facemarket/models/{model['id']}/thumbnail"
                            if model.get("assets_status") == "ready"
                            else None
                        ),
                    }
                )
            self._many = rows
        elif s.startswith("select e.id::text as enrollment_id"):
            lic = None
            if len(params) == 3:
                license_id, enrollment_id, user_id = params[:3]
                owned = {m["id"] for m in models if m["user_id"] == user_id}
                lic = next(
                    (r for r in licenses
                     if r["id"] == license_id
                     and r.get("enrollment_id") == enrollment_id
                     and r["model_id"] in owned),
                    None,
                )
                if lic is None:
                    self._result = None
                    return
            else:
                enrollment_id, user_id = params[:2]
            e = next(
                (r for r in self.store["enrollments"]
                 if r["id"] == enrollment_id and r["user_id"] == user_id),
                None,
            )
            if e is None:
                self._result = None
                return
            m = next((r for r in models if r["id"] == e["model_id"] and r["user_id"] == user_id), None)
            front_photo = next(
                (p for p in self.store["enrollment_photos"]
                 if p["enrollment_id"] == e["id"] and p["angle"] == "front"),
                None,
            )
            assets = {
                a["view"]: a for a in self.store["assets"]
                if a["model_id"] == e["model_id"] and a["view"] in {"face_front", "grid_sedcard"}
            }
            face = assets.get("face_front") or {}
            grid = assets.get("grid_sedcard") or {}
            self._result = {
                "enrollment_id": e["id"],
                "enrollment_status": e["status"],
                "model_id": (m or {}).get("id"),
                "model_status": (m or {}).get("status"),
                "model_did": (m or {}).get("did"),
                "assets_status": (m or {}).get("assets_status"),
                "current_enrollment_id": (m or {}).get("current_enrollment_id"),
                "model_reverification_batch_id": (m or {}).get("reverification_batch_id"),
                "batch_status": (m or {}).get("batch_status"),
                "batch_completed_at": (m or {}).get("batch_completed_at"),
                "enrollment_created_at": e.get("created_at", NOW),
                "license_created_at": (lic or {}).get("created_at", NOW),
                "license_reverification_batch_id": (lic or {}).get("reverification_batch_id"),
                "front_key": (front_photo or {}).get("r2_key"),
                "front_digest": (front_photo or {}).get("image_digest"),
                "front_storage_state": (front_photo or {}).get("storage_state"),
                "match_policy_version": e.get("match_policy_version"),
                "face_asset_key": face.get("r2_key"),
                "face_asset_source_enrollment_id": face.get("source_enrollment_id"),
                "face_asset_evidence_version": face.get("evidence_version"),
                "grid_asset_key": grid.get("r2_key"),
                "grid_asset_source_enrollment_id": grid.get("source_enrollment_id"),
                "grid_asset_evidence_version": grid.get("evidence_version"),
            }
        elif s.startswith("select id from fm_models where user_id"):
            # verified 모델 조회
            m = next(
                (r for r in models if r["user_id"] == params[0] and r["status"] == "verified"),
                None,
            )
            self._result = {"id": m["id"]} if m else None
        elif (
            s.startswith("select l.id::text as id")
            and "from fm_licenses l" in s
            and "where l.id = %s" in s
        ):
            license_id, user_id = params[:2]
            owned = {m["id"] for m in models if m["user_id"] == user_id}
            row = next((r for r in licenses if r["id"] == license_id and r["model_id"] in owned), None)
            if row:
                self._result = {k: row[k] for k in _LICENSE_KEYS}
                self._result["enrollment_id"] = row.get("enrollment_id")
                self._result["face_image_key"] = row.get("face_image_key")
            else:
                self._result = None
        elif s.startswith("select") and "from fm_licenses l" in s and "l.enrollment_id" in s:
            enrollment_id, user_id = params[:2]
            if self.store.pop("hide_existing_license_once", False):
                self._result = None
                return
            owned = {m["id"] for m in models if m["user_id"] == user_id}
            row = next(
                (r for r in licenses if r.get("enrollment_id") == enrollment_id and r["model_id"] in owned),
                None,
            )
            self._result = {k: row[k] for k in _LICENSE_KEYS} if row else None
        elif s.startswith("insert into fm_licenses") and "enrollment_id" in s:
            (lid, model_id, enrollment_id, gate_uri, key, digest,
             allowed, forbidden, unit_price, valid_until) = params
            row = next((r for r in licenses if r.get("enrollment_id") == enrollment_id), None)
            if row is not None:
                self._result = None
                self.rowcount = 0
                return
            row = {
                "id": lid, "model_id": model_id, "enrollment_id": enrollment_id,
                "face_image_uri": gate_uri, "face_image_key": key, "face_image_digest": digest,
                "allowed_use": list(allowed), "forbidden_use": list(forbidden),
                "unit_price": unit_price, "license_valid_until": valid_until,
                "status": "pending", "vc_id": None, "created_at": NOW,
                "profile_id": None,
            }
            licenses.append(row)
            self._result = {k: row[k] for k in _LICENSE_KEYS}
            self.rowcount = 1
        elif s.startswith("insert into fm_licenses"):
            (lid, model_id, gate_uri, key, digest,
             allowed, forbidden, unit_price, valid_until, profile_id) = params
            row = {
                "id": lid, "model_id": model_id, "face_image_uri": gate_uri,
                "face_image_key": key, "face_image_digest": digest,
                "allowed_use": list(allowed), "forbidden_use": list(forbidden),
                "unit_price": unit_price, "license_valid_until": valid_until,
                "status": "active", "vc_id": None, "created_at": NOW,
                "profile_id": profile_id,  # 개인화 프로필 참조(레거시 face 경로는 None)
            }
            licenses.append(row)
            self._result = {k: row[k] for k in _LICENSE_KEYS}
            self.rowcount = 1
        elif s.startswith("update fm_biometric_enrollments set status = 'vc_pending'"):
            enrollment_id = params[0]
            e = next((r for r in self.store["enrollments"] if r["id"] == enrollment_id), None)
            if e and e["status"] in {"license_pending", "vc_pending"}:
                e["status"] = "vc_pending"
                self._result = {"id": e["id"]}
                self.rowcount = 1
            else:
                self._result = None
                self.rowcount = 0
        elif s.startswith("update fm_licenses set status = 'active'"):
            vc_id, license_id = params[:2]
            if self.store.get("final_license_update_misses"):
                self._result = None
                self.rowcount = 0
                return
            row = next((r for r in licenses if r["id"] == license_id and r["status"] == "pending"), None)
            if row:
                row["status"] = "active"
                row["vc_id"] = vc_id
                self._result = {k: row[k] for k in _LICENSE_KEYS}
                self.rowcount = 1
            else:
                self._result = None
                self.rowcount = 0
        elif s.startswith("update fm_licenses set status = 'revoked'"):
            # 재등록으로 갈아탄 옛 라이선스 정리(같은 모델, 자기 자신 제외).
            model_id, keep_id = params[:2]
            rows = [
                r for r in licenses
                if r["model_id"] == model_id
                and r["id"] != keep_id
                and r["status"] == "reverification_required"
            ]
            for r in rows:
                r["status"] = "revoked"
            self._many = [{"id": r["id"], "vc_id": r.get("vc_id")} for r in rows]
            self.rowcount = len(rows)
        elif s.startswith("update fm_models set status = 'verified'"):
            user_did, model_id, enrollment_id = params[:3]
            if self.store.get("final_model_update_misses"):
                self._result = None
                self.rowcount = 0
                return
            m = next(
                (r for r in models
                 if r["id"] == model_id
                 and r.get("current_enrollment_id") == enrollment_id
                 and r["status"] in {"pending", "reverification_required"}),
                None,
            )
            if m:
                m["status"] = "verified"
                if user_did and not m.get("did"):
                    m["did"] = user_did
                self._result = {"id": m["id"]}
                self.rowcount = 1
            else:
                self._result = None
                self.rowcount = 0
        elif s.startswith("update fm_biometric_enrollments set status = 'passed'"):
            vc_id, enrollment_id = params[:2]
            if self.store.get("final_enrollment_update_misses"):
                self._result = None
                self.rowcount = 0
                return
            e = next(
                (r for r in self.store["enrollments"]
                 if r["id"] == enrollment_id and r["status"] == "vc_pending"),
                None,
            )
            if e:
                e["status"] = "passed"
                e["decision"] = "passed"
                e["vc_id"] = vc_id
                e["completed_at"] = NOW
                self._result = {"id": e["id"]}
                self.rowcount = 1
            else:
                self._result = None
                self.rowcount = 0
        elif s.startswith("insert into fm_vc_revocation_jobs"):
            license_id, model_id, vc_id = params[:3]
            self.store.setdefault("revocations", {}).setdefault(
                vc_id,
                {
                    "license_id": license_id,
                    "model_id": model_id,
                    "vc_id": vc_id,
                    "status": "pending",
                },
            )
            self.rowcount = 1
        elif s.startswith("select l.id::text as id, l.model_id::text as model_id"):
            # 목록: 소유 모델 경유. revoked 는 죽은 카드라 목록에서 빠진다.
            owned = {m["id"] for m in models if m["user_id"] == params[0]}
            rows = [
                r for r in licenses
                if r["model_id"] in owned
                and not ("l.status <> 'revoked'" in s and r["status"] == "revoked")
            ]
            self._many = [{k: r[k] for k in _LICENSE_KEYS} for r in rows]
        elif s.startswith("select l.face_image_key, l.status"):
            # 게이트: license id + 소유자 조인
            lid, uid = params
            owned = {m["id"] for m in models if m["user_id"] == uid}
            r = next((x for x in licenses if x["id"] == lid and x["model_id"] in owned), None)
            self._result = (
                {"face_image_key": r["face_image_key"], "status": r["status"],
                 "license_valid_until": r["license_valid_until"]}
                if r else None
            )
        elif s.startswith("select id::text as id, status from personalization_profiles"):
            # 개인화 프로필 — 소유자 스코프 + purged 제외를 SQL 이 하므로 페이크도 동일하게
            pid, uid = params
            p = next(
                (x for x in self.store["profiles"]
                 if x["id"] == pid and x["user_id"] == uid and x["status"] != "purged"),
                None,
            )
            self._result = {"id": p["id"], "status": p["status"]} if p else None
        elif s.startswith("select r2_key, image_digest from personalization_face_photos"):
            pid = params[0]
            ph = next(
                (x for x in self.store["face_photos"]
                 if x["profile_id"] == pid and x["angle"] == "front"),
                None,
            )
            self._result = (
                {"r2_key": ph["r2_key"], "image_digest": ph["image_digest"]} if ph else None
            )
        elif s.startswith("select l.status, l.allowed_use"):
            # 공개 검증(QR) — 라이선스 + 모델 표시명 + 최신 본인확인의 birthYear
            lid = params[0]
            lic = next((x for x in licenses if x["id"] == lid), None)
            if lic is None:
                self._result = None
            else:
                m = next((x for x in models if x["id"] == lic["model_id"]), None)
                ident = next(
                    (i for i in self.store["identities"] if i["model_id"] == lic["model_id"]), None
                )
                self._result = {
                    "status": lic["status"], "allowed_use": lic["allowed_use"],
                    "forbidden_use": lic["forbidden_use"], "unit_price": lic["unit_price"],
                    "license_valid_until": lic["license_valid_until"], "vc_id": lic["vc_id"],
                    "display_name": (m or {}).get("display_name") or "",
                    "birth_year": (ident or {}).get("birth_year"),
                }
        else:  # pragma: no cover
            raise AssertionError(f"unexpected SQL: {s}")

    async def fetchone(self):
        return self._result

    async def fetchall(self):
        return self._many or []


class FakeConn:
    def __init__(self, store):
        self.store = store
        self._snapshot = copy.deepcopy(store)

    def cursor(self):
        self.store["cursor_count"] = self.store.get("cursor_count", 0) + 1
        return FakeCursor(self.store)

    async def commit(self):
        self._snapshot = copy.deepcopy(self.store)
        return None

    async def rollback(self):
        self.store.clear()
        self.store.update(copy.deepcopy(self._snapshot))
        winner_vc = self.store.pop("winner_after_rollback", None)
        if winner_vc:
            self.store["licenses"][0].update(status="active", vc_id=winner_vc)
            self.store["models"][0]["status"] = "verified"
            self.store["enrollments"][0].update(status="passed", vc_id=winner_vc)


@pytest.fixture()
def fm(keypair, monkeypatch):
    _priv, public_key = keypair
    app = create_app(make_settings(facemarket_enabled=True, fm_ci_pepper="pep"))
    app.state.jwt_key_resolver = lambda token: public_key
    fake_r2 = FakeR2Face()
    app.state.r2_face = fake_r2

    # user-1 소유의 verified 모델 1개 시드
    store = {
        "models": [
            {"id": "model-1", "user_id": "user-1", "status": "verified", "display_name": "홍*동"}
        ],
        "licenses": [],
        "enrollments": [],
        "enrollment_photos": [],
        "assets": [],
        "profiles": [],     # 개인화 프로필 {id, user_id, status}
        "face_photos": [],  # 개인화 얼굴 슬롯 {profile_id, angle, r2_key, image_digest}
        "identities": [],   # fm_identity_verifications {model_id, birth_year}
        "revocations": {},
        "account_closed": False,
    }

    @contextlib.asynccontextmanager
    async def fake_get_conn(_request):
        yield FakeConn(store)

    monkeypatch.setattr(facemarket, "get_conn", fake_get_conn)
    return TestClient(app), store, fake_r2


@pytest.fixture()
def biometric_fm(keypair, monkeypatch):
    _priv, public_key = keypair
    monkeypatch.setattr(
        facemarket_enrollment,
        "build_biometric_aws_clients",
        lambda _settings: (object(), object()),
    )
    app = create_app(make_settings(
        app_env="dev",
        facemarket_enabled=True,
        fm_biometric_enrollment_enabled=True,
        fm_oacx_contract_mode="dev-mock-v1",
        fm_liveness_browser_role_arn="arn:aws:iam::123456789012:role/test",
        fm_liveness_confidence_threshold=90.0,
        fm_id_live_threshold=0.45,
        fm_retouched_live_threshold=0.40,
        fm_match_policy_version=EVIDENCE_VERSION,
        fm_ci_pepper="pep",
        fm_face_qc_enabled=True,
        opendid_holder_url="http://holder.test",
        opendid_holder_hmac_secret="shared-secret",
    ))
    app.state.jwt_key_resolver = lambda token: public_key
    app.state.r2_face = FakeR2Face()
    store = {
        "models": [
            {
                "id": MODEL_ID, "user_id": "user-1", "status": "pending",
                "display_name": "홍*동", "assets_status": "ready",
                "current_enrollment_id": ENROLLMENT_ID, "did": None,
            }
        ],
        "licenses": [],
        "enrollments": [],
        "enrollment_photos": [],
        "assets": [],
        "profiles": [],
        "face_photos": [],
        "identities": [],
        "revocations": {},
        "account_closed": False,
    }

    @contextlib.asynccontextmanager
    async def fake_get_conn(_request):
        yield FakeConn(store)

    monkeypatch.setattr(facemarket, "get_conn", fake_get_conn)
    return TestClient(app), store, app.state.r2_face


class HolderStub:
    def __init__(self):
        self.calls = []
        self.fail_with_status = None
        self.fail_path = None
        self.malformed_issue = False
        self.wallet_status = 201
        self.register_body = {"flowAComplete": True, "userDid": "did:dev:user-1"}
        self.issue_body = {"vcId": "vc:dev:1", "userDid": "did:dev:user-1"}
        self.after_issue = None


class _HolderResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = {} if body is None else body
        self.text = "SECRET_HOLDER_BODY_WITH_CLAIMS"

    def json(self):
        return self._body


@pytest.fixture()
def holder_stub(monkeypatch):
    stub = HolderStub()

    async def fake_post(_client, **kwargs):
        stub.calls.append(kwargs)
        path = kwargs["path"]
        if stub.fail_with_status and (
            stub.fail_path is None or path.endswith(stub.fail_path)
        ):
            return _HolderResponse(stub.fail_with_status, {"error": "SECRET_CLAIM"})
        if path.endswith("/wallet"):
            return _HolderResponse(stub.wallet_status, {"walletId": "wallet-1"})
        if path.endswith("/register-did"):
            return _HolderResponse(200, stub.register_body)
        if stub.malformed_issue:
            return _HolderResponse(200, {"userDid": "did:dev:user-1", "claims": "SECRET_CLAIM"})
        if stub.after_issue:
            stub.after_issue()
        return _HolderResponse(200, stub.issue_body)

    monkeypatch.setattr(holder_client, "post", fake_post)
    return stub


def _auth(make_token, sub="user-1"):
    return {"Authorization": f"Bearer {make_token(sub=sub)}"}


def _png():
    return ("face.png", b"\x89PNG\r\n\x1a\nFAKEBYTES", "image/png")


def valid_license_body(enrollment_id=ENROLLMENT_ID):
    return {
        "enrollmentId": enrollment_id,
        "allowedUse": ["상의"],
        "forbiddenUse": ["속옷·란제리"],
        "unitPrice": 10000,
        "validDays": 365,
    }


def test_license_use_categories_are_the_exact_approved_sets():
    assert facemarket.ALLOWED_BRAND_USE_CATEGORIES == (
        "상의",
        "하의",
        "아우터",
        "원피스",
        "니트·스웨터",
        "데님",
        "셋업·수트",
        "스커트",
        "트레이닝·애슬레저",
        "잡화·액세서리",
        "뷰티·화장품",
    )
    assert facemarket.FORBIDDEN_BRAND_USE_CATEGORIES == (
        "속옷·란제리",
        "수영복·비키니",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("allowedUse", "광고"),
        ("forbiddenUse", "성인"),
        ("allowedUse", "수영복·비키니"),
        ("forbiddenUse", "상의"),
    ],
    ids=[
        "unknown-allowed",
        "unknown-forbidden",
        "forbidden-preset-in-allowed",
        "allowed-preset-in-forbidden",
    ],
)
def test_create_license_rejects_invalid_use_category_before_db_and_holder(
    biometric_fm, make_token, holder_stub, field, value
):
    client, store, _ = biometric_fm
    body = valid_license_body()
    body[field] = [value]

    response = client.post(
        "/v1/facemarket/licenses",
        json=body,
        headers=_auth(make_token),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_use_category"
    assert store.get("sql", []) == []
    assert holder_stub.calls == []


def _assert_biometric_creation_gate(response):
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "biometric_enrollment_required"


def _seed_license_pending_enrollment(store, *, enrollment_id=ENROLLMENT_ID, user_id="user-1"):
    model = store["models"][0]
    model.update(
        {
            "id": MODEL_ID,
            "user_id": user_id,
            "status": "pending",
            "assets_status": "ready",
            "current_enrollment_id": enrollment_id,
            "did": None,
        }
    )
    store["enrollments"].append(
        {
            "id": enrollment_id,
            "user_id": user_id,
            "model_id": MODEL_ID,
            "status": "license_pending",
            "decision": "passed",
            "consent_version": facemarket_enrollment.BIOMETRIC_CONSENT_VERSION,
            "match_policy_version": EVIDENCE_VERSION,
            "vc_id": None,
            "created_at": NOW,
            "completed_at": None,
        }
    )
    store["enrollment_photos"].append(
        {
            "enrollment_id": enrollment_id,
            "angle": "front",
            "r2_key": APPROVED_FRONT_KEY,
            "image_digest": APPROVED_FRONT_DIGEST,
            "mime_type": "image/webp",
            "storage_state": "approved",
        }
    )
    store["assets"].extend(
        [
            {
                "model_id": MODEL_ID,
                "view": "face_front",
                "r2_key": FRONT_ASSET_KEY,
                "bucket": "face",
                "mime": "image/png",
                "source_enrollment_id": enrollment_id,
                "evidence_version": EVIDENCE_VERSION,
            },
            {
                "model_id": MODEL_ID,
                "view": "grid_sedcard",
                "r2_key": GRID_ASSET_KEY,
                "bucket": "face",
                "mime": "image/png",
                "source_enrollment_id": enrollment_id,
                "evidence_version": EVIDENCE_VERSION,
            },
        ]
    )
    return enrollment_id


def _seed_active_license(
    store,
    r2,
    *,
    license_id="44444444-4444-4444-4444-444444444444",
    model_id="model-1",
    key="private/facemarket/front.png",
    digest="sha256-seeded-face",
    allowed_use=None,
    forbidden_use=None,
    unit_price=5000,
    data=b"\x89PNG\r\n\x1a\nFAKEBYTES",
):
    row = {
        "id": license_id,
        "model_id": model_id,
        "face_image_uri": f"/v1/facemarket/licenses/{license_id}/face",
        "face_image_key": key,
        "face_image_digest": digest,
        "allowed_use": [allowed_use] if isinstance(allowed_use, str) else (allowed_use or []),
        "forbidden_use": [forbidden_use] if isinstance(forbidden_use, str) else (forbidden_use or []),
        "unit_price": unit_price,
        "license_valid_until": datetime.now(timezone.utc) + timedelta(days=365),
        "status": "active",
        "vc_id": "vc:seeded",
        "created_at": NOW,
        "profile_id": None,
    }
    store["licenses"].append(row)
    r2.objects[key] = (data, "image/png")
    return {"id": license_id, "faceImageDigest": digest}


def _seed_pending_license(
    store,
    *,
    license_id="55555555-5555-5555-5555-555555555555",
    enrollment_id=ENROLLMENT_ID,
    allowed_use=None,
    forbidden_use=None,
    unit_price=7000,
    valid_until=None,
    digest="sha256-persisted-front",
):
    row = {
        "id": license_id,
        "model_id": MODEL_ID,
        "enrollment_id": enrollment_id,
        "face_image_uri": f"/v1/facemarket/licenses/{license_id}/face",
        "face_image_key": APPROVED_FRONT_KEY,
        "face_image_digest": digest,
        "allowed_use": allowed_use or ["persisted allowed"],
        "forbidden_use": forbidden_use or ["persisted forbidden"],
        "unit_price": unit_price,
        "license_valid_until": valid_until or datetime(2027, 1, 1, tzinfo=timezone.utc),
        "status": "pending",
        "vc_id": None,
        "created_at": NOW,
        "profile_id": None,
    }
    store["licenses"].append(row)
    return row


def _create_current_license(biometric_fm, make_token):
    client, store, r2 = biometric_fm
    enrollment_id = _seed_license_pending_enrollment(store)
    r2.objects.update(
        {
            APPROVED_FRONT_KEY: (APPROVED_FRONT_BYTES, "image/webp"),
            FRONT_ASSET_KEY: (b"derived-front", "image/png"),
            GRID_ASSET_KEY: (b"derived-grid", "image/png"),
        }
    )
    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )
    assert response.status_code == 201, response.text
    r2.gets.clear()
    return response.json()["id"]


def test_license_starts_pending_and_activates_only_after_vc(
    biometric_fm, make_token, holder_stub
):
    client, store, _ = biometric_fm
    enrollment_id = _seed_license_pending_enrollment(store)
    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )
    assert response.status_code == 201, response.text
    card = response.json()
    assert card["status"] == "active"
    assert card["vcId"] == "vc:dev:1"
    assert card["faceImageUri"] == f"/v1/facemarket/licenses/{card['id']}/face"
    assert APPROVED_FRONT_KEY not in response.text
    assert store["licenses"][0]["status"] == "active"
    assert store["licenses"][0]["face_image_key"] == APPROVED_FRONT_KEY
    assert store["models"][0]["status"] == "verified"
    assert store["enrollments"][0]["status"] == "passed"
    issue_call = next(c for c in holder_stub.calls if c["path"].endswith("/issue-vc"))
    assert issue_call["payload"]["idempotencyKey"] == f"fm-license:{card['id']}"
    assert all(c["secret"] == "shared-secret" for c in holder_stub.calls)


def test_license_terms_are_normalized_once_for_storage_and_holder_claims(
    biometric_fm, make_token, holder_stub
):
    client, store, _ = biometric_fm
    enrollment_id = _seed_license_pending_enrollment(store)

    response = client.post(
        "/v1/facemarket/licenses",
        json={
            **valid_license_body(enrollment_id),
            "allowedUse": [
                "  상의  ",
                "",
                "하의",
                "상의",
            ],
            "forbiddenUse": [
                "  속옷·란제리  ",
                "\t",
                "수영복·비키니",
                "속옷·란제리",
            ],
        },
        headers=_auth(make_token),
    )

    assert response.status_code == 201, response.text
    assert response.json()["allowedUse"] == ["상의", "하의"]
    assert response.json()["forbiddenUse"] == ["속옷·란제리", "수영복·비키니"]
    issue_call = next(c for c in holder_stub.calls if c["path"].endswith("/issue-vc"))
    assert issue_call["payload"]["claims"]["allowedUse"] == "상의, 하의"
    assert issue_call["payload"]["claims"]["forbiddenUse"] == "속옷·란제리, 수영복·비키니"


def test_holder_failure_leaves_everything_non_active(
    biometric_fm, make_token, holder_stub, caplog
):
    client, store, _ = biometric_fm
    holder_stub.fail_with_status = 503
    holder_stub.fail_path = "/issue-vc"
    enrollment_id = _seed_license_pending_enrollment(store)
    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "vc_issue_delayed"
    assert store["licenses"][0]["status"] == "pending"
    assert store["models"][0]["status"] != "verified"
    assert store["enrollments"][0]["status"] == "vc_pending"
    assert "SECRET_HOLDER_BODY_WITH_CLAIMS" not in response.text
    assert "SECRET_CLAIM" not in caplog.text


def test_repeated_pending_post_reuses_license_and_holder_idempotency(
    biometric_fm, make_token, holder_stub
):
    client, store, _ = biometric_fm
    holder_stub.fail_with_status = 503
    holder_stub.fail_path = "/issue-vc"
    enrollment_id = _seed_license_pending_enrollment(store)
    first = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )
    second = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )
    assert first.status_code == second.status_code == 503
    assert len(store["licenses"]) == 1
    license_id = store["licenses"][0]["id"]
    assert [
        c["payload"]["idempotencyKey"]
        for c in holder_stub.calls
        if c["path"].endswith("/issue-vc")
    ] == [f"fm-license:{license_id}", f"fm-license:{license_id}"]


@pytest.mark.parametrize(
    ("allowed_use", "forbidden_use"),
    [
        (["legacy allowed"], ["속옷·란제리"]),
        (["상의"], ["legacy forbidden"]),
    ],
    ids=["invalid-stored-allowed", "invalid-stored-forbidden"],
)
def test_pending_retry_rejects_invalid_persisted_terms_before_enrollment_or_holder(
    biometric_fm, make_token, holder_stub, allowed_use, forbidden_use
):
    client, store, _ = biometric_fm
    enrollment_id = _seed_license_pending_enrollment(store)
    _seed_pending_license(
        store,
        enrollment_id=enrollment_id,
        allowed_use=allowed_use,
        forbidden_use=forbidden_use,
    )

    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_use_category"
    assert store["enrollments"][0]["status"] == "license_pending"
    assert holder_stub.calls == []


def test_active_retry_returns_existing_card_without_reissue(
    biometric_fm, make_token, holder_stub
):
    client, store, _ = biometric_fm
    enrollment_id = _seed_license_pending_enrollment(store)
    first = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )
    before = len(holder_stub.calls)
    second = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert len(holder_stub.calls) == before


def test_enrollment_contract_rejects_stale_foreign_and_incomplete_assets(
    biometric_fm, make_token, holder_stub
):
    client, store, _ = biometric_fm
    _seed_license_pending_enrollment(store)
    foreign = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(OTHER_ENROLLMENT_ID),
        headers=_auth(make_token),
    )
    assert foreign.status_code == 404
    store["models"][0]["current_enrollment_id"] = OTHER_ENROLLMENT_ID
    stale = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(ENROLLMENT_ID),
        headers=_auth(make_token),
    )
    assert stale.status_code == 409
    store["models"][0]["current_enrollment_id"] = ENROLLMENT_ID
    store["assets"] = [a for a in store["assets"] if a["view"] != "grid_sedcard"]
    incomplete = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(ENROLLMENT_ID),
        headers=_auth(make_token),
    )
    assert incomplete.status_code == 409
    assert holder_stub.calls == []
    assert store["licenses"] == []


def test_multipart_license_request_never_creates_row(biometric_fm, make_token, holder_stub):
    client, store, _ = biometric_fm
    _seed_license_pending_enrollment(store)
    response = client.post(
        "/v1/facemarket/licenses",
        files={"face": _png()},
        data={"unit_price": "1000"},
        headers=_auth(make_token),
    )
    assert response.status_code in {400, 415, 422}
    assert store["licenses"] == []
    assert holder_stub.calls == []


def test_malformed_holder_issue_response_stays_pending(
    biometric_fm, make_token, holder_stub, caplog
):
    client, store, _ = biometric_fm
    holder_stub.malformed_issue = True
    enrollment_id = _seed_license_pending_enrollment(store)
    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "vc_issue_delayed"
    assert store["licenses"][0]["status"] == "pending"
    assert store["models"][0]["status"] != "verified"
    assert "SECRET_CLAIM" not in response.text
    assert "SECRET_CLAIM" not in caplog.text


def test_final_activation_rejects_suspended_model_and_rolls_back(
    biometric_fm, make_token, holder_stub
):
    client, store, _ = biometric_fm
    enrollment_id = _seed_license_pending_enrollment(store)
    store["models"][0]["status"] = "suspended"

    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )

    assert response.status_code == 409
    assert store["licenses"][0]["status"] == "pending"
    assert store["licenses"][0]["vc_id"] is None
    assert store["models"][0]["status"] == "suspended"
    assert store["enrollments"][0]["status"] == "vc_pending"
    assert set(store["revocations"]) == {"vc:dev:1"}


@pytest.mark.parametrize(
    "mutate_evidence",
    [
        lambda store: store["enrollment_photos"][0].update(
            image_digest="sha256-replaced-front"
        ),
        lambda store: store["models"][0].update(
            current_enrollment_id=OTHER_ENROLLMENT_ID
        ),
        lambda store: store["assets"][0].update(evidence_version="replaced-policy"),
        lambda store: store["enrollments"][0].update(status="failed"),
    ],
    ids=["front-digest", "current-enrollment", "asset-version", "enrollment-status"],
)
def test_final_activation_rechecks_current_evidence_and_queues_issued_vc(
    biometric_fm, make_token, holder_stub, mutate_evidence
):
    client, store, _ = biometric_fm
    enrollment_id = _seed_license_pending_enrollment(store)
    holder_stub.after_issue = lambda: mutate_evidence(store)

    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )

    assert response.status_code == 409
    assert store["licenses"][0]["status"] == "pending"
    assert store["licenses"][0]["vc_id"] is None
    assert store["models"][0]["status"] != "verified"
    assert set(store["revocations"]) == {"vc:dev:1"}


def test_final_activation_concurrent_winner_returns_active_card(
    biometric_fm, make_token, holder_stub
):
    client, store, _ = biometric_fm
    enrollment_id = _seed_license_pending_enrollment(store)

    def concurrent_winner():
        lic = store["licenses"][0]
        lic["status"] = "active"
        lic["vc_id"] = "vc:dev:1"
        store["models"][0]["status"] = "verified"
        store["enrollments"][0]["status"] = "passed"
        store["enrollments"][0]["vc_id"] = "vc:dev:1"

    holder_stub.after_issue = concurrent_winner
    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )

    assert response.status_code == 201, response.text
    assert response.json()["id"] == store["licenses"][0]["id"]
    assert response.json()["status"] == "active"
    assert response.json()["vcId"] == "vc:dev:1"
    assert store["revocations"] == {}


def test_final_activation_different_concurrent_winner_queues_loser_vc(
    biometric_fm, make_token, holder_stub
):
    client, store, _ = biometric_fm
    enrollment_id = _seed_license_pending_enrollment(store)

    def concurrent_winner():
        license_row = store["licenses"][0]
        license_row["status"] = "active"
        license_row["vc_id"] = "vc:winner"
        store["models"][0]["status"] = "verified"
        store["enrollments"][0]["status"] = "passed"
        store["enrollments"][0]["vc_id"] = "vc:winner"

    holder_stub.after_issue = concurrent_winner
    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )

    assert response.status_code == 201
    assert response.json()["vcId"] == "vc:winner"
    assert set(store["revocations"]) == {"vc:dev:1"}


def test_final_activation_queues_vc_after_license_and_model_are_deleted(
    biometric_fm, make_token, holder_stub
):
    client, store, _ = biometric_fm
    enrollment_id = _seed_license_pending_enrollment(store)
    deleted = {}

    def delete_activation_rows():
        deleted["license_id"] = store["licenses"][0]["id"]
        deleted["model_id"] = store["models"][0]["id"]
        store["licenses"].clear()
        store["models"].clear()

    holder_stub.after_issue = delete_activation_rows
    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )

    assert response.status_code == 409
    assert store["licenses"] == []
    assert store["models"] == []
    assert store["revocations"]["vc:dev:1"] == {
        "license_id": deleted["license_id"],
        "model_id": deleted["model_id"],
        "vc_id": "vc:dev:1",
        "status": "pending",
    }


@pytest.mark.parametrize(
    "winner_vc,expected_revocations",
    [("vc:dev:1", set()), ("vc:other", {"vc:dev:1"})],
)
def test_final_activation_cas_race_returns_winner_and_revokes_only_loser(
    biometric_fm, make_token, holder_stub, winner_vc, expected_revocations
):
    client, store, _ = biometric_fm
    enrollment_id = _seed_license_pending_enrollment(store)
    store["final_license_update_misses"] = True
    store["winner_after_rollback"] = winner_vc

    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )

    assert response.status_code == 201
    assert response.json()["vcId"] == winner_vc
    assert set(store["revocations"]) == expected_revocations


@pytest.mark.parametrize(
    "miss_flag",
    ["final_model_update_misses", "final_enrollment_update_misses"],
)
def test_final_activation_cas_failure_rolls_back_all_updates_and_queues_vc(
    biometric_fm, make_token, holder_stub, miss_flag
):
    client, store, _ = biometric_fm
    enrollment_id = _seed_license_pending_enrollment(store)
    store[miss_flag] = True

    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )

    assert response.status_code == 409
    assert store["licenses"][0]["status"] == "pending"
    assert store["licenses"][0]["vc_id"] is None
    assert store["models"][0]["status"] == "pending"
    assert store["enrollments"][0]["status"] == "vc_pending"
    assert store["enrollments"][0]["vc_id"] is None
    assert set(store["revocations"]) == {"vc:dev:1"}


def test_final_activation_rejects_batch_linked_pre_completion_evidence_and_revokes_vc(
    biometric_fm, make_token, holder_stub
):
    """Break caught: stale batch-linked enrollment evidence can activate after cutover close."""
    client, store, _ = biometric_fm
    enrollment_id = _seed_license_pending_enrollment(store)
    store["models"][0].update(
        reverification_batch_id="batch-1",
        batch_status="completed",
        batch_completed_at=NOW,
    )
    store["enrollments"][0]["created_at"] = NOW

    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "license_activation_stale"
    assert store["licenses"][0]["status"] == "pending"
    assert store["revocations"]["vc:dev:1"]["vc_id"] == "vc:dev:1"
    assert any(
        "m.reverification_batch_id::text as model_reverification_batch_id" in sql
        and "l.reverification_batch_id::text as license_reverification_batch_id" in sql
        for sql in store["sql"]
    )


def test_final_activation_allows_batch_linked_strict_post_completion_evidence(
    biometric_fm, make_token, holder_stub
):
    """Break caught: completed cutover would stay permanently closed to fresh re-enrollment."""
    client, store, _ = biometric_fm
    enrollment_id = _seed_license_pending_enrollment(store)
    store["models"][0].update(
        reverification_batch_id="batch-1",
        batch_status="completed",
        batch_completed_at=NOW - timedelta(seconds=1),
    )
    store["enrollments"][0]["created_at"] = NOW

    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )

    assert response.status_code == 201
    assert store["licenses"][0]["status"] == "active"
    assert store["models"][0]["reverification_batch_id"] == "batch-1"
    assert store.get("revocations", {}) == {}


@pytest.mark.parametrize("register_body", [[], "not-object", None])
def test_malformed_holder_register_body_is_closed_502(
    biometric_fm, make_token, holder_stub, register_body
):
    client, store, _ = biometric_fm
    holder_stub.register_body = register_body
    enrollment_id = _seed_license_pending_enrollment(store)

    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "vc_issue_delayed"
    assert store["licenses"][0]["status"] == "pending"


@pytest.mark.parametrize("issue_body", [[], "not-object", None, {"vcId": ""}, {"vcId": 123}])
def test_malformed_holder_issue_body_is_closed_502(
    biometric_fm, make_token, holder_stub, issue_body, caplog
):
    client, store, _ = biometric_fm
    holder_stub.issue_body = issue_body
    enrollment_id = _seed_license_pending_enrollment(store)

    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "vc_issue_delayed"
    assert store["licenses"][0]["status"] == "pending"
    assert "SECRET_HOLDER_BODY_WITH_CLAIMS" not in response.text
    assert "SECRET_CLAIM" not in caplog.text


def test_license_creation_flag_off_rejects_json_and_multipart_before_db(
    fm, make_token, holder_stub
):
    client, store, _ = fm

    json_response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(),
        headers=_auth(make_token),
    )
    multipart_response = client.post(
        "/v1/facemarket/licenses",
        files={"face": _png()},
        data={"unit_price": "1000"},
        headers=_auth(make_token),
    )

    assert json_response.status_code == 409
    assert json_response.json()["error"]["code"] == "biometric_enrollment_required"
    assert multipart_response.status_code == 409
    assert multipart_response.json()["error"]["code"] == "biometric_enrollment_required"
    assert store["licenses"] == []
    assert store.get("sql", []) == []
    assert holder_stub.calls == []


def test_malformed_enrollment_uuid_rejected_before_sql(
    biometric_fm, make_token, holder_stub
):
    client, store, _ = biometric_fm
    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body("not-a-uuid"),
        headers=_auth(make_token),
    )

    assert response.status_code in {400, 404}
    assert store.get("sql", []) == []
    assert holder_stub.calls == []


@pytest.mark.parametrize(
    ("allowed_use", "forbidden_use"),
    [
        (["legacy allowed"], ["속옷·란제리"]),
        (["상의"], ["legacy forbidden"]),
    ],
    ids=["invalid-stored-allowed", "invalid-stored-forbidden"],
)
def test_conflict_reload_rejects_invalid_persisted_terms_before_enrollment_or_holder(
    biometric_fm, make_token, holder_stub, allowed_use, forbidden_use
):
    client, store, _ = biometric_fm
    enrollment_id = _seed_license_pending_enrollment(store)
    _seed_pending_license(
        store,
        enrollment_id=enrollment_id,
        allowed_use=allowed_use,
        forbidden_use=forbidden_use,
    )
    store["hide_existing_license_once"] = True

    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_use_category"
    assert store["enrollments"][0]["status"] == "license_pending"
    assert holder_stub.calls == []


def test_conflict_reload_uses_persisted_terms_for_holder_claims(
    biometric_fm, make_token, holder_stub
):
    client, store, _ = biometric_fm
    enrollment_id = _seed_license_pending_enrollment(store)
    persisted = _seed_pending_license(
        store,
        enrollment_id=enrollment_id,
        allowed_use=["하의"],
        forbidden_use=["수영복·비키니"],
        unit_price=4321,
        valid_until=datetime(2027, 2, 3, tzinfo=timezone.utc),
        digest="sha256-persisted-digest",
    )
    store["hide_existing_license_once"] = True
    holder_stub.issue_body = {}

    response = client.post(
        "/v1/facemarket/licenses",
        json={
            "enrollmentId": enrollment_id,
            "allowedUse": ["상의"],
            "forbiddenUse": ["속옷·란제리"],
            "unitPrice": 9999,
            "validDays": 30,
        },
        headers=_auth(make_token),
    )

    assert response.status_code == 502
    issue_call = next(c for c in holder_stub.calls if c["path"].endswith("/issue-vc"))
    assert issue_call["payload"]["idempotencyKey"] == f"fm-license:{persisted['id']}"
    assert issue_call["payload"]["claims"] == {
        "allowedUse": "하의",
        "forbiddenUse": "수영복·비키니",
        "unitPrice": 4321,
        "licenseValidUntil": "2027-02-03",
        "faceImageDigest": "sha256-persisted-digest",
    }


def test_final_stale_transition_does_not_report_active(
    biometric_fm, make_token, holder_stub
):
    client, store, _ = biometric_fm
    store["final_license_update_misses"] = True
    enrollment_id = _seed_license_pending_enrollment(store)
    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )
    assert response.status_code == 409
    assert store["licenses"][0]["status"] == "pending"
    assert store["models"][0]["status"] != "verified"
    assert set(store["revocations"]) == {"vc:dev:1"}


def test_biometric_startup_requires_holder_url(monkeypatch):
    monkeypatch.setattr(
        facemarket_enrollment,
        "build_biometric_aws_clients",
        lambda _settings: (object(), object()),
    )
    with pytest.raises(RuntimeError, match="OPENDID_HOLDER_URL"):
        create_app(make_settings(
            app_env="dev",
            facemarket_enabled=True,
            fm_biometric_enrollment_enabled=True,
            fm_oacx_contract_mode="dev-mock-v1",
            fm_liveness_browser_role_arn="arn:aws:iam::123456789012:role/test",
            fm_liveness_confidence_threshold=90.0,
            fm_id_live_threshold=0.45,
            fm_retouched_live_threshold=0.40,
            fm_match_policy_version=EVIDENCE_VERSION,
            fm_ci_pepper="pep",
            fm_face_qc_enabled=True,
            opendid_holder_url=None,
        ))


def test_direct_face_license_request_is_rejected_before_storage(fm, make_token):
    client, store, r2 = fm
    r = client.post(
        "/v1/facemarket/licenses",
        files={"face": _png()},
        data={"allowed_use": ["광고", "상세페이지"], "forbidden_use": ["성인"],
              "unit_price": "5000", "valid_days": "30"},
        headers=_auth(make_token),
    )
    _assert_biometric_creation_gate(r)
    assert len(r2.objects) == 0
    assert store["licenses"] == []


def test_create_license_requires_verified_model(fm, make_token):
    client, _, _ = fm
    r = client.post(
        "/v1/facemarket/licenses",
        files={"face": _png()},
        data={"unit_price": "1000"},
        headers=_auth(make_token, sub="user-2"),
    )
    _assert_biometric_creation_gate(r)


def test_create_license_rejects_non_image(fm, make_token):
    client, _, r2 = fm
    r = client.post(
        "/v1/facemarket/licenses",
        files={"face": ("x.pdf", b"%PDF-1.4", "application/pdf")},
        headers=_auth(make_token),
    )
    _assert_biometric_creation_gate(r)
    assert len(r2.objects) == 0  # 저장 안 됨


def test_create_license_requires_auth(fm):
    client, _, _ = fm
    r = client.post("/v1/facemarket/licenses", files={"face": _png()})
    assert r.status_code == 401


def test_list_licenses_scoped_to_owner(fm, make_token):
    client, store, r2 = fm
    _seed_active_license(store, r2)
    mine = client.get("/v1/facemarket/licenses", headers=_auth(make_token))
    assert mine.status_code == 200
    assert len(mine.json()) == 1
    # 다른 사용자는 못 본다
    other = client.get("/v1/facemarket/licenses", headers=_auth(make_token, sub="user-2"))
    assert other.status_code == 200 and other.json() == []


def test_face_gate_owner_gets_current_approved_bytes_only(
    biometric_fm, make_token, holder_stub
):
    client, store, r2 = biometric_fm
    lid = _create_current_license(biometric_fm, make_token)

    ok = client.get(f"/v1/facemarket/licenses/{lid}/face", headers=_auth(make_token))
    assert ok.status_code == 200
    assert ok.headers["content-type"].startswith("image/webp")
    assert ok.headers["cache-control"] == "no-store, private"
    assert ok.content == APPROVED_FRONT_BYTES
    assert r2.gets == [APPROVED_FRONT_KEY]

    other = client.get(f"/v1/facemarket/licenses/{lid}/face", headers=_auth(make_token, sub="user-2"))
    assert other.status_code == 404
    assert other.json() == {"error": {"code": "not_found", "message": "찾을 수 없습니다."}}
    assert other.headers["cache-control"] == "no-store, private"
    assert r2.gets == [APPROVED_FRONT_KEY]
    owner_sql = next(
        sql for sql in store["sql"] if sql.startswith("select l.face_image_key, p.mime_type")
    )
    assert owner_sql.startswith("select l.face_image_key, p.mime_type from fm_models m")
    _assert_current_card_sql(owner_sql)


def _remove_asset(store, view):
    store["assets"][:] = [row for row in store["assets"] if row["view"] != view]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda store: store["models"][0].update(status="reverification_required"),
        lambda store: store["models"][0].update(assets_status="building"),
        lambda store: store["licenses"][0].update(status="revoked"),
        lambda store: store["licenses"][0].update(status="reverification_required"),
        lambda store: store["licenses"][0].update(
            license_valid_until=datetime(2020, 1, 1, tzinfo=timezone.utc)
        ),
        lambda store: store["models"][0].update(current_enrollment_id=OTHER_ENROLLMENT_ID),
        lambda store: store["licenses"][0].update(enrollment_id=OTHER_ENROLLMENT_ID),
        lambda store: store["enrollments"][0].update(status="cancelled"),
        lambda store: store["enrollments"][0].update(consent_version="old-version"),
        lambda store: store["enrollments"][0].update(match_policy_version=" "),
        lambda store: store["enrollments"][0].update(vc_id="vc:other"),
        lambda store: _remove_asset(store, "face_front"),
        lambda store: _remove_asset(store, "grid_sedcard"),
        lambda store: store["assets"][0].update(bucket="public"),
        lambda store: store["assets"][0].update(mime="text/plain"),
        lambda store: store["assets"][0].update(source_enrollment_id=OTHER_ENROLLMENT_ID),
        lambda store: store["assets"][0].update(evidence_version="stale-policy"),
        lambda store: store["assets"][0].update(r2_key=" "),
        lambda store: store["enrollment_photos"][0].update(storage_state="delete_pending"),
        lambda store: store["enrollment_photos"][0].update(r2_key="different/front.png"),
        lambda store: store["licenses"][0].update(face_image_key=" "),
    ],
    ids=[
        "model-frozen",
        "assets-building",
        "license-revoked",
        "license-frozen",
        "license-expired",
        "model-current-enrollment-changed",
        "license-enrollment-changed",
        "enrollment-cancelled",
        "stale-consent",
        "blank-policy",
        "vc-mismatch",
        "face-asset-missing",
        "grid-asset-missing",
        "public-asset",
        "non-image-asset",
        "stale-asset-enrollment",
        "stale-asset-policy",
        "blank-asset-key",
        "front-delete-pending",
        "front-license-key-mismatch",
        "blank-license-key",
    ],
)
def test_face_gate_denials_never_reach_private_storage(
    biometric_fm, make_token, holder_stub, mutate
):
    client, store, r2 = biometric_fm
    lid = _create_current_license(biometric_fm, make_token)
    mutate(store)

    response = client.get(f"/v1/facemarket/licenses/{lid}/face", headers=_auth(make_token))

    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "찾을 수 없습니다."}}
    assert response.headers["cache-control"] == "no-store, private"
    assert r2.gets == []


def test_face_gate_purge_returns_private_404_without_storage(
    biometric_fm, make_token, holder_stub
):
    client, store, r2 = biometric_fm
    lid = _create_current_license(biometric_fm, make_token)
    store["models"][0]["current_enrollment_id"] = None
    store["enrollments"].clear()
    store["enrollment_photos"].clear()
    store["assets"].clear()

    response = client.get(f"/v1/facemarket/licenses/{lid}/face", headers=_auth(make_token))

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store, private"
    assert r2.gets == []


def test_face_gate_missing_object_is_private_404_after_one_read(
    biometric_fm, make_token, holder_stub
):
    client, _store, r2 = biometric_fm
    lid = _create_current_license(biometric_fm, make_token)
    del r2.objects[APPROVED_FRONT_KEY]

    response = client.get(f"/v1/facemarket/licenses/{lid}/face", headers=_auth(make_token))

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store, private"
    assert r2.gets == [APPROVED_FRONT_KEY]


def test_face_gate_missing_license_404(biometric_fm, make_token):
    client, store, _ = biometric_fm
    r = client.get("/v1/facemarket/licenses/00000000-0000-0000-0000-000000000000/face",
                   headers=_auth(make_token))
    assert r.status_code == 404
    assert r.headers["cache-control"] == "no-store, private"
    before = store.get("cursor_count", 0)
    malformed = client.get(
        "/v1/facemarket/licenses/not-a-uuid/face",
        headers=_auth(make_token),
    )
    assert malformed.status_code == 404
    assert malformed.headers["cache-control"] == "no-store, private"
    assert store.get("cursor_count", 0) == before


def test_face_gate_requires_auth_without_db(biometric_fm):
    client, store, _ = biometric_fm
    before = store.get("cursor_count", 0)
    response = client.get(
        "/v1/facemarket/licenses/44444444-4444-4444-4444-444444444444/face"
    )
    assert response.status_code == 401
    assert store.get("cursor_count", 0) == before


def test_thumbnail_is_fixed_non_biometric_and_never_reads_storage(
    biometric_fm, make_token, holder_stub
):
    client, store, r2 = biometric_fm
    _create_current_license(biometric_fm, make_token)

    response = client.get(
        f"/v1/facemarket/models/{MODEL_ID}/thumbnail",
        headers=_auth(make_token),
    )

    assert response.status_code == 200
    assert response.content == EXPECTED_PLACEHOLDER_SVG
    assert response.content == facemarket._MODEL_PLACEHOLDER_SVG
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.headers["cache-control"] == "no-store, private"
    assert MODEL_ID.encode() not in response.content
    assert r2.gets == []
    sql = store["thumbnail_sql"]
    assert sql.startswith("select 1 as eligible from fm_models m")
    _assert_current_card_sql(sql)
    assert "r2_key" not in sql.split(" from fm_models m", 1)[0]
    assert "cover_image_url" not in sql
    assert "nullif(btrim(a.r2_key), '') is not null" in sql
    assert "p.r2_key = l.face_image_key" in sql
    compiled = _compile_psycopg_query(sql, store["thumbnail_params"])
    assert compiled.count("like 'image/%'") == 3
    assert "image/%%" not in compiled


def test_runtime_license_query_escapes_mime_wildcards_for_psycopg(biometric_fm):
    _, store, _ = biometric_fm

    result = asyncio.run(facemarket.resolve_model_license(FakeConn(store), MODEL_ID))

    # 모델은 존재하지만(left join) 라이선스가 없으므로 라이선스 필드만 비어 있다.
    assert result["model_id"] == MODEL_ID
    assert result["id"] is None
    raw_sql = " ".join(store["runtime_license_sql"].split()).lower()
    assert raw_sql.count("like 'image/%%'") == 4
    compiled = _compile_psycopg_query(
        store["runtime_license_sql"], store["runtime_license_params"]
    )
    assert compiled.count("like 'image/%'") == 4
    assert "image/%%" not in compiled


def test_resolve_model_license_includes_physique(biometric_fm):
    _, store, _ = biometric_fm
    store["models"][0].update(
        gender="male", height_bucket="m_180_185", body_type="toned",
    )

    row = asyncio.run(facemarket.resolve_model_license(FakeConn(store), MODEL_ID))

    assert row["gender"] == "male"
    assert row["height_bucket"] == "m_180_185"
    assert row["body_type"] == "toned"


def test_detail_page_worker_mime_extension_contract_remains_importable():
    assert facemarket._EXT_TO_MIME["png"] == "image/png"
    assert facemarket._EXT_TO_MIME["jpg"] == "image/jpeg"


def _purge_current(store):
    store["models"][0]["current_enrollment_id"] = None
    store["enrollments"].clear()
    store["enrollment_photos"].clear()
    store["assets"].clear()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda store: store["models"][0].update(status="reverification_required"),
        lambda store: store["licenses"][0].update(status="reverification_required"),
        lambda store: store["licenses"][0].update(status="revoked"),
        lambda store: store["licenses"][0].update(
            license_valid_until=datetime(2020, 1, 1, tzinfo=timezone.utc)
        ),
        _purge_current,
        lambda store: store["enrollments"][0].update(consent_version="old-version"),
        lambda store: store["enrollments"][0].update(status="failed"),
        lambda store: store["models"][0].update(assets_status="building"),
        lambda store: store["enrollments"][0].update(vc_id="vc:other"),
        lambda store: store["licenses"][0].update(vc_id=None),
        lambda store: _remove_asset(store, "face_front"),
        lambda store: _remove_asset(store, "grid_sedcard"),
        lambda store: store["assets"][0].update(evidence_version="old-policy"),
        lambda store: store["assets"][1].update(source_enrollment_id=OTHER_ENROLLMENT_ID),
        lambda store: store["assets"][1].update(r2_key=""),
        lambda store: store["enrollment_photos"][0].update(storage_state="delete_pending"),
        lambda store: store["licenses"][0].update(face_image_key="other/front.png"),
    ],
    ids=[
        "model-freeze",
        "license-freeze",
        "revoke",
        "expiry",
        "purge",
        "stale-consent",
        "non-passed-enrollment",
        "assets-not-ready",
        "vc-mismatch",
        "vc-missing",
        "face-asset-missing",
        "grid-asset-missing",
        "face-asset-stale",
        "grid-asset-stale",
        "grid-key-blank",
        "approved-front-missing",
        "license-front-mismatch",
    ],
)
def test_catalog_and_thumbnail_share_current_eligibility(
    biometric_fm, make_token, holder_stub, mutate
):
    client, store, r2 = biometric_fm
    _create_current_license(biometric_fm, make_token)
    mutate(store)

    catalog = client.get("/v1/facemarket/models", headers=_auth(make_token))
    thumbnail = client.get(
        f"/v1/facemarket/models/{MODEL_ID}/thumbnail",
        headers=_auth(make_token),
    )

    assert catalog.status_code == 200
    assert catalog.json() == []
    assert catalog.headers["cache-control"] == "no-store, private"
    assert thumbnail.status_code == 404
    assert thumbnail.json() == {"error": {"code": "not_found", "message": "찾을 수 없습니다."}}
    assert thumbnail.headers["cache-control"] == "no-store, private"
    assert r2.gets == []


def _clone_current_card(store, r2):
    second_model_id = "55555555-5555-5555-5555-555555555555"
    second_enrollment_id = "66666666-6666-6666-6666-666666666666"
    second_license_id = "77777777-7777-7777-7777-777777777777"
    second_front_key = "facemarket/models/second/approved/front.jpg"
    model = copy.deepcopy(store["models"][0])
    model.update(id=second_model_id, user_id="user-2", current_enrollment_id=second_enrollment_id)
    enrollment = copy.deepcopy(store["enrollments"][0])
    enrollment.update(id=second_enrollment_id, user_id="user-2", model_id=second_model_id)
    license_row = copy.deepcopy(store["licenses"][0])
    license_row.update(
        id=second_license_id,
        model_id=second_model_id,
        enrollment_id=second_enrollment_id,
        face_image_key=second_front_key,
    )
    photo = copy.deepcopy(store["enrollment_photos"][0])
    photo.update(enrollment_id=second_enrollment_id, r2_key=second_front_key)
    assets = []
    for asset in store["assets"]:
        clone = copy.deepcopy(asset)
        clone.update(
            model_id=second_model_id,
            source_enrollment_id=second_enrollment_id,
            r2_key=f"facemarket/models/second/assets/{asset['view']}.png",
        )
        assets.append(clone)
        r2.objects[clone["r2_key"]] = (f"different-{clone['view']}".encode(), "image/png")
    store["models"].append(model)
    store["enrollments"].append(enrollment)
    store["licenses"].append(license_row)
    store["enrollment_photos"].append(photo)
    store["assets"].extend(assets)
    r2.objects[second_front_key] = (b"different-approved-face", "image/jpeg")
    return second_model_id


def test_thumbnail_bytes_are_identical_across_models(
    biometric_fm, make_token, holder_stub
):
    client, store, r2 = biometric_fm
    _create_current_license(biometric_fm, make_token)
    second_model_id = _clone_current_card(store, r2)
    store["models"][0]["cover_image_url"] = "https://legacy.example/first.jpg"
    store["models"][1]["cover_image_url"] = "https://legacy.example/second.jpg"

    first = client.get(
        f"/v1/facemarket/models/{MODEL_ID}/thumbnail",
        headers=_auth(make_token),
    )
    second = client.get(
        f"/v1/facemarket/models/{second_model_id}/thumbnail",
        headers=_auth(make_token),
    )

    assert first.status_code == second.status_code == 200
    assert first.content == second.content == EXPECTED_PLACEHOLDER_SVG
    assert r2.gets == []


def test_thumbnail_invalid_uuid_is_private_404_before_db_and_auth_is_401(
    biometric_fm, make_token
):
    client, store, _ = biometric_fm
    before = store.get("cursor_count", 0)
    malformed = client.get(
        "/v1/facemarket/models/not-a-uuid/thumbnail",
        headers=_auth(make_token),
    )
    assert malformed.status_code == 404
    assert malformed.headers["cache-control"] == "no-store, private"
    assert store.get("cursor_count", 0) == before
    unauthenticated = client.get(
        f"/v1/facemarket/models/{MODEL_ID}/thumbnail"
    )
    assert unauthenticated.status_code == 401
    assert store.get("cursor_count", 0) == before


# ── step02: 개인화 프로필 기반 발급 ────────────────────────────
PROFILE_ID = "11111111-1111-1111-1111-111111111111"
FRONT_KEY = f"personalization/profiles/{PROFILE_ID}/faces/front.png"
FRONT_BYTES = b"\x89PNG\r\n\x1a\nPROFILEFRONT"
FRONT_DIGEST = "sha256-frontdigestvalue"


def _seed_profile(store, r2, *, status="ready", user_id="user-1", with_front=True):
    store["profiles"].append({"id": PROFILE_ID, "user_id": user_id, "status": status})
    if with_front:
        store["face_photos"].append(
            {"profile_id": PROFILE_ID, "angle": "front",
             "r2_key": FRONT_KEY, "image_digest": FRONT_DIGEST}
        )
        r2.objects[FRONT_KEY] = (FRONT_BYTES, "image/png")


def test_create_license_from_profile_references_front_slot(fm, make_token):
    """profile_id 직접 발급은 더 이상 라이선스를 만들 수 없다."""
    client, store, r2 = fm
    _seed_profile(store, r2)
    r = client.post(
        "/v1/facemarket/licenses",
        data={"profile_id": PROFILE_ID, "allowed_use": ["광고"], "unit_price": "7000"},
        headers=_auth(make_token),
    )
    _assert_biometric_creation_gate(r)
    assert store["licenses"] == []
    assert len(r2.objects) == 1                     # 프로필 시드뿐 — 새 업로드 0


def test_profile_license_face_gate_streams_profile_bytes(fm, make_token):
    """레거시 개인화 얼굴은 현재 생체 등록 증거를 대신할 수 없다."""
    client, store, r2 = fm
    _seed_profile(store, r2)
    card = _seed_active_license(store, r2, key=FRONT_KEY, digest=FRONT_DIGEST, data=FRONT_BYTES)
    ok = client.get(f"/v1/facemarket/licenses/{card['id']}/face", headers=_auth(make_token))
    assert ok.status_code == 404
    assert ok.headers["cache-control"] == "no-store, private"
    assert r2.gets == []


def test_create_license_rejects_not_ready_profile(fm, make_token):
    client, store, r2 = fm
    _seed_profile(store, r2, status="draft")  # 온보딩 미완(3각도·동의·신체 중 결손)
    r = client.post("/v1/facemarket/licenses", data={"profile_id": PROFILE_ID},
                    headers=_auth(make_token))
    _assert_biometric_creation_gate(r)
    assert store["licenses"] == []


def test_create_license_rejects_foreign_profile(fm, make_token):
    """타인 프로필은 '없는 프로필'과 같은 코드 — 존재 여부가 새지 않는다."""
    client, store, r2 = fm
    _seed_profile(store, r2, user_id="user-2")
    r = client.post("/v1/facemarket/licenses", data={"profile_id": PROFILE_ID},
                    headers=_auth(make_token))
    _assert_biometric_creation_gate(r)
    missing = client.post(
        "/v1/facemarket/licenses",
        data={"profile_id": "22222222-2222-2222-2222-222222222222"},
        headers=_auth(make_token),
    )
    assert missing.json()["error"]["code"] == "biometric_enrollment_required"  # 동일 코드
    assert store["licenses"] == []


def test_create_license_rejects_purged_profile(fm, make_token):
    client, store, r2 = fm
    _seed_profile(store, r2, status="purged")
    r = client.post("/v1/facemarket/licenses", data={"profile_id": PROFILE_ID},
                    headers=_auth(make_token))
    _assert_biometric_creation_gate(r)


def test_create_license_rejects_malformed_profile_id(fm, make_token):
    """비-uuid 는 500 아닌 400."""
    client, _, _ = fm
    r = client.post("/v1/facemarket/licenses", data={"profile_id": "not-a-uuid"},
                    headers=_auth(make_token))
    _assert_biometric_creation_gate(r)


def test_create_license_requires_face_or_profile(fm, make_token):
    client, _, r2 = fm
    r = client.post("/v1/facemarket/licenses", data={"unit_price": "1000"},
                    headers=_auth(make_token))
    _assert_biometric_creation_gate(r)
    assert len(r2.objects) == 0


def test_create_license_rejects_face_and_profile_together(fm, make_token):
    """둘 다 오면 명시적 거절 — 어느 얼굴을 라이선스했는지 모호해지면 안 된다."""
    client, store, r2 = fm
    _seed_profile(store, r2)
    r = client.post("/v1/facemarket/licenses", files={"face": _png()},
                    data={"profile_id": PROFILE_ID}, headers=_auth(make_token))
    _assert_biometric_creation_gate(r)
    assert store["licenses"] == []
    assert len(r2.objects) == 1  # 프로필 얼굴만 — 업로드분 저장 0


def test_legacy_face_license_records_null_profile(fm, make_token):
    """레거시 face 1장 경로는 제거되어 라이선스 행을 만들지 않는다."""
    client, store, _ = fm
    r = client.post("/v1/facemarket/licenses", files={"face": _png()},
                    data={"unit_price": "1000"}, headers=_auth(make_token))
    _assert_biometric_creation_gate(r)
    assert store["licenses"] == []


# ── step02: 공개 검증(QR — 무인증) ─────────────────────────────
_PUBLIC_KEYS = {
    "valid", "status", "allowedUse", "forbiddenUse", "unitPrice", "validUntil", "vcId", "model",
}


def _make_license(store, r2, **data):
    return _seed_active_license(
        store,
        r2,
        allowed_use=data.get("allowed_use"),
        forbidden_use=data.get("forbidden_use"),
        unit_price=int(data.get("unit_price", 5000)),
    )


def test_public_verify_exposes_only_whitelist_no_pii(fm, make_token):
    """🔴 하드룰 — 무인증 라우트에 얼굴·신원·내부키가 한 톨도 실리면 안 된다(영구 유출)."""
    client, store, r2 = fm
    store["identities"].append({"model_id": "model-1", "birth_year": "1996"})
    card = _make_license(store, r2, allowed_use="광고", forbidden_use="성인")
    r = client.get(f"/v1/facemarket/verify/{card['id']}")  # Authorization 헤더 없음
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == _PUBLIC_KEYS
    assert set(body["model"]) == {"nameMasked", "age"}
    assert body["valid"] is True and body["status"] == "active"
    assert body["allowedUse"] == ["광고"] and body["forbiddenUse"] == ["성인"]
    assert body["unitPrice"] == 5000
    assert body["model"]["nameMasked"] == "홍*동"
    assert body["model"]["age"] == datetime.now(timezone.utc).year - 1996 - 1  # 보수적 하한
    # 유출 금지 값이 응답 본문 어디에도 등장하지 않는지 원문으로 확인
    raw = r.text
    for leaked in (
        card["faceImageDigest"],          # 얼굴 digest(생체 파생 고정 식별자)
        "sha256-", "face_image", "faceImage", "faceImageKey",
        "facemarket/models",              # 내부 R2 키스페이스
        "model-1", "user-1",              # model_id · user_id
        "ci_hash", "birthYear", "1996",   # CI 해시 · 생년(원문)
    ):
        assert leaked not in raw, f"공개 검증 응답에 {leaked!r} 유출"
    assert r.headers["cache-control"] == "no-store"


def test_public_verify_requires_no_auth_and_hides_nothing_else(fm, make_token):
    """무인증 도달 확인 — 인증 헤더 유무와 무관하게 같은 응답."""
    client, store, r2 = fm
    card = _make_license(store, r2)
    anon = client.get(f"/v1/facemarket/verify/{card['id']}")
    authed = client.get(f"/v1/facemarket/verify/{card['id']}", headers=_auth(make_token))
    assert anon.status_code == authed.status_code == 200
    assert anon.json() == authed.json()
    # 타인 토큰으로도 동일(공개 라우트)
    other = client.get(f"/v1/facemarket/verify/{card['id']}", headers=_auth(make_token, sub="user-2"))
    assert other.status_code == 200


def test_public_verify_revoked_is_invalid(fm, make_token):
    client, store, r2 = fm
    card = _make_license(store, r2)
    store["licenses"][0]["status"] = "revoked"
    body = client.get(f"/v1/facemarket/verify/{card['id']}").json()
    assert body["valid"] is False and body["status"] == "revoked"


def test_public_verify_expired_is_invalid(fm, make_token):
    """DB status='active' 라도 기간이 지났으면 status='expired' + valid=false(실시간 판정)."""
    client, store, r2 = fm
    card = _make_license(store, r2)
    store["licenses"][0]["license_valid_until"] = datetime(2020, 1, 1, tzinfo=timezone.utc)
    body = client.get(f"/v1/facemarket/verify/{card['id']}").json()
    assert body["valid"] is False and body["status"] == "expired"


def test_public_verify_age_null_when_birth_year_unusable(fm, make_token):
    """birthYear 없음/파싱 불가 → age null(성인 오통과 방지 — 안전측)."""
    client, store, r2 = fm
    card = _make_license(store, r2)
    body = client.get(f"/v1/facemarket/verify/{card['id']}").json()
    assert body["model"]["age"] is None  # identities 미시드
    store["identities"].append({"model_id": "model-1", "birth_year": "0101"})  # MMDD 오염
    body2 = client.get(f"/v1/facemarket/verify/{card['id']}").json()
    assert body2["model"]["age"] is None  # 연도 범위 밖 → null(1900+ 세 오표기 금지)


def test_public_verify_unknown_and_malformed_404(fm):
    client, _, _ = fm
    assert client.get(
        "/v1/facemarket/verify/00000000-0000-0000-0000-000000000000"
    ).status_code == 404
    assert client.get("/v1/facemarket/verify/not-a-uuid").status_code == 404


def test_storage_unavailable_503(keypair, monkeypatch, make_token):
    """multipart 생성은 저장소 확인 전에 거절되어 R2 폴백을 타지 않는다."""
    _priv, public_key = keypair
    app = create_app(make_settings(facemarket_enabled=True, fm_ci_pepper="pep"))
    app.state.jwt_key_resolver = lambda token: public_key
    app.state.r2_face = None  # 저장소 없음

    store = {
        "models": [{"id": "model-1", "user_id": "user-1", "status": "verified"}],
        "licenses": [], "enrollments": [], "enrollment_photos": [], "assets": [],
        "profiles": [], "face_photos": [], "identities": [],
    }

    @contextlib.asynccontextmanager
    async def fake_get_conn(_request):
        yield FakeConn(store)

    monkeypatch.setattr(facemarket, "get_conn", fake_get_conn)
    client = TestClient(app)
    r = client.post("/v1/facemarket/licenses", files={"face": _png()},
                    data={"unit_price": "1000"}, headers={"Authorization": f"Bearer {make_token(sub='user-1')}"})
    _assert_biometric_creation_gate(r)
    assert store["licenses"] == []


def test_enabled_face_features_reject_main_bucket_fallback_in_dev():
    """개발 환경도 생체 얼굴을 공개 가능 메인 버킷에 저장하는 폴백을 허용하지 않는다."""
    settings = make_settings(
        app_env="dev",
        facemarket_enabled=True,
        r2_account_id="account",
        r2_access_key_id="access",
        r2_secret_access_key="secret",
        r2_bucket="main-bucket",
    )
    with pytest.raises(RuntimeError, match="R2_FACE_BUCKET"):
        create_app(settings)


# ── 재등록으로 갈아탄 옛 라이선스 정리 ────────────────────────────────────────
# 새 등록을 시작하면 기존 active 라이선스가 reverification_required 로 강등된다. 그런데 새
# 라이선스가 발급돼도 그 옛 행을 정리하는 코드가 없어, 등록을 다시 할 때마다 목록에 VC 카드가
# 한 장씩 쌓였다(실측 2026-08-31 prod: 한 모델에 2장). 체인 쪽은 더 나빴다 — 라이선스는 죽었는데
# 옛 VC 는 폐기되지 않고 유효한 채 남았다.

def _seed_superseded_license(store, *, vc_id="vc:dev:old", user_id="user-1"):
    """같은 모델에 딸린, 재검증 대기 상태의 옛 라이선스 한 건."""
    old = dict(store["licenses"][0]) if store["licenses"] else {}
    old.update(
        {
            "id": "11111111-1111-4111-8111-111111111111",
            "model_id": MODEL_ID,
            "enrollment_id": "22222222-2222-4222-8222-222222222222",
            "status": "reverification_required",
            "vc_id": vc_id,
        }
    )
    store["licenses"].append(old)
    return old["id"]


def test_new_license_revokes_the_superseded_one_and_queues_its_vc(
    biometric_fm, make_token, holder_stub
):
    client, store, _ = biometric_fm
    old_id = _seed_superseded_license(store)
    enrollment_id = _seed_license_pending_enrollment(store)

    response = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )

    assert response.status_code == 201, response.text
    new_card = response.json()
    assert new_card["status"] == "active"

    old = next(row for row in store["licenses"] if row["id"] == old_id)
    assert old["status"] == "revoked", "재등록으로 갈아탄 라이선스는 죽은 채로 남으면 안 된다"
    # 체인에 남은 옛 VC 도 같이 폐기 큐에 들어가야 한다(라이선스만 죽이면 VC 는 유효한 채 남는다).
    queued = set(store["revocations"])
    assert "vc:dev:old" in queued, queued
    assert new_card["vcId"] not in queued, "방금 발급한 VC 를 폐기하면 안 된다"


def test_license_list_hides_revoked_cards(biometric_fm, make_token, holder_stub):
    client, store, _ = biometric_fm
    _seed_superseded_license(store)
    enrollment_id = _seed_license_pending_enrollment(store)
    created = client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=_auth(make_token),
    )
    assert created.status_code == 201, created.text

    listed = client.get("/v1/facemarket/licenses", headers=_auth(make_token))

    assert listed.status_code == 200, listed.text
    ids = [card["id"] for card in listed.json()]
    assert created.json()["id"] in ids
    assert "11111111-1111-4111-8111-111111111111" not in ids, \
        "폐기된 라이선스는 카드 목록에 남지 않는다"
