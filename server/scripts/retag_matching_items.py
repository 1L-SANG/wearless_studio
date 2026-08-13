"""60개 매칭 의류를 Gemini 비전으로 닫힌 style tag enum에 재태깅한다.

실행(네트워크·Gemini 비용 발생):
    cd server
    .venv/bin/python scripts/retag_matching_items.py

요청 페이로드만 확인(네트워크·파일 쓰기 없음):
    .venv/bin/python scripts/retag_matching_items.py --dry-run

성공한 제안은 매 건 ``scripts/retag/proposals.json``에 저장한다. 다시 실행하면 이미
저장된 id는 건너뛰므로, 호출 실패나 중단 뒤에도 이어서 실행할 수 있다.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx

SERVER_DIR = Path(__file__).resolve().parents[1]
ITEMS_PATH = SERVER_DIR / "scripts" / "retag" / "items.json"
PROPOSALS_PATH = SERVER_DIR / "scripts" / "retag" / "proposals.json"


def _load_env(path: Path) -> None:
    """python-dotenv 의존 없이 server/.env의 미설정 값만 환경에 적재한다."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env(SERVER_DIR / ".env")
sys.path.insert(0, str(SERVER_DIR))

from app.agents.gemini_image import InlineImage  # noqa: E402
from app.agents.style_tags import STYLE_TAG_SET, STYLE_TAGS  # noqa: E402
from app.agents.vision_llm import _to_gemini_schema  # noqa: E402
from app.config import Settings, load_settings  # noqa: E402


RETAG_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "style_tags": {
            "type": "array",
            "items": {"type": "string", "enum": list(STYLE_TAGS)},
            "minItems": 2,
            "maxItems": 4,
            "uniqueItems": True,
            "description": "이 옷과 어울리는 코디 스타일 방향 2~4개",
        },
        "reason": {
            "type": "string",
            "description": "이미지와 메타데이터를 근거로 한 한국어 한 문장",
        },
    },
    "required": ["style_tags", "reason"],
    "additionalProperties": False,
}

_TAG_GUIDE = """\
basic=장식과 개성이 거의 없는 범용 기본, daily=일상 활용 중심, minimal=절제되고 간결함,
casual=편안한 캐주얼, formal=격식, classic=전통적이고 정제됨, sporty=스포츠웨어 무드,
trendy=현재 유행 지향, street=도시적 스트리트, chic=날렵하고 시크함,
feminine=여성스럽고 우아함, lovely=사랑스럽고 발랄함, romantic=로맨틱한 장식과 실루엣,
vintage=빈티지 감성, retro=특정 과거 시대의 복고, modern=현대적이고 선명한 인상,
luxury=고급스럽고 풍부함, preppy=단정한 스쿨·아이비 무드, workwear=실용적 작업복 무드,
athleisure=운동복과 일상복의 결합, cozy=포근하고 여유로움, unique=독특한 개성,
sophisticated=세련되고 성숙함, y2k=2000년대 초반 감성"""


def build_prompt(item: dict) -> str:
    """소재명 추출이 아니라 '어울리는 스타일 방향'에 집중시키는 단일 상품 프롬프트."""
    return f"""당신은 패션 코디 큐레이터입니다. 첨부된 옷 한 벌의 이미지와 아래 메타데이터를
함께 보고, 이 옷이 잘 어울리는 코디 스타일 방향을 고르세요.

메타데이터
- 카테고리: {item.get('category') or '미상'}
- 색: {item.get('color_name') or '미상'}
- 핏: {item.get('fit') or '미상'}

허용 태그와 의미
{_TAG_GUIDE}

판정 규칙
1. style_tags는 위 허용 enum 중 서로 다른 2~4개만 고릅니다. enum 밖 단어를 만들지 마세요.
2. 소재·조직·디테일의 이름을 태그로 옮기지 말고, 그것이 만드는 '코디 무드와 방향'을 고르세요.
3. basic과 daily는 정말 장식이 적고 어디에나 쓰이는 무난한 기본템일 때만 허용합니다.
   습관적인 기본값이나 개수 채우기로 쓰지 말고, 근거가 약하면 둘 다 빼세요.
4. 같은 카테고리라도 색, 핏, 실루엣과 이미지 속 디테일이 다르면 태그가 달라질 수 있습니다.
5. 예: 헨리넥 와플 → cozy·casual·daily / 플리츠 와이드 슬랙스 →
   minimal·sophisticated·modern / 디스트레스드 슬리브리스 → street·y2k·trendy.
6. reason은 선택 근거와 코디 방향을 담은 짧은 한국어 한 문장으로 쓰고 줄바꿈하지 마세요.
"""


def _gemini_schema() -> dict:
    """공용 변환기를 쓰되 Gemini Schema가 지원하는 배열 길이 제약을 보존한다."""
    schema = _to_gemini_schema(RETAG_RESPONSE_SCHEMA)
    tags = schema["properties"]["style_tags"]
    tags["minItems"] = 2
    tags["maxItems"] = 4
    return schema


def build_request_payload(item: dict, image: InlineImage) -> dict:
    return {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": build_prompt(item)},
                    {
                        "inline_data": {
                            "mime_type": image.mime,
                            "data": base64.b64encode(image.data).decode(),
                        }
                    },
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseSchema": _gemini_schema(),
            "thinkingConfig": {"thinkingLevel": "low"},
        },
    }


def _endpoint(settings: Settings) -> str:
    """GeminiImageClient와 같은 AI Studio/Vertex 엔드포인트 선택 규약."""
    model = settings.model_text_gemini
    if settings.vertex_project:
        location = settings.vertex_location
        host = (
            "aiplatform.googleapis.com"
            if location == "global"
            else f"{location}-aiplatform.googleapis.com"
        )
        return (
            f"https://{host}/v1/projects/{settings.vertex_project}/locations/{location}"
            f"/publishers/google/models/{model}:generateContent"
        )
    return f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _load_json_list(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"파일이 없습니다: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 형식 오류: {path}: {exc}") from exc
    if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
        raise ValueError(f"객체 배열이어야 합니다: {path}")
    return data


def _unique_by_id(rows: list[dict], label: str) -> dict[str, dict]:
    indexed: dict[str, dict] = {}
    for row in rows:
        item_id = row.get("id")
        if not isinstance(item_id, str) or not item_id:
            raise ValueError(f"{label}에 유효하지 않은 id가 있습니다.")
        if item_id in indexed:
            raise ValueError(f"{label}에 중복 id가 있습니다: {item_id}")
        indexed[item_id] = row
    return indexed


def _atomic_write_json(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _guess_mime(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(suffix, "image/jpeg")


async def _fetch_thumbnail(client: httpx.AsyncClient, item: dict) -> InlineImage:
    url = item.get("thumb_url")
    if not isinstance(url, str) or urlparse(url).scheme not in {"http", "https"}:
        raise ValueError(f"{item.get('id')}: 유효하지 않은 thumb_url")
    response = await client.get(url, timeout=30.0)
    response.raise_for_status()
    if not response.content:
        raise ValueError(f"{item['id']}: 썸네일 응답이 비어 있습니다.")
    mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if not mime.startswith("image/"):
        mime = _guess_mime(url)
    return InlineImage(mime=mime, data=response.content)


def _parse_response(response: httpx.Response, item: dict) -> dict:
    try:
        envelope = response.json()
        parts = (((envelope.get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
        text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
        raw = json.loads(text)
    except (AttributeError, IndexError, TypeError, ValueError) as exc:
        raise ValueError(f"{item['id']}: Gemini JSON 응답을 해석할 수 없습니다: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{item['id']}: Gemini 결과가 객체가 아닙니다.")
    return raw


def _validated_proposal(item: dict, raw: dict) -> dict:
    tags = raw.get("style_tags")
    if not isinstance(tags, list) or not 2 <= len(tags) <= 4:
        raise ValueError(f"{item['id']}: style_tags는 2~4개여야 합니다: {tags!r}")
    if not all(isinstance(tag, str) and tag in STYLE_TAG_SET for tag in tags):
        invalid = [tag for tag in tags if tag not in STYLE_TAG_SET]
        raise ValueError(f"{item['id']}: enum 밖 style tag: {invalid}")
    if len(tags) != len(set(tags)):
        raise ValueError(f"{item['id']}: style_tags가 중복되었습니다: {tags}")
    raw_reason = raw.get("reason")
    if not isinstance(raw_reason, str) or not raw_reason.strip():
        raise ValueError(f"{item['id']}: reason이 비어 있습니다.")
    reason = " ".join(raw_reason.split())
    return {
        "id": item["id"],
        "name": item.get("name") or "",
        "current_tags": list(item.get("current_tags") or []),
        "proposed_tags": tags,
        "reason": reason,
    }


async def _request_proposal(
    client: httpx.AsyncClient,
    settings: Settings,
    item: dict,
    image: InlineImage,
) -> dict:
    payload = build_request_payload(item, image)
    response: httpx.Response | None = None
    for attempt in range(3):
        response = await client.post(
            _endpoint(settings),
            json=payload,
            headers={"x-goog-api-key": settings.gemini_api_key or ""},
            timeout=settings.analysis_timeout_seconds,
        )
        if response.status_code != 429 or attempt == 2:
            break
        await asyncio.sleep(5 * (attempt + 1))
    assert response is not None
    if response.status_code != 200:
        raise RuntimeError(f"{item['id']}: Gemini {response.status_code}: {response.text[:300]}")
    return _validated_proposal(item, _parse_response(response, item))


def _print_dry_run(item: dict, settings: Settings) -> None:
    placeholder = InlineImage(mime=_guess_mime(item["thumb_url"]), data=b"")
    payload = build_request_payload(item, placeholder)
    payload["contents"][0]["parts"][1]["inline_data"]["data"] = (
        f"<실행 시 fetch 후 base64 인코딩: {item['thumb_url']}>"
    )
    print(
        json.dumps(
            {
                "model": settings.model_text_gemini,
                "item_id": item["id"],
                "request": payload,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


async def run(items_path: Path, output_path: Path, *, dry_run: bool) -> int:
    items = _load_json_list(items_path)
    item_index = _unique_by_id(items, "items.json")
    if not items:
        raise ValueError("items.json이 비어 있습니다.")
    settings = load_settings()

    if dry_run:
        _print_dry_run(items[0], settings)
        return 0
    if not settings.gemini_api_key:
        raise ValueError(f"GEMINI_API_KEY가 없습니다: {SERVER_DIR / '.env'}")

    proposals = _load_json_list(output_path) if output_path.exists() else []
    proposal_index = _unique_by_id(proposals, "proposals.json")
    unknown = set(proposal_index) - set(item_index)
    if unknown:
        raise ValueError(f"proposals.json에 items.json 밖 id가 있습니다: {sorted(unknown)}")
    for item_id, proposal in proposal_index.items():
        _validated_proposal(
            item_index[item_id],
            {
                "style_tags": proposal.get("proposed_tags"),
                "reason": proposal.get("reason"),
            },
        )

    pending = [item for item in items if item["id"] not in proposal_index]
    print(f"전체 {len(items)}건 · 완료 {len(proposals)}건 · 남음 {len(pending)}건")
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for position, item in enumerate(pending, start=len(proposals) + 1):
            print(f"[{position}/{len(items)}] {item['id']} {item.get('name', '')}", flush=True)
            image = await _fetch_thumbnail(client, item)
            proposal = await _request_proposal(client, settings, item, image)
            proposals.append(proposal)
            proposal_index[item["id"]] = proposal
            _atomic_write_json(output_path, proposals)
            print(f"  → {', '.join(proposal['proposed_tags'])}: {proposal['reason']}", flush=True)
    print(f"완료: {output_path} ({len(proposals)}건)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="첫 1벌 요청 페이로드만 출력")
    parser.add_argument("--items", type=Path, default=ITEMS_PATH)
    parser.add_argument("--output", type=Path, default=PROPOSALS_PATH)
    args = parser.parse_args()
    try:
        return asyncio.run(run(args.items, args.output, dry_run=args.dry_run))
    except (httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
