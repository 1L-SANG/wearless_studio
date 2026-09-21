"""컷당 단일 아이덴티티-소스 선택 + 실존 모델 자산 조회 (codex 리뷰 [P1]).

detail_page/editor 워커가 컷 루프 전 1회 소스를 정한다 — 컷마다 아이덴티티 소스가 정확히
하나여야 얼굴 이중주입(라이선스 단일얼굴 + 그리드 동시)이 발생하지 않는다.

  REAL      실존 모델 자산(그리드+face_front, 비공개 버킷) — 라이선스 활성일 때만
  VIRTUAL   가상모델(virtual_models.json, 공개 버킷) — 라이선스 불요
  LEGACY    모델 미선택 기존 step03 단일 얼굴 호환
  NONE      얼굴 없이 생성
  REJECTED  실존 모델 대상인데 라이선스 실패 → 조용한 폴백 금지, 얼굴 미주입

키·바이트는 여기서 다루지 않는다(private 키 미직렬화) — 상위 워커가 refs 로 r2_face 에서 로드.

assets_source_hash 무결성: fm_model_asset_job 이 자산 빌드 시점에 소스 사진(front/
angle45/side)의 지문을 fm_models.assets_source_hash 에 새긴다. resolve_real_model_assets
는 fm_biometric_enrollment_photos(현재 소스, 승인 후 불변)에서 동일 알고리즘으로 재계산해
비교한다 — 사진 바이트를 다시 읽지 않고 이미 저장된 image_digest 문자열만 사용하는 저비용
비교. 불일치(=DB 변조·데이터 유실 등 진짜 stale)면 fail-closed(None). 두 계산이 동일 함수를
쓰므로 정상 상태에서는 false-reject 가 나올 수 없다.
"""

import asyncio
import hashlib
import contextlib
import logging
import uuid

from ..facemarket_photos import (ASSET_SOURCE_SLOTS, REFSET_SLOTS,
                                 canonical_photo_slot, resolve_photo_rows)


log = logging.getLogger("wearless.identity_source")

_ANGLES = ("front", "angle45", "side")


def compute_assets_source_hash(faces: list[dict]) -> str:
    """fm_model_asset_job._source_hash 와 동일 알고리즘(단일 진실 원천).

    faces 는 front/angle45/side 순으로 정렬된 리스트, 각 항목은 image_digest
    (우선) 또는 r2_key 를 갖는다. 두 계산부가 다른 구현을 쓰면 false-reject
    위험이 생기므로 이 함수 하나만 공유한다.
    """
    return hashlib.sha256(
        "|".join(
            str(face.get("image_digest") or face.get("r2_key") or "")
            for face in faces
        ).encode()
    ).hexdigest()


def select_source(*, selected_model_id, license_row, has_real_assets: bool,
                  has_license_face: bool) -> str:
    """컷 루프 전 1회 호출. 반환: REAL|VIRTUAL|LEGACY|NONE|REJECTED."""
    try:
        uuid.UUID(str(selected_model_id))
    except (TypeError, ValueError):
        is_real = False
    else:
        is_real = True
    if is_real:
        if (
            has_real_assets
            and license_row
            and str(license_row.get("model_id")) == str(selected_model_id)
            and license_row.get("status") == "active"
            and license_row.get("model_status") == "verified"
        ):
            return "REAL"
        return "REJECTED"
    if selected_model_id:
        return "VIRTUAL"
    if has_license_face:
        return "LEGACY"
    return "NONE"


async def resolve_real_model_assets(
    conn,
    model_id: str,
    *,
    enrollment_id: str,
    evidence_version: str,
) -> list[dict] | None:
    """Return the exact two private refs pinned to current passed evidence."""
    try:
        uuid.UUID(str(model_id))
    except (TypeError, ValueError):
        return None
    async with conn.cursor() as cur:
        await cur.execute(
            "select m.status as model_status, m.assets_status, "
            "m.current_enrollment_id::text as current_enrollment_id, "
            "m.assets_source_hash, "
            "e.status as enrollment_status, e.match_policy_version, "
            "a.view, a.r2_key, a.mime, a.bucket, "
            "a.source_enrollment_id::text as source_enrollment_id, a.evidence_version "
            "from fm_models m "
            "left join fm_biometric_enrollments e "
            "on e.id = m.current_enrollment_id and e.model_id = m.id "
            "left join fm_model_assets a on a.model_id = m.id "
            "where m.id = %s",
            (model_id,))
        rows = await cur.fetchall()
    if not rows:
        return None
    state = rows[0]
    current_enrollment_id = str(state.get("current_enrollment_id") or "")
    policy_version = str(state.get("match_policy_version") or "").strip()
    if (
        state.get("model_status") != "verified"
        or state.get("assets_status") != "ready"
        or state.get("enrollment_status") != "passed"
        or current_enrollment_id != str(enrollment_id)
        or not policy_version
        or policy_version != str(evidence_version)
    ):
        return None
    # assets_source_hash 무결성: 사진 바이트가 아니라 이미 저장된 image_digest 문자열만
    # 읽는 저비용 재계산 — 승인된 사진 행은 이 시점 이후 절대 갱신되지 않으므로(업로드/삭제
    # 는 photos_pending/liveness_pending 단계에서만 허용) 정상 상태에선 항상 일치한다.
    async with conn.cursor() as cur:
        await cur.execute(
            "select angle, image_digest, r2_key "
            "from fm_biometric_enrollment_photos "
            "where enrollment_id = %s",
            (enrollment_id,))
        photo_rows = await cur.fetchall()
    sources = resolve_photo_rows(photo_rows, ASSET_SOURCE_SLOTS)
    if len(sources) != len(ASSET_SOURCE_SLOTS):
        return None
    current_source_hash = compute_assets_source_hash(sources)
    if current_source_hash != str(state.get("assets_source_hash") or ""):
        # 관측 로그(PII 없음 — 사유 코드·model_id·enrollment_id 만, 해시/다이제스트
        # 값 자체는 남기지 않는다). 다른 REJECTED 사유와 같은 bare None 만 반환하면
        # 운영에서 진짜 데이터 이상(변조·유실)과 흔한 REJECTED 를 구분할 수 없다.
        log.warning(
            "assets_source_hash_mismatch",
            extra={
                "reason": "assets_source_hash_mismatch",
                "model_id": str(model_id),
                "enrollment_id": str(enrollment_id),
            },
        )
        return None
    by_view = {r["view"]: r for r in rows if r.get("view")}

    def pinned(view: str):
        """같은 등록·같은 정책 버전에 핀된 비공개 자산 행만 통과시킨다. 어긋나면 None."""
        r = by_view.get(view)
        if (
            not r
            or not str(r.get("r2_key") or "").strip()
            or r.get("bucket") != "face"
            or not str(r.get("mime") or "").startswith("image/")
            or str(r.get("source_enrollment_id") or "") != str(enrollment_id)
            or r.get("evidence_version") != evidence_version
            or r.get("evidence_version") != policy_version
        ):
            return None
        return {"key": r["r2_key"], "mime": r["mime"], "bucket": r["bucket"]}

    out = []
    # 얼굴 두 장은 필수 — 하나라도 핀이 어긋나면 자산 전체를 거부한다(fail-closed 유지).
    for view in ("face_front", "grid_sedcard"):
        ref = pinned(view)
        if ref is None:
            return None
        out.append(ref)
    # 전신은 **선택**이다. 있으면 세 번째로 붙이고(매니페스트 MODEL FULL BODY 자리), 없으면 기존 2장 그대로.
    # 있는데 핀이 어긋나면 그 자산만 빼는 게 아니라 전체를 거부한다 — 반쪽 근거로 컷을 만들지 않는다.
    if "body_front" in by_view:
        ref = pinned("body_front")
        if ref is None:
            return None
        out.append(ref)
    return out


#: 옆·뒷모습 컷의 머리 교체에 쓸 등록 사진 칸. 얼굴 자산(fm_model_assets)이 아니라 등록 사진
#: (fm_biometric_enrollment_photos) 에서 바로 읽는다 — 이 각도는 얼굴 그리드에 안 들어간다.
#: sh_side 는 "코가 화면 왼쪽", sh_side_right 는 그 반대다(facemarket_photos 주석).
#:
#: 값 = 그 방향에 쓸 수 있는 칸 이름들(앞이 우선). 왼쪽만 후보가 둘인 이유는 sh_side 가 자산
#: 소스이기도 해서다 — v3 이전 등록은 sh_side 에 행을 넣는 순간 assets_source_hash 가 어긋나
#: 실사 컷이 통째로 막힌다(facemarket_photos.ANGLE_ALT_SLOTS 주석). 그런 등록은 보조 칸
#: sh_side_left 에 넣고, v3 이후 등록은 sh_side 로 그대로 들어온다. 두 칸 모두 정의상
#: **코가 화면 왼쪽인 90도 옆모습**이라 섞여도 같은 사진이다.
ANGLE_PHOTO_CANDIDATES: dict[str, tuple[str, ...]] = {
    "sh_side": ("sh_side_left", "sh_side"),
    "sh_side_right": ("sh_side_right",),
    "sh_back": ("sh_back",),
}
ANGLE_PHOTO_SLOTS = tuple(ANGLE_PHOTO_CANDIDATES)


async def resolve_angle_photos(conn, enrollment_id: str) -> dict[str, str]:
    """등록 사진에서 옆·뒤 칸의 R2 키를 찾는다 — {칸 이름: r2_key}. 없는 칸은 빠진다.

    얼굴 패스와 달리 여기서는 **한 장이라도 있으면 그 각도는 만들 수 있다.** 그래서 전부 있어야
    한다는 fail-closed 규칙을 걸지 않는다(칸이 빈 각도의 컷만 실패로 끝난다).
    """
    async with conn.cursor() as cur:
        await cur.execute(ANGLE_PHOTO_SQL, (enrollment_id,))
        rows = await cur.fetchall()
    return angle_photos_from_rows(rows)


#: 등록 사진 행 조회. 커서를 이미 들고 있는 호출자(워밍 핑)가 같은 질의를 쓰도록 빼 둔다 —
#: 연결 안에서 커서를 겹쳐 열지 않게 한다.
ANGLE_PHOTO_SQL = (
    "select angle, r2_key from fm_biometric_enrollment_photos where enrollment_id = %s")


def angle_photos_from_rows(rows) -> dict[str, str]:
    """등록 사진 행 → {방향 칸 이름: r2_key}. 없는 방향은 빠진다.

    받는 이름은 ANGLE_PHOTO_CANDIDATES 에 적힌 것뿐이고, **옛 이름은 안 받는다.**

    ★ 다른 자리(자산 소스·기준 얼굴)는 SLOT_CANDIDATES 로 옛 이름을 풀어 준다. 여기서는
      그러면 안 된다: `sh_side` 의 옛 별칭은 `face05`(얼굴 중심 측면 컷)인데, 각도 교체가
      요구하는 건 **코가 화면 왼쪽인 90도 옆모습**이다. 다른 사진이다.

      별칭을 풀면 옛 등록자의 왼쪽 옆 컷이 **빈 컷이 아니라 엉뚱한 머리로 채워진다** —
      실패보다 나쁘다. 남의 머리를 내보내지 않는다는 이 경로의 계약과도 어긋난다
      (2026-09-21: 실제로 face05 가 sh_side 로 풀리고 있었다).

      왼쪽만 후보가 둘인 것(sh_side_left · sh_side)은 별칭 해소가 아니다 — 둘 다 정의상
      같은 사진이고, 옛 등록이 sh_side 를 못 쓰는 사정 때문에 갈린 것뿐이다.
    """
    by_slot = {}
    for row in rows:
        slot = str(row.get("angle") or "")
        key = str(row.get("r2_key") or "").strip()
        if slot and key:
            by_slot[slot] = key
    out: dict[str, str] = {}
    for direction, candidates in ANGLE_PHOTO_CANDIDATES.items():
        key = next((by_slot[name] for name in candidates if name in by_slot), None)
        if key:
            out[direction] = key
    return out


#: 동일인 검사 기준으로 쓸 등록 얼굴 사진 슬롯 — **정면 계열만**, 그리고 **한 조명 한 자리**.
#: 16칸 스펙(2026-09-14)의 기준 3장이 그대로 이 자리다: 그늘에서 정면 무표정 2 · 시선만 왼쪽 ·
#: 시선만 오른쪽. 조명이 섞이면 기준끼리의 점수부터 무너진다(v6_refset_check 규칙: 중앙값 0.80 ·
#: 최저쌍 0.70). 턱을 내린 컷은 **일부러 뺐다** — 같은 사람인데 시선 컷과 0.664 로 최저선을 깬다
#: (2026-09-11 v7 인테이크 실측: 3장이면 평균 0.841·최저 0.788 로 통과).
#:
#: 왜 정면만인가(2026-09-13 실측, prod 테스트컷 8장 · YuNet+SFace):
#:     기준                         테스트컷 점수
#:     옛 등록 front 1장            0.51 ~ 0.60
#:     옛 등록 angle45 1장          0.34 ~ 0.43      ← 45도가 통째로 끌어내린다
#:     front+angle45 중앙값         0.435 ~ 0.514    ← 0.45 에서 2장 탈락·1장 턱걸이
#:     9/11 정면 8장 중앙           0.72 ~ 0.79
#:     9/11 정면+3/4 12장 중앙      0.69 ~ 0.73
#:     9/11 3/4 4장 중앙            0.58 ~ 0.66
#: SFace 임베딩이 정면 기준이라 각도 사진은 같은 사람이라도 점수가 내려간다. 기준에 섞으면
#: 중앙값이 내려가 **같은 사람을 떨어뜨린다** — 문턱을 내리는 것보다 기준을 맞추는 게 먼저다.
IDENTITY_REFERENCE_SLOTS: tuple[str, ...] = REFSET_SLOTS
#: 16칸 이전 등록(18칸 · 옛 3장)의 기준. face01 은 SLOT_CANDIDATES 를 타고 sh_front → face01 →
#: front 순으로 내려가므로 옛 3장 등록도 front 한 장이 잡힌다(angle45·side 는 목록에 없다).
LEGACY_IDENTITY_REFERENCE_SLOTS: tuple[str, ...] = ("face01", "face02", "face07", "face08")
#: 앞 단계가 0장이면 다음 단계로 간다. 둘 다 0장이면 호출자가 승인 face_front 한 장으로 폴백한다.
IDENTITY_REFERENCE_SLOT_TIERS: tuple[tuple[str, ...], ...] = (
    IDENTITY_REFERENCE_SLOTS, LEGACY_IDENTITY_REFERENCE_SLOTS)
#: 기준 사진으로 쓸 수 있는 저장 상태. 승인 전(quarantine)도 기준으로는 유효하다 — 게이트는
#: "같은 사람인가"만 보고, 공개 자산 승인과는 다른 판단이다.
_REFERENCE_STORAGE_STATES = ("quarantine", "approved")


async def enrollment_reference_faces(app, conn, model_id: str, *, limit: int = 8,
                                     model_dir: str | None = None) -> list[bytes]:
    """동일인 검사 기준셋 = 등록 **정면** 얼굴 사진 여러 장(IDENTITY_REFERENCE_SLOTS).

    왜 여러 장인가(2026-09-13 실측): 기준을 승인 자산 face_front **한 장**으로 쓰면 그 한 장의
    촬영 조건이 곧 기준이 된다. 같은 사람인데도
      · 등록 face_front(9/2) vs 실제 사진(9/11) 중앙 0.586~0.665
      · 테스트컷 vs face_front 0.51~0.60 인데 vs 9/11 기준셋은 0.70~0.77
    로 갈렸다 — 얼굴이 아니라 **조명·카메라가 다른 날** 이 점수를 갈랐다. 여러 장의 중앙값을 쓰면
    한 장의 촬영 조건이 문턱을 대신 정하는 일이 없어진다(identity_score 가 중앙값을 쓴다).

    슬롯 이름만 믿지 않는다 — 정면 슬롯에 각도 사진이 올라온 촬영 실수를 대비해 사진마다 YuNet
    yaw_proxy 를 재서 YAW_FRONT_MAX 이상이면 뺀다.

    단계는 둘이다(IDENTITY_REFERENCE_SLOT_TIERS): 16칸 기준 3장 → 0장이면 옛 슬롯(face01·02·
    07·08, 옛 3장 등록은 front 한 장). 그마저 0장이면 [] — 호출자가 기존 face_front 한 장 경로로
    폴백한다. 기준이 아예 없으면 게이트가 신원을 보지 않는다(identity=None). 얼굴 패스 자체는
    어떤 경우에도 막지 않는다.
    """
    r2_face = getattr(app.state, "r2_face", None)
    if r2_face is None:
        return []
    try:
        uuid.UUID(str(model_id))
    except (TypeError, ValueError):
        return []
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                "select p.angle, p.r2_key, p.qc_status, p.storage_state "
                "from fm_biometric_enrollment_photos p "
                "join fm_models m on m.current_enrollment_id = p.enrollment_id "
                "where m.id = %s", (model_id,))
            rows = await cur.fetchall()
    except Exception as exc:  # noqa: BLE001 — 기준을 못 읽으면 폴백한다(컷을 막지 않는다)
        log.warning("enrollment reference photos lookup failed for %s: %r", model_id, exc)
        return []
    for slots in IDENTITY_REFERENCE_SLOT_TIERS:
        out = await _reference_tier(rows, slots, r2_face, model_id,
                                    limit=limit, model_dir=model_dir)
        if out:
            return out
    return []


async def _reference_tier(rows, slots, r2_face, model_id: str, *, limit: int,
                          model_dir: str | None) -> list[bytes]:
    """한 단계의 기준 슬롯으로 실제 바이트를 모은다. 한 장도 못 모으면 [] — 호출자가 다음 단계로."""
    usable = [r for r in resolve_photo_rows(rows, slots)
              if r.get("qc_status") == "passed"
              and r.get("storage_state") in _REFERENCE_STORAGE_STATES
              and str(r.get("r2_key") or "").strip()]
    if not usable:
        return []
    out: list[bytes] = []
    turned = 0
    for row in usable[:limit]:
        try:
            data = await asyncio.to_thread(r2_face.get_bytes, row["r2_key"])
        except Exception as exc:  # noqa: BLE001 — 한 장이 없어도 나머지로 간다
            log.info("reference photo unavailable (%s) — skipped", type(exc).__name__)
            continue
        if not await asyncio.to_thread(_is_frontal, data, model_dir):
            turned += 1
            continue
        out.append(data)
    log.info("identity reference photos for %s (%s): %d/%d usable (yaw 로 뺀 것 %d)",
             model_id, slots[0], len(out), len(usable), turned)
    return out


def _is_frontal(data: bytes, model_dir: str | None) -> bool:
    """기준으로 쓸 만큼 정면인가. 못 재면 **쓴다**(기준을 잃는 쪽이 더 나쁘다)."""
    from . import face_identity  # 지연 임포트 — 이 모듈은 cv2 없이도 임포트될 수 있어야 한다

    try:
        image = face_identity._decode(data)
        det = face_identity.detect_face(image, model_dir)
    except Exception as exc:  # noqa: BLE001 — 판정 불가는 통과로 본다
        log.info("reference yaw 측정 실패(%s) — 그대로 쓴다", type(exc).__name__)
        return True
    if det is None:
        return True
    return det.yaw_proxy < face_identity.YAW_FRONT_MAX


async def reference_face_bytes(app, conn, model_id: str, license_row, *,
                               model_dir: str | None = None) -> list[bytes]:
    """동일인 검사 기준. 등록 얼굴 사진 여러 장이 우선이고, 없으면 승인된 face_front 한 장.

    변형 컷처럼 실존 자산을 따로 안 읽는 경로가 쓴다. 새 컷·상세페이지는 이미 읽은 model_images[0] 를 그대로 쓴다.
    """
    photos = await enrollment_reference_faces(app, conn, model_id, model_dir=model_dir)
    if photos:
        return photos
    if not isinstance(license_row, dict):
        return []
    enrollment_id = license_row.get("current_enrollment_id")
    evidence_version = license_row.get("match_policy_version")
    r2_face = getattr(app.state, "r2_face", None)
    if not enrollment_id or not evidence_version or r2_face is None:
        return []
    refs = await resolve_real_model_assets(
        conn, str(model_id), enrollment_id=str(enrollment_id), evidence_version=str(evidence_version))
    if not refs:
        return []
    try:
        return [await asyncio.to_thread(r2_face.get_bytes, refs[0]["key"])]
    except Exception as exc:  # noqa: BLE001 — 기준을 못 읽으면 신원 검사 없이 간다(얼굴 패스 자체는 막지 않는다)
        log.warning("reference face unavailable for %s: %r", model_id, exc)
        return []


#: 켜진 LoRA 한 행. 피부 보정(fm_models.skin_finish_code)을 같이 읽는다 — 얼굴 패스가 그 값으로
#: 네거티브 문구와 크롭 확대 blend 를 정한다.
_LORA_COLUMNS = (
    "l.id::text as id, l.version, l.lora_r2_key, l.lora_sha256, l.bucket, l.trigger_token, "
    "l.hair_length, l.hair_color, l.hair_texture, l.face_shape, l.jaw_line, l.trained_steps"
)
_LORA_WHERE = " from fm_model_loras l where l.model_id = %s and l.enabled and l.status = 'ready' limit 1"


async def _lora_row(conn, model_id: str) -> dict | None:
    """행 조회. skin_finish_code 컬럼이 아직 없는 DB(마이그 미적용)에서도 옛 모양으로 한 번 더 시도한다.

    ★ 여기서 그냥 None 을 돌려주면 **얼굴 패스가 통째로 꺼진다** — 실존 모델 컷은 그 순간
      남의 얼굴이 나가거나(옛 계약) 컷이 실패한다. 컬럼 하나 때문에 그렇게 되면 안 된다.
    """
    select_with = ("select " + _LORA_COLUMNS
                   + ", coalesce((select m.skin_finish_code from fm_models m where m.id = l.model_id),"
                     " 'prod') as skin_finish" + _LORA_WHERE)
    for sql, labelled in ((select_with, True), ("select " + _LORA_COLUMNS + _LORA_WHERE, False)):
        try:
            async with conn.cursor() as cur:
                await cur.execute(sql, (model_id,))
                return await cur.fetchone()
        except Exception as exc:  # noqa: BLE001 — 테이블·컬럼 부재, 권한 등
            if labelled:
                # 실패한 트랜잭션은 그대로 두면 다음 질의가 InFailedSqlTransaction 으로 죽는다.
                with contextlib.suppress(Exception):
                    await conn.rollback()
                log.info("fm_models.skin_finish_code unavailable (%s) — 보정 없이 읽는다",
                         type(exc).__name__)
                continue
            log.warning("fm_model_loras lookup failed for %s: %r", model_id, exc)
    return None


async def resolve_enabled_lora(conn, model_id: str) -> dict | None:
    """이 모델의 **켜진** LoRA 행 하나. 없으면 None(얼굴 패스 없이 진행).

    fm_model_loras 는 partial unique(model_id) where enabled 라 최대 한 행이다.
    hair_*/face_shape/jaw_line 은 등록자의 현재 모습이 아니라 이 LoRA 가 학습한 모습이다
    (마이그레이션 20260910100000 주석 참조) — 프롬프트 블록도 이 값으로 만든다.
    테이블이 아직 없는 환경(마이그 미적용)에서도 죽지 않는다 — None 을 돌려주고 기존 동작을 유지한다.
    """
    try:
        uuid.UUID(str(model_id))
    except (TypeError, ValueError):
        return None
    row = await _lora_row(conn, model_id)
    if row is None:
        return None
    if not row or row.get("bucket") != "face" or not str(row.get("lora_r2_key") or "").strip():
        return None
    row = dict(row)
    row["face_backend_url"] = await _active_face_backend_url(conn, model_id)
    return row


async def active_face_backend_url(pool, model_id: str | None = None) -> str | None:
    """지금 등록된 렌더 파드의 URL. 워커가 **대기 중에도 계속** 다시 묻는 자리다.

    파드는 재고 때문에 바뀌고(id 가 바뀌면 URL 도 바뀐다), 처음에는 아예 없을 수도 있다
    (자동 켜기로 그 컷을 위해 만들어지는 중). 잡 시작 때 한 번 읽은 값을 붙들면 그 컷은
    영영 죽은 주소를 보거나, 파드가 생기기도 전에 폴백한다.
    """
    try:
        async with pool.connection() as conn:
            return await _active_face_backend_url(conn, model_id)
    except Exception as exc:  # noqa: BLE001 — 못 읽으면 "아직 없음"으로 본다
        log.warning("face render pod lookup failed: %r", exc)
        return None


async def _active_face_backend_url(conn, model_id: str | None = None) -> str | None:
    """이 모델의 렌더 파드 URL. 없으면 None → 설정값 폴백.

    **파드 하나 = LoRA 하나**(2026-09-14). 모델을 주면 그 모델의 파드를 고르고, 없으면
    모델 미지정 행(구 단일 파드)으로 떨어진다 — 모델별 파드를 세우기 전까지의 다리다.
    남의 모델 파드로는 절대 안 보낸다: 그 파드는 다른 LoRA 를 물고 있어 409 만 돌려준다.
    """
    from ..services.face_autoscale import pod_backend_url

    try:
        async with conn.cursor() as cur:
            await cur.execute("select to_regclass('public.fm_face_render_pod') as t")
            if not (await cur.fetchone() or {}).get("t"):
                return None
            await cur.execute(
                "select pod_id, model_id from fm_face_render_pod where retired_at is null "
                "and (model_id = %s or model_id is null) "
                "order by (model_id is null), created_at desc limit 1",
                (model_id,))
            row = await cur.fetchone()
    except Exception as exc:  # noqa: BLE001 — 못 읽으면 설정값으로 간다
        log.warning("face render pod lookup failed: %r", exc)
        return None
    return pod_backend_url((row or {}).get("pod_id"))


async def active_angle_pod_id(pool) -> str | None:
    """지금 등록된 각도 교체(ComfyUI) 파드 id. 없으면 None → 설정값 폴백.

    얼굴 파드와 달리 **모델을 가리지 않는다** — 각도 교체는 등록자별 LoRA 를 물지 않아
    (BFS Head 는 사람과 무관한 일반 LoRA) 어느 잡이든 같은 파드를 쓴다.
    """
    try:
        async with pool.connection() as conn, conn.cursor() as cur:
            await cur.execute("select to_regclass('public.fm_angle_render_pod') as t")
            if not (await cur.fetchone() or {}).get("t"):
                return None
            await cur.execute(
                "select pod_id from fm_angle_render_pod where retired_at is null "
                "order by created_at desc limit 1")
            row = await cur.fetchone()
    except Exception as exc:  # noqa: BLE001 — 못 읽으면 설정값으로 간다
        log.warning("angle pod lookup failed: %r", exc)
        return None
    return str((row or {}).get("pod_id") or "").strip() or None


def profiles_from_lora_row(row: dict | None) -> tuple[dict | None, dict | None]:
    """LoRA 행 → (hair_profile, face_shape_profile). 값이 없으면 None 을 돌려 프롬프트를 그대로 둔다."""
    if not row:
        return None, None
    hair = {k: row.get(c) for k, c in (("hairLength", "hair_length"), ("hairColor", "hair_color"),
                                       ("hairTexture", "hair_texture")) if row.get(c)}
    face = {k: row.get(c) for k, c in (("faceShape", "face_shape"), ("jawLine", "jaw_line")) if row.get(c)}
    return (hair or None), (face or None)
