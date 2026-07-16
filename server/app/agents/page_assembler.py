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

KIND_NAMES = {
    "hook": "후킹",
    "selling": "셀링포인트",
    "styling": "스타일링컷",
    "horizon": "호리존컷",
    "product": "제품컷",
    "info": "블록",
}


def _block_id(i: int) -> str:
    return f"b{i}"


def _el_id(i: int, j: int) -> str:
    return f"b{i}e{j}"


def _text_el(block_i: int, el_j: int, x, y, w, h, text: str, style: dict | None = None) -> dict:
    """mock T(x,y,w,h,text,style) 포팅 — id만 결정적으로 대체."""
    return {
        "id": _el_id(block_i, el_j),
        "type": "text",
        "x": x, "y": y, "w": w, "h": h,
        "text": text,
        "style": style or {},
    }


def _image_el(block_i: int, el_j: int, x, y, w, h, src, radius=None, cut_type=None) -> dict:
    """mock IMG(x,y,w,h,src,radius,cutType) 포팅 — id만 결정적으로 대체.
    src=None 은 계약 §3.5 Element(image) 의 '빈 슬롯' 표현."""
    el = {
        "id": _el_id(block_i, el_j),
        "type": "image",
        "x": x, "y": y, "w": w, "h": h,
        "src": src,
        "radius": radius if radius is not None else 8,
    }
    if cut_type:
        el["cutType"] = cut_type
    return el


def _cut_url_by_block(cut_results: list[dict] | None) -> dict:
    out: dict[str, str] = {}
    for r in cut_results or []:
        if isinstance(r, dict) and r.get("blockId"):
            out[r["blockId"]] = r.get("imageUrl")
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
    if license_notice:
        face_cuts = license_notice.get("faceCuts") or 0
        total_cuts = license_notice.get("totalCuts") or 0
        # 전 컷이 라이선스 얼굴일 때만 페이지 전체를 '가상인물 아님' 으로 주장할 수 있다.
        # 하나라도 얼굴 미첨부 컷이 있으면 그 인물은 AI 가 지어낸 가상인물 → '일부 컷' 문구.
        # total 을 모르면(0) 안전측으로 '일부' 를 쓴다 — 과대 주장이 허위표시 방향이다.
        all_licensed = total_cuts > 0 and face_cuts >= total_cuts
        template = _AI_NOTICE_LICENSED_ALL if all_licensed else _AI_NOTICE_LICENSED_PARTIAL
        notice_text = template.format(
            model_name=license_notice.get("modelName") or "익명",
            license_id=license_notice.get("licenseId") or "",
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
    copywriting=True 면 hook/selling 블록에 copy_results 텍스트를 배치한다
    (mock 하드코딩 문자열 대신 실제 카피 사용).
    license_notice 는 AI 생성 안내 문구 분기용으로 그대로 통과시킨다(build_auto_blocks 참고).
    """
    cut_url_by_block = _cut_url_by_block(cut_results)
    copy_by_block = _copy_texts_by_block(copy_results)

    blocks: list[dict] = []
    for i, b in enumerate(storyboard or []):
        bg = "#f5f5f5" if i % 2 else "#ffffff"

        if b.get("source") == "mine":
            own_images = (b.get("ownImages") or [])[:1]
            els = [
                _image_el(i, j, 60, 50, 880, 560, src, 12)
                for j, src in enumerate(own_images)
            ]
            blocks.append({
                "id": _block_id(i), "name": "내 이미지", "kind": "info",
                "bg": bg, "h": 660, "elements": els,
            })
            continue

        name = b.get("title") or KIND_NAMES.get(b.get("kind"), "컷")
        cut_type = b.get("cutType") or None
        src = cut_url_by_block.get(b.get("id"))  # 없으면 None → 빈 슬롯 (생성 실패해도 크래시 안 함)
        els = [_image_el(i, 0, 60, 50, 880, 560, src, 12, cut_type)]
        el_j = 1

        if copywriting:
            texts = copy_by_block.get(b.get("id"), [])
            if b.get("kind") == "hook":
                headline = _text_for_role(texts, "headline")
                if headline:
                    els.append(_text_el(i, el_j, 120, 110, 600, 80, headline,
                                         {"size": 40, "weight": 600, "font": "Cal Sans", "color": "#0e0d14"}))
                    el_j += 1
            if b.get("kind") == "selling":
                body = _text_for_role(texts, "body")
                if body:
                    els.append(_text_el(i, el_j, 120, 560, 760, 40, body,
                                         {"size": 18, "color": "#4a4a45"}))
                    el_j += 1

        blocks.append({
            "id": _block_id(i), "name": name, "kind": b.get("kind"),
            "bg": bg, "h": 660, "elements": els,
        })

    return blocks + build_auto_blocks(product, start_index=len(blocks),
                                      license_notice=license_notice)
