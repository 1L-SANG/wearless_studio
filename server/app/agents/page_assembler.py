"""M-02 page-assembler — 상세페이지 조립 (결정적 템플릿 엔진, 비-AI).

구현 기준(ai_agent_modules.md §M-02): mock의 `buildEditorBlocksFromStoryboard`
(`src/mock/db.js`)가 이 모듈의 자리 — 같은 결정적 로직을 서버로 포팅한다.
AI 호출 없음. id는 uid()(랜덤) 대신 **인덱스 기반 결정적 id**로 대체
(`b{i}` / `b{i}e{j}`) — 같은 입력이면 항상 같은 출력(계약 §1-4).

입력: storyboard(StoryboardBlock[]) + cut_results(AG-06 산출 {blockId,imageUrl})
     + copy_results(AG-02/03 산출 {blockId,texts:[{role,text}]}) + product + copywriting.
출력: EditorBlock[] (계약 §3.5) — storyboard 순서를 따르는 블록들 + 자동 블록 3종(size/care/ai-notice).

컷 생성 실패(해당 블록의 cut_result 없음)는 크래시가 아니라 빈 슬롯(src=None) 렌더.
"""

from .content_roles import CONTENT_ROLE_NAMES, resolve_content_role, resolve_section_role


def _block_id(i: int) -> str:
    return f"b{i}"


def _el_id(i: int, j: int) -> str:
    return f"b{i}e{j}"


def _text_el(block_i: int, el_j: int, x, y, w, h, text: str, style: dict | None = None,
             source_block_id=None, copy_role=None) -> dict:
    """mock T(x,y,w,h,text,style) 포팅 — id만 결정적으로 대체.
    sourceBlockId+copyRole = 셀러가 대기 화면에서 고친 카피를 로드 시점에
    되살리는 매칭 키(editor_wait_dev_spec §4 — 셀러 편집이 항상 이긴다)."""
    el = {
        "id": _el_id(block_i, el_j),
        "type": "text",
        "x": x, "y": y, "w": w, "h": h,
        "text": text,
        "style": style or {},
    }
    if source_block_id:
        el["sourceBlockId"] = source_block_id
    if copy_role:
        el["copyRole"] = copy_role
    return el


def _image_el(block_i: int, el_j: int, x, y, w, h, src, radius=None, cut_type=None,
              source_block_id=None) -> dict:
    """mock IMG(x,y,w,h,src,radius,cutType) 포팅 — id만 결정적으로 대체.
    src=None 은 계약 §3.5 Element(image) 의 '빈 슬롯' 표현.
    sourceBlockId = 콘티 블록 id (editor_wait_dev_spec §2-3 — 대기 화면 컷 채움과
    셀러 카피 오버라이드의 매칭 키. 추가 필드라 기존 저장 계약과 호환)."""
    el = {
        "id": _el_id(block_i, el_j),
        "type": "image",
        "x": x, "y": y, "w": w, "h": h,
        "src": src,
        "radius": radius if radius is not None else 8,
    }
    if source_block_id:
        el["sourceBlockId"] = source_block_id
    if cut_type:
        el["cutType"] = cut_type
    return el


# AI 컷 요소 박스 — x·y·폭은 고정, **높이는 소스 이미지 비율에서 유도**한다.
# 하드코딩(구 880×560)은 2:3 세로컷을 가로 박스에 넣어 object-fit:cover 로 높이를 잘라먹었다
# (에디터·미리보기·다운로드가 같은 지오메트리를 쓰므로 산출물까지 잘림). 생성 종횡비 설정이
# 바뀌어도 레이아웃이 따라오도록 비율은 계산으로만 얻는다.
_IMG_X, _IMG_Y = 60, 50
_IMG_W = 880
_IMG_MARGIN_B = 50          # 이미지 하단 ~ 블록 바닥 여백
_BODY_INSET_B = 50          # body 카피를 이미지 하단 근처에 두는 오프셋(구 레이아웃 관계 보존)
_FALLBACK_RATIO = (2, 3)    # dims 미상 시 기본 세로비(현 mannequin_aspect_ratio=2:3)
# 파손·조작된 dims(예: 10000×1, 1×10000)가 레이아웃을 무너뜨리지 않게 유도 높이를 가둔다.
# 정상 컷(2:3≈1320)은 이 범위 한가운데라 실사용에선 발화하지 않는다.
_IMG_MIN_H, _IMG_MAX_H = 200, 2400

_ROW_LAYOUTS = {
    "twoColumn": {"name": "2단 구성", "kind": "twocol"},
    "threeColumn": {"name": "3단 구성", "kind": "threecol"},
    "grid2x2": {"name": "2×2단 구성", "kind": "grid2x2"},
    "colorCompare": {"name": "컬러 비교", "kind": "colorcmp"},
}

_SWATCH_LABELS = {
    "white": "화이트", "gray": "그레이", "black": "블랙", "ivory": "아이보리",
    "beige": "베이지", "brown": "브라운", "red": "레드", "yellow": "옐로우",
    "green": "그린", "blue": "블루", "navy": "네이비", "pink": "핑크",
}


def _image_box_for_width(box_width, width, height) -> tuple[int, int]:
    """(w, h) — 지정 폭에서 소스 비율을 보존. dims 미상·이상치면 2:3 폴백 + 비례 클램프."""
    try:
        src_w, src_h = int(width), int(height)
    except (TypeError, ValueError):
        src_w = src_h = 0
    if src_w > 0 and src_h > 0:
        derived = round(box_width * src_h / src_w)
    else:
        fw, fh = _FALLBACK_RATIO
        derived = round(box_width * fh / fw)
    scale = box_width / _IMG_W
    min_height = max(1, round(_IMG_MIN_H * scale))
    max_height = max(min_height, round(_IMG_MAX_H * scale))
    return box_width, min(max(derived, min_height), max_height)


def _image_box(width, height) -> tuple[int, int]:
    """(w, h) — 기본 단일컷 폭에서 소스 비율을 보존한다."""
    return _image_box_for_width(_IMG_W, width, height)


def _block_height(elements: list[dict]) -> int:
    """모든 요소를 담는 블록 높이 — 이미지뿐 아니라 카피까지(짧은 이미지에서 헤드라인이
    블록 밖으로 잘리던 문제 방지)."""
    bottom = max((int(e.get("y") or 0) + int(e.get("h") or 0)
                  for e in elements if isinstance(e, dict)), default=0)
    return bottom + _IMG_MARGIN_B


def _cut_meta_by_block(cut_results: list[dict] | None) -> dict:
    """blockId → {imageUrl, width, height}. width/height 는 없을 수 있다(구 데이터·실패)."""
    out: dict[str, dict] = {}
    for r in cut_results or []:
        if isinstance(r, dict) and r.get("blockId"):
            out[r["blockId"]] = {
                "imageUrl": r.get("imageUrl"),
                "width": r.get("width"),
                "height": r.get("height"),
            }
    return out


def _copy_texts_by_block(copy_results: list[dict] | None) -> dict:
    out: dict[str, list[dict]] = {}
    for r in copy_results or []:
        if isinstance(r, dict) and r.get("blockId"):
            out[r["blockId"]] = r.get("texts") or []
    return out


def _text_for_role(texts: list[dict], role: str) -> str | None:
    for t in texts:
        if isinstance(t, dict) and t.get("role") == role and t.get("text"):
            return t["text"]
    return None


def _colorway_pair(first: dict, second: dict) -> bool:
    """확장형 기본 시드가 명시한 추가 색상 풀샷+미디움샷 한 묶음인지 확인한다.

    사용자가 한쪽의 색상·매칭 의류·레이아웃을 바꾸거나 행을 풀면 자동 조립하지 않는다.
    """
    if not isinstance(first, dict) or not isinstance(second, dict):
        return False
    group_id = first.get("colorwayGroupId")
    row_id = first.get("layoutRowId")
    return bool(
        group_id
        and first.get("colorwayPairVersion") == 1
        and second.get("colorwayPairVersion") == 1
        and second.get("colorwayGroupId") == group_id
        and row_id
        and second.get("layoutRowId") == row_id
        and first.get("sectionLayout") == second.get("sectionLayout") == "twoColumn"
        and first.get("source") != "mine"
        and second.get("source") != "mine"
        and first.get("sectionRole") == second.get("sectionRole") == "studio"
        and first.get("cutType") == second.get("cutType") == "horizon"
        and first.get("direction") == second.get("direction") == "front"
        and first.get("colorId") == second.get("colorId")
        and {first.get("shot"), second.get("shot")} == {"full", "medium"}
        and (first.get("matchIds") or []) == (second.get("matchIds") or [])
        and not first.get("spaceGroupId")
        and not second.get("spaceGroupId")
    )


def _display_text(value, fallback="") -> str:
    text = str(value or "").strip()
    return text[:120] or fallback


def _colorway_labels(product: dict, pair: list[dict]) -> tuple[str, str | None]:
    color_id = str(pair[0].get("colorId"))
    color = next(
        (item for item in (product.get("colors") or [])
         if item.get("id") is not None and str(item.get("id")) == color_id),
        {},
    )
    product_name = _display_text(product.get("name"), "상품")
    color_name = _display_text(
        color.get("name") or color.get("label") or _SWATCH_LABELS.get(str(color.get("swatchId") or "")),
        "색상",
    )
    product_label = f"{product_name} [{color_name}]"

    match_ids = pair[0].get("matchIds") or []
    match_id = str(match_ids[0]) if match_ids else None
    matching = next(
        (item for item in (product.get("_matchClothing") or [])
         if match_id is not None and str(item.get("id")) == match_id),
        None,
    )
    if not matching:
        return product_label, None
    match_name = _display_text(matching.get("name"), "매칭 의류")
    match_color = _display_text(matching.get("colorName"))
    return product_label, f"{match_name} [{match_color}]" if match_color else match_name


# AI 생성 안내 문구(PRD §10.14). 기본 = AI 생성 사실 고지.
_AI_NOTICE_DEFAULT = (
    "본 상세페이지의 일부 이미지는 AI를 활용해 생성되었습니다. "
    "실제 상품의 색상과 핏은 촬영 환경 및 화면 설정에 따라 다르게 보일 수 있습니다."
)
# 라이선스 실제 모델 사용 시 — 26.06 가상인물 표기 의무는 **가상인물에만** 적용된다.
# 이 컷의 얼굴은 본인확인(CX)을 거친 실제 모델이 라이선스 계약으로 제공한 얼굴이라
# '가상인물' 표기가 오히려 사실과 다르다. AI 로 생성한 이미지라는 사실 고지는 유지한다.
# 모델명은 **마스킹된 display_name**만 — 상세페이지는 무인증 공개면이라 공개 검증(QR)의
# 하드룰(facemarket.py §공개 검증 ③ 신원은 파생·마스킹 값만)과 같은 기준을 적용한다.
#
# ⚠️ 문구가 **범위를 넘어 주장하면 안 된다**. 얼굴 레퍼런스는 얼굴이 식별되는 컷에만 붙는다
# (cut_generator._face_fits — 거울샷·뒷모습·하반신 컷은 제외). 그 제외 컷에도 인물은 렌더되지만
# 그 인물은 **AI 가 지어낸 가상인물**이라 26.06 표기 의무 대상이다. 따라서:
#   · 일부 컷만 라이선스 얼굴 → "일부 컷"으로 한정하고 나머지가 가상인물임을 함께 고지
#   · 전 컷이 라이선스 얼굴 → 그때만 "가상인물 아님" 을 붙일 수 있다
# 페이지 전체를 '가상인물 아님' 으로 뒤집으면 표기 의무 대상 컷에 반대 표기가 붙는다(허위표시).
_AI_NOTICE_LICENSED_ALL = (
    "본 상세페이지의 인물 이미지는 검증된 실제 모델 {model_name} 님의 얼굴을 "
    "라이선스 계약에 따라 사용해 AI로 생성했습니다(가상인물 아님). "
    "라이선스 진위는 /verify/{license_id} 에서 확인할 수 있습니다. "
    "실제 상품의 색상과 핏은 촬영 환경 및 화면 설정에 따라 다르게 보일 수 있습니다."
)
_AI_NOTICE_LICENSED_PARTIAL = (
    "본 상세페이지의 이미지는 AI로 생성했습니다. 얼굴이 드러나는 컷은 검증된 실제 모델 "
    "{model_name} 님의 얼굴을 라이선스 계약에 따라 사용했으며(가상인물 아님), "
    "얼굴이 드러나지 않는 컷의 인물은 AI가 생성했습니다. "
    "라이선스 진위는 /verify/{license_id} 에서 확인할 수 있습니다. "
    "실제 상품의 색상과 핏은 촬영 환경 및 화면 설정에 따라 다르게 보일 수 있습니다."
)


def build_auto_blocks(product: dict, start_index: int = 0, *,
                      license_notice: dict | None = None) -> list[dict]:
    """mock buildAutoBlocks(product) 포팅 (PRD §10.14) — 사이즈/세탁/AI 생성 안내.
    사이즈 안내는 product.measurements 를 조립 시점에 읽는다.

    license_notice={'modelName','licenseId','faceCuts','totalCuts'} 면 AI 생성 안내를
    '검증된 실제 모델 라이선스' 문구로 바꾼다. **얼굴이 실제로 담긴 컷이 하나라도 성공했을 때만**
    워커가 채운다 — 라이선스만 잠기고 주입이 실패했는데 이 문구를 쓰면 허위 고지가 된다.

    faceCuts < totalCuts 면 '일부 컷' 문구(나머지는 가상인물임을 함께 고지). 전 컷이 라이선스
    얼굴일 때만 '가상인물 아님' 을 페이지 전체 주장으로 붙인다 — 얼굴 레퍼런스는 얼굴이
    식별되는 컷에만 붙으므로(거울샷·뒷모습·하반신 제외), 그 제외 컷의 인물은 AI 가 지어낸
    가상인물이고 26.06 표기 의무 대상이다.

    keyword-only + 기본 None = 기존 호출자(위치인자) 무변경.
    """
    product = product or {}
    measurement_labels = {
        "totalLength": "총장", "shoulderWidth": "어깨너비", "chestWidth": "가슴단면",
        "sleeveLength": "소매길이", "waistWidth": "허리단면", "hipWidth": "엉덩이단면",
        "thighWidth": "허벅지단면", "rise": "밑위", "hemWidth": "밑단단면", "armhole": "암홀",
    }

    i = start_index
    els = [
        _text_el(i, 0, 60, 56, 500, 44, "사이즈 안내",
                 {"size": 28, "weight": 600, "font": "Cal Sans", "color": "#0e0d14"}),
        _text_el(i, 1, 60, 104, 760, 24, "단위: cm · 측정 위치에 따라 1~3cm 오차가 있을 수 있어요",
                 {"size": 14, "color": "#4a4a45"}),
    ]
    j = 2
    for idx, m in enumerate((product.get("measurements") or [])[:4]):
        x = 60 + idx * 232
        key = m.get("key") if isinstance(m, dict) else None
        value = m.get("value") if isinstance(m, dict) else None
        els.append(_text_el(i, j, x, 168, 200, 24, measurement_labels.get(key, key),
                             {"size": 14, "color": "#4a4a45"}))
        j += 1
        els.append(_text_el(i, j, x, 194, 200, 48, (f"{value} cm" if value is not None else "—"),
                             {"size": 32, "weight": 600, "font": "Cal Sans", "color": "#0e0d14"}))
        j += 1
    size_block = {
        "id": _block_id(i), "name": "사이즈 안내", "kind": "size", "auto": True,
        "bg": "#ffffff", "elements": els,
    }

    i += 1
    care_block = {
        "id": _block_id(i), "name": "세탁 안내", "kind": "care", "auto": True,
        "bg": "#f5f5f5",
        "elements": [
            _text_el(i, 0, 60, 56, 500, 40, "세탁 안내",
                     {"size": 24, "weight": 600, "font": "Cal Sans", "color": "#0e0d14"}),
            _text_el(i, 1, 60, 104, 880, 64,
                     "세탁 전 실제 상품의 케어라벨을 반드시 확인해주세요. 소재와 상품 특성에 따라 관리 방법이 달라질 수 있습니다.",
                     {"size": 16, "color": "#0e0d14"}),
        ],
    }

    i += 1
    notice_text, notice_h = _AI_NOTICE_DEFAULT, 60
    license_id = ""
    if license_notice:
        face_cuts = license_notice.get("faceCuts") or 0
        total_cuts = license_notice.get("totalCuts") or 0
        license_id = license_notice.get("licenseId") or ""
        # 전 컷이 라이선스 얼굴일 때만 페이지 전체를 '가상인물 아님' 으로 주장할 수 있다.
        # 하나라도 얼굴 미첨부 컷이 있으면 그 인물은 AI 가 지어낸 가상인물 → '일부 컷' 문구.
        # total 을 모르면(0) 안전측으로 '일부' 를 쓴다 — 과대 주장이 허위표시 방향이다.
        all_licensed = total_cuts > 0 and face_cuts >= total_cuts
        template = _AI_NOTICE_LICENSED_ALL if all_licensed else _AI_NOTICE_LICENSED_PARTIAL
        notice_text = template.format(
            model_name=license_notice.get("modelName") or "익명",
            license_id=license_id,
        )
        notice_h = 80 if all_licensed else 100  # 기본 문구 경로의 높이(60)는 그대로
    ai_notice_block = {
        "id": _block_id(i), "name": "AI 생성 안내", "kind": "ai-notice", "auto": True,
        "bg": "#ffffff",
        "elements": [
            _text_el(i, 0, 60, 48, 880, notice_h, notice_text,
                     {"size": 13, "color": "#4a4a45", "align": "center"}),
        ],
    }

    # 제안서 step03 "& DID 서명 첨부" — 라이선스가 잠긴 상세페이지에만 '검증된 실제 모델'
    # 배지 + 공개 검증 QR 을 붙인다. QR 은 실제 실행 시점(프론트)에서 {origin}/verify/{id}
    # 절대 URL 로 구워진다(외부 폰 스캔). 파이썬 qrcode 미설치 — 여기선 licenseId 만 실어
    # 프론트 렌더러(license-verify)가 읽게 노출한다. QR·배지에는 licenseId(공개 검증용
    # 능력토큰)만 — 얼굴·digest·CI·생년월일은 애초에 전달 경로가 없다.
    #
    # 회귀 0: license_id 없는 경로(라이선스 미사용 일반 상세페이지)는 아래 블록을 절대
    # 만지지 않는다 → ai-notice 블록·elements·block 키가 기존과 바이트 동일.
    if license_id:
        badge_y = 48 + notice_h + 28
        qr_y = badge_y + 30 + 18
        ai_notice_block["elements"].append(
            _text_el(i, 1, 60, badge_y, 880, 30, "✅ 검증된 실제 모델",
                     {"size": 18, "weight": 700, "color": "#1a7f37", "align": "center"}))
        ai_notice_block["elements"].append({
            "id": _el_id(i, 2),
            "type": "license-verify",
            "x": 380, "y": qr_y, "w": 240, "h": 200,
            "licenseId": license_id,
        })
        # 블록 메타로도 노출 — 프론트가 QR 생성용 licenseId 를 요소 밖에서도 읽을 수 있게.
        ai_notice_block["licenseId"] = license_id

    return [size_block, care_block, ai_notice_block]


def assemble(
    storyboard: list[dict],
    cut_results: list[dict],
    copy_results: list[dict],
    product: dict,
    copywriting: bool,
    *,
    license_notice: dict | None = None,
) -> list[dict]:
    """mock buildEditorBlocksFromStoryboard(storyboard, product, copywriting) 포팅.

    콘티(storyboard) 순서를 그대로 따라 EditorBlock[] 을 배치하고, 끝에 자동 블록
    3종(size/care/ai-notice)을 붙인다. cut_results 에서 해당 블록 매치가 없으면
    (생성 실패) 빈 슬롯(src=None) 이미지 엘리먼트로 렌더 — 크래시하지 않는다.
    copywriting=True 면 contentRole에 맞는 copy_results 텍스트를 배치한다
    (mock 하드코딩 문자열 대신 실제 카피 사용). EditorBlock.kind는 상위
    sectionRole, contentRole은 사진의 구체 역할로 mock 출력 계약과 맞춘다.
    license_notice 는 AI 생성 안내 문구 분기용으로 그대로 통과시킨다(build_auto_blocks 참고).
    """
    cut_meta_by_block = _cut_meta_by_block(cut_results)
    copy_by_block = _copy_texts_by_block(copy_results)

    blocks: list[dict] = []

    def push_single(b: dict) -> None:
        block_i = len(blocks)
        bg = "#f5f5f5" if block_i % 2 else "#ffffff"
        if b.get("source") == "mine":
            section_role = resolve_section_role(b) or "styling"
            own_images = (b.get("ownImages") or [])[:1]
            els = [
                _image_el(block_i, j, 60, 50, 880, 560, src, 12)
                for j, src in enumerate(own_images)
            ]
            blocks.append({
                "id": _block_id(block_i), "name": "내 이미지", "kind": section_role,
                "contentRole": "custom",
                "bg": bg, "h": 660, "elements": els,
            })
            return

        content_role = resolve_content_role(b)
        section_role = resolve_section_role(b, content_role) or "styling"
        name = CONTENT_ROLE_NAMES[content_role]
        cut_type = b.get("cutType") or None
        meta = cut_meta_by_block.get(b.get("id")) or {}
        src = meta.get("imageUrl")  # 없으면 None → 빈 슬롯 (생성 실패해도 크래시 안 함)
        img_w, img_h = _image_box(meta.get("width"), meta.get("height"))
        els = [_image_el(block_i, 0, _IMG_X, _IMG_Y, img_w, img_h, src, 12, cut_type,
                          source_block_id=b.get("id"))]
        el_j = 1

        if b.get("hookTitleOverlay") and str(product.get("name") or "").strip():
            # 시그니처 컷 계약(스펙 2026-08-14 §1): 제품명을 이미지 중앙에 흰색으로 얹는다 —
            # 카피라이팅 토글과 무관한 첫 화면 구성 요소이며, 셀러는 에디터에서 자유 수정한다.
            els.append(_text_el(block_i, el_j, _IMG_X, _IMG_Y + img_h // 2 - 30, img_w, 60,
                                 str(product.get("name")).strip(),
                                 {"size": 34, "weight": 650, "color": "#ffffff", "align": "center"},
                                 source_block_id=b.get("id"), copy_role="hookTitle"))
            el_j += 1

        if copywriting:
            texts = copy_by_block.get(b.get("id"), [])
            if content_role == "hero":
                headline = _text_for_role(texts, "headline")
                if headline:
                    els.append(_text_el(block_i, el_j, 120, 110, 600, 80, headline,
                                         {"size": 40, "weight": 600, "font": "Cal Sans", "color": "#0e0d14"},
                                         source_block_id=b.get("id"), copy_role="headline"))
                    el_j += 1
            else:
                body = _text_for_role(texts, "body")
                if body:
                    # 이미지 하단 근처(구 레이아웃의 시각 관계) — 이미지 높이에서 유도한다.
                    els.append(_text_el(block_i, el_j, 120, _IMG_Y + img_h - _BODY_INSET_B, 760, 40, body,
                                         {"size": 18, "color": "#4a4a45"},
                                         source_block_id=b.get("id"), copy_role="body"))
                    el_j += 1

        editor_block = {
            "id": _block_id(block_i), "name": name, "kind": section_role,
            "contentRole": content_role,
            "bg": bg, "h": _block_height(els), "elements": els,
        }
        blocks.append(editor_block)

    def push_row(chunk: list[dict], layout: str) -> None:
        block_i = len(blocks)
        row_layout = _ROW_LAYOUTS[layout]
        count = len(chunk)
        width = (880 - (count - 1) * 20) // count
        els: list[dict] = []
        for column, row_block in enumerate(chunk):
            meta = cut_meta_by_block.get(row_block.get("id")) or {}
            els.append(_image_el(
                block_i,
                column,
                60 + column * (width + 20),
                50,
                width,
                500,
                meta.get("imageUrl"),
                12,
                row_block.get("cutType") or None,
                source_block_id=row_block.get("id"),
            ))

        if copywriting:
            hero = next((row_block for row_block in chunk
                         if resolve_content_role(row_block) == "hero"), None)
            if hero:
                headline = _text_for_role(copy_by_block.get(hero.get("id"), []), "headline")
                if headline:
                    els.append(_text_el(
                        block_i, len(els), 60, 582, 880, 56, headline,
                        {"size": 40, "weight": 600, "font": "Cal Sans", "color": "#0e0d14"},
                        source_block_id=hero.get("id"), copy_role="headline",
                    ))
                subtitle_block = next((row_block for row_block in chunk
                                       if resolve_content_role(row_block) == "benefit"), None)
                subtitle = _text_for_role(
                    copy_by_block.get(subtitle_block.get("id"), []) if subtitle_block else [],
                    "body",
                )
                if subtitle:
                    els.append(_text_el(
                        block_i, len(els), 60, 650, 880, 34, subtitle,
                        {"size": 18, "color": "#6b6b73"},
                        source_block_id=subtitle_block.get("id") if subtitle_block else None,
                        copy_role="body",
                    ))

        blocks.append({
            "id": _block_id(block_i),
            "name": row_layout["name"],
            "kind": row_layout["kind"],
            "bg": "#f5f5f5" if block_i % 2 else "#ffffff",
            "elements": els,
        })

    def push_colorway_pair(pair: list[dict]) -> None:
        block_i = len(blocks)
        ordered = sorted(pair, key=lambda block: 0 if block.get("shot") == "full" else 1)
        width = 430
        gap = 20
        els: list[dict] = []
        image_bottom = 0
        for column, row_block in enumerate(ordered):
            meta = cut_meta_by_block.get(row_block.get("id")) or {}
            _, image_height = _image_box_for_width(width, meta.get("width"), meta.get("height"))
            image_y = 24
            els.append(_image_el(
                block_i,
                column,
                60 + column * (width + gap),
                image_y,
                width,
                image_height,
                meta.get("imageUrl"),
                0,
                row_block.get("cutType") or None,
                source_block_id=row_block.get("id"),
            ))
            image_bottom = max(image_bottom, image_y + image_height)

        product_label, matching_label = _colorway_labels(product, ordered)
        label_y = image_bottom + 14
        els.append(_text_el(
            block_i, len(els), 60, label_y, 880, 24, product_label,
            {"size": 14, "weight": 400, "color": "#4a4a45", "align": "center", "tracking": 0.2},
        ))
        if matching_label:
            els.append(_text_el(
                block_i, len(els), 60, label_y + 22, 880, 26, matching_label,
                {"size": 15, "weight": 700, "color": "#0e0d14", "align": "center", "tracking": 0.1},
            ))

        color_name = product_label.rsplit("[", 1)[-1].rstrip("]")
        blocks.append({
            "id": _block_id(block_i),
            "name": f"컬러 룩 · {color_name}",
            "kind": "twocol",
            "layoutType": "colorwayPair",
            "bg": "#f5f5f5",
            "h": _block_height(els),
            "elements": els,
        })

    arranged = storyboard or []
    i = 0
    while i < len(arranged):
        b = arranged[i]
        if i + 1 < len(arranged) and _colorway_pair(b, arranged[i + 1]):
            push_colorway_pair(arranged[i:i + 2])
            i += 2
            continue
        layout = b.get("sectionLayout")
        row_id = b.get("layoutRowId")
        section_id = b.get("sectionId")
        if (b.get("source") != "mine" and layout in _ROW_LAYOUTS and row_id and section_id):
            end = i + 1
            while end < len(arranged):
                candidate = arranged[end]
                if (candidate.get("source") == "mine"
                        or candidate.get("sectionId") != section_id
                        or candidate.get("sectionLayout") != layout
                        or candidate.get("layoutRowId") != row_id):
                    break
                end += 1
            members = arranged[i:end]
            if len(members) > 1:
                push_row(members, layout)
                i = end
                continue
        push_single(b)
        i += 1

    return blocks + build_auto_blocks(product, start_index=len(blocks),
                                      license_notice=license_notice)
