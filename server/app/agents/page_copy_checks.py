"""Deterministic Korean copy checks, without provider or renderer dependencies.

These are conservative lexical signals, not a morphological parser or a semantic
fact checker. Only the page repetition results are advisory. Domain inflections
and FAQ topics are deliberately small, inspectable vocabularies; novel paraphrases
can escape detection. Callers keep ownership of source facts and material names.
"""
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from functools import lru_cache
import json
from pathlib import Path
import re
import unicodedata


_PROMOTION = re.compile(
    r"배송|교환|반품|환불|주문\s*취소|결제|입금|취소\s*(?:불가|가능|기한)|할인|최저가|재입고|품절|신상|1\s*\+\s*1|한정|특가|세일|"
    r"쿠폰|적립금|자사몰|가격|가성비|보상|책임지지|인스타|팔로우|디엠|카카오|카톡|채널|"
    r"유튜브|틱톡|블로그|네이버|스토어|링크|https?://|www\.|@|(?<![a-z])dm(?![a-z])|문의\s*주세요",
    re.I,
)
_DESIGN_DIRECTION = re.compile(
    r"디자이너|썸네일|상세\s*페이지.{0,20}(?:넣|배치|강조|수정)|"
    r"(?:강조|부각|배치|수정|삭제|교체|편집)(?:해|하여)?\s*(?:주|부탁)|"
    r"(?:문구|사진|이미지|컷|텍스트).{0,24}(?:넣어|넣지|빼\s*주|위주|써\s*주|사용해|사용하지)"
)


def clean_seller_text(text: str) -> str:
    """Drop entire offending sentences, retaining product wording and newlines.

    A sentence ends at punctuation followed by whitespace or a line boundary.
    Decimal points and URL dots therefore do not split an offending sentence into
    fragments that could leak back into the cleaned source.
    """
    lines = []
    for line in str(text or "").splitlines():
        sentences = re.split(r"(?<=[.!?。！？])\s+|…+", line)
        kept = [s.strip() for s in sentences if s.strip()
                and not _PROMOTION.search(s) and not _DESIGN_DIRECTION.search(s)]
        if kept:
            lines.append(" ".join(kept))
    return "\n".join(lines)


_STEMS = (
    (r"부드[러럽].*", "부드러"), (r"가[벼볍].*", "가벼"),
    (r"두[꺼껍].*", "두꺼"), (r"차[가갑].*", "차가"),
    (r"(?:탄탄|도톰|단정|매끈|깔끔|촘촘|유연|폭신|보송|쫀쫀|시원|따뜻|편안).*", None),
    (r"찰랑.*", "찰랑"), (r"편(?:하|한|해|함).*", "편안"),
    (r"조절.*", "조절"), (r"수납.*", "수납"), (r"산책.*", "산책"),
    (r"출근.*", "출근"), (r"세탁.*|손세탁.*|손빨래.*|물빨래.*", "세탁"),
)
_SUFFIXES = sorted((
    "으로부터", "에서부터", "으로서는", "으로써", "으로서", "에게서", "에서는", "으로는",
    "이라고", "이라는", "이라서", "이라면", "이어서", "입니다", "이에요", "예요", "이네요",
    "했어요", "해요", "할게요", "할까요", "하나요", "합니다", "하고", "하게", "해서", "하면",
    "하는", "하기", "하며", "한", "으로", "에서", "에게", "까지", "부터", "처럼", "보다",
    "이랑", "이라", "에는", "에도", "만의", "해도", "할", "은", "는", "을", "를", "이", "가",
    "의", "와", "과", "도", "만", "로", "에",
), key=len, reverse=True)
_STOP_WORDS = {
    "있어요", "있어", "있습니다", "있는", "있고", "있다면", "없어요", "없어", "없습니다", "없는",
    "같은", "같이", "함께", "이런", "이렇게", "그런", "그렇게", "어떤", "어떻게", "무엇", "정말",
    "조금", "아주", "너무", "그리고", "하지만", "또한", "위해", "통해", "대한", "대해", "때문",
    "위한", "하기", "사용", "입기", "입고", "입을", "입는", "입어", "입어도", "입으면", "입으면요",
    "입었어요", "입어요", "옷을", "찾는", "찾고", "찾으시", "싶은", "싶어", "좋아요", "좋은", "좋고",
    "좋아", "보여요", "보여", "보이는", "보이", "보세요", "확인", "주세요", "부탁드려요", "부탁드려",
    "가능", "되는", "돼요", "됩니다", "그대로", "자연스럽게", "자연스럽", "추천", "느낌", "느껴져요",
}


def _content_word(word: str) -> str:
    if word in _STOP_WORDS:
        return ""
    for pattern, stem in _STEMS:
        if re.fullmatch(pattern, word):
            return stem if stem is not None else word[:2]
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 2:
            word = word[:-len(suffix)]
            break
    return "" if word in _STOP_WORDS or len(word) < 2 else word


def content_words(text: str) -> set[str]:
    """Return Korean content stems of two or more characters.

    Known irregular apparel adjectives are normalized before stripping a single
    particle or ending. This avoids pretending to perform general Korean parsing.
    """
    return {stem for word in re.findall(r"[가-힣]+", unicodedata.normalize("NFKC", text or ""))
            if (stem := _content_word(word))}


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or "")).casefold()


def fabric_restatement(title: str, body: str) -> bool:
    body_words = content_words(body)
    return bool(body_words) and body_words <= content_words(title)


def _mostly_same(left: str, right: str, *, minimum: int = 2) -> bool:
    a, b = content_words(left), content_words(right)
    shared = a & b
    return len(shared) >= minimum and len(shared) / max(len(a), len(b)) >= 0.8


_FAQ_TOPICS = {
    "washing": r"세탁|빨래|드라이|건조|다림|물에\s*빨|세제",
    "transparency": r"비침|비치|비쳐|속옷|이너",
    "size": r"사이즈|치수|크기|정사이즈|작게\s*나|크게\s*나|체형|어깨가\s*넓",
    "stretch": r"늘어|신축|탄성|탄력|텐션",
    "season": r"계절|두께|두꺼|두껍|얇|봄|여름|가을|겨울|간절기",
}


def faq_overlap(question_a: str, answer_a: str, question_b: str, answer_b: str) -> bool:
    """Flag a shared known question topic or a substantially reused answer."""
    topics_a = {name for name, pattern in _FAQ_TOPICS.items() if re.search(pattern, question_a)}
    topics_b = {name for name, pattern in _FAQ_TOPICS.items() if re.search(pattern, question_b)}
    return bool(topics_a & topics_b) or _mostly_same(answer_a, answer_b, minimum=1)


def recommendation_overlap(items: Sequence[str]) -> list[tuple[int, int]]:
    """Return earlier/later item pairs whose substantive content is reused."""
    return [(i, j) for j, text in enumerate(items) for i in range(j)
            if _mostly_same(items[i], text, minimum=1)]


def related_detail_overlap(text_a: str, text_b: str) -> bool:
    """Compare descriptions only after the caller establishes related parts."""
    return _mostly_same(text_a, text_b)


_GARMENTS = content_words(
    "상의 하의 아우터 원피스 티셔츠 셔츠 블라우스 니트 가디건 스웨터 스웨트 맨투맨 후드티 "
    "바지 팬츠 슬랙스 청바지 데님 스커트 치마 재킷 자켓 코트 점퍼 패딩 베스트 조끼 쇼츠 반바지"
)


def _fact_names(facts: Mapping) -> set[str]:
    values = [facts.get(key, "") for key in ("name", "clothingType", "garment", "fit", "dominantMaterial")]
    values.extend(facts.get("axes", {}).values())
    values.extend(c if isinstance(c, str) else c.get("name", "") for c in facts.get("colors", []))
    for key in ("materials", "parts"):
        values.extend(row if isinstance(row, str) else row.get("name", "") for row in facts.get(key, []))
    values.append(facts.get("category", {}).get("name", ""))
    return content_words(" ".join(v for v in values if isinstance(v, str)))


def page_repetitions(slots: Sequence[Mapping], texts: Mapping[str, str], facts: Mapping) -> list[dict]:
    """Advise rewriting later slots; this function never rejects or edits text.

    A word must occur in at least four distinct slots. Repeated final two-eojeol
    frames must cover more than half the distinct items of their section. Multiple
    roles of one item count once. Non-item sections use their text slots as items.
    """
    excluded = _GARMENTS | _fact_names(facts)
    occurrences = defaultdict(list)
    sections = defaultdict(list)
    seen = set()
    for slot in slots:
        slot_id = slot["id"]
        text = texts.get(slot_id, "")
        if slot_id in seen or not isinstance(text, str) or not text.strip():
            continue
        seen.add(slot_id)
        for word in sorted(content_words(text) - excluded):
            occurrences[word].append(slot_id)
        role = slot.get("role", "")
        item = re.match(r"items\.(\d+)(?:\.|$)", role)
        item_id = item[1] if item else slot_id
        words = re.findall(r"[가-힣A-Za-z0-9]+", text)
        frame = " ".join(words[-2:]) if len(words) >= 2 else ""
        sections[slot.get("sectionKey", slot.get("key", ""))].append((bool(item), item_id, slot_id, frame))
    warnings = []
    for word, ids in occurrences.items():
        if len(ids) >= 4:
            warnings.extend({"reason": "page_content_repetition", "slotId": slot_id,
                             "word": word, "count": len(ids)} for slot_id in ids[1:])
    for section, rows in sections.items():
        if any(is_item for is_item, _, _, _ in rows):
            rows = [row for row in rows if row[0]]
        item_count = len({item for _, item, _, _ in rows})
        if item_count < 2:
            continue
        frames = defaultdict(list)
        for _, item, slot_id, frame in rows:
            if frame and item not in {entry[0] for entry in frames[frame]}:
                frames[frame].append((item, slot_id))
        for frame, matches in frames.items():
            if len(matches) >= 2 and len(matches) > item_count / 2:
                warnings.extend({"reason": "section_sentence_frame", "slotId": slot_id,
                                 "sectionKey": section, "frame": frame, "count": len(matches)}
                                for _, slot_id in matches[1:])
    return warnings


def exemplar_copy(text: str, exemplars: Sequence[str]) -> bool:
    """Detect at least eight contiguous characters after whitespace removal."""
    value = _compact(text)
    if len(value) < 8:
        return False
    windows = {value[i:i + 8] for i in range(len(value) - 7)}
    return any(any(example[i:i + 8] in windows for i in range(len(example) - 7))
               for example in map(_compact, exemplars))


@lru_cache(maxsize=1)
def _trade_term_patterns() -> tuple[tuple[str, re.Pattern], ...]:
    path = Path(__file__).resolve().parents[1] / "data" / "page_copy_trade_terms.json"
    terms = json.loads(path.read_text(encoding="utf-8"))["terms"]
    return tuple((term, re.compile(r"\s*".join(map(re.escape, term.split())))) for term in terms)


def trade_terms(text: str) -> list[str]:
    return [term for term, pattern in _trade_term_patterns() if pattern.search(text)]


_COUNTERS = r"퍼센트|단계|군데|가지|컬러|사이즈|센티미터|밀리미터|센티|밀리|인치|센치|cm|mm|kg|개|장|벌|쌍|줄|칸|겹|단|색|회|번|배|곳|명|미터|m|g|%"
_UNITS = {"센티미터": "cm", "센티": "cm", "센치": "cm", "밀리미터": "mm", "밀리": "mm",
          "미터": "m", "퍼센트": "%", "컬러": "색", "곳": "군데"}
_NATIVE_ONES = {"하나": 1, "한": 1, "둘": 2, "두": 2, "셋": 3, "세": 3, "넷": 4, "네": 4,
                "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9}
_NATIVE_TENS = {"열": 10, "스물": 20, "스무": 20, "서른": 30, "마흔": 40, "쉰": 50,
                "예순": 60, "일흔": 70, "여든": 80, "아흔": 90}
_NATIVE = dict(_NATIVE_ONES, **_NATIVE_TENS)
_NATIVE.update({t + o: n + m for t, n in _NATIVE_TENS.items() if t != "스무" for o, m in _NATIVE_ONES.items()})
_NATIVE_PATTERN = "|".join(sorted(_NATIVE, key=len, reverse=True))


def _sino_number(word: str) -> int:
    total, digit = 0, 0
    for char in word:
        if char in "십백천":
            total += (digit or 1) * {"십": 10, "백": 100, "천": 1000}[char]
            digit = 0
        else:
            digit = "영일이삼사오육칠팔구".index(char)
    return total + digit


def _quantities(text: str) -> set[tuple[str, str]]:
    text = unicodedata.normalize("NFKC", text or "").casefold()
    values = set()
    for match in re.finditer(r"(?<![\d.])[+-]?\d+(?:,\d{3})*(?:\.\d+)?(?:\s*(?:" + _COUNTERS + r"))?", text):
        value = match[0]
        number = re.match(r"[+-]?[\d,.]+", value)[0]
        unit = value[len(number):].strip()
        values.add((str(Decimal(number.replace(",", "")).normalize()), _UNITS.get(unit, unit)))
    for match in re.finditer(r"(?<![가-힣])(" + _NATIVE_PATTERN + r")\s*(" + _COUNTERS + r")", text):
        values.add((str(Decimal(_NATIVE[match[1]]).normalize()), _UNITS.get(match[2], match[2])))
    for match in re.finditer(r"(?<![가-힣])([일이삼사오육칠팔구십백천]+)\s*(" + _COUNTERS + r")", text):
        values.add((str(Decimal(_sino_number(match[1])).normalize()), _UNITS.get(match[2], match[2])))
    # Standalone native numbers are safe only in their full noun form; '한' can
    # modify ordinary prose, while '하나/둘/셋/넷' are countable quantities here.
    for match in re.finditer(r"(?<![가-힣])(하나|둘|셋|넷)(?=$|\s|[.!?,]|예요|이에요|입니다|가|를|는|씩)", text):
        values.add((str(_NATIVE[match[1]]), "개"))
    return values


_FUNCTIONS = {
    "adjustment": r"조절|조정|줄일|줄여|좁힐|넓힐",
    "storage": r"수납|(?:물건|소지품|휴대폰|손)(?:을|이|도)?\s*(?:넣|담)|포켓.{0,12}(?:넣|담)",
    "closure": r"여닫|열고\s*닫|열었다|잠그|잠글|잠가|오픈|클로징",
    "waterproof": r"방수|물을?\s*막",
    "water_repellent": r"발수",
    "windproof": r"방풍|바람을?\s*막",
    "warmth": r"보온|따뜻|따듯|따스",
    "stretch": r"늘어나|늘어남|늘어나는|신축|탄성|탄력|텐션",
    "breathability": r"통기|통풍|바람이?\s*통",
    "absorption": r"흡수|땀(?:을|이)?\s*(?:배출|빼)",
    "antibacterial": r"항균",
    "uv_protection": r"자외선|\buv\b",
    "wrinkle": r"구김|주름.{0,5}(?:안|않|없|방지)",
    "durability": r"내구|견고|튼튼|변형|형태\s*유지|모양\s*유지",
    "opacity": r"비치지\s*않|비침\s*없|비침이\s*없|안\s*비치",
    "skin_irritation": r"피부\s*자극|저자극",
    "stability": r"안정적|안정감",
    "stretch_resistance": r"늘어짐|늘어나지",
    "comfort": r"쾌적",
    "universal_fit": r"누구나",
    "all_day": r"하루\s*종일",
}


def _functional_claims(text: str) -> set[tuple[str, bool]]:
    claims = set()
    for sentence in re.split(r"[.!?\n]", text.casefold()):
        for name, pattern in _FUNCTIONS.items():
            for match in re.finditer(pattern, sentence):
                # Track nearby negation so a paraphrase cannot turn an explicit
                # limitation into the corresponding positive performance claim.
                before = sentence[max(0, match.start() - 5):match.start()]
                after = sentence[match.end():match.end() + 12]
                negated = bool(re.search(r"(?:안|못)\s*$", before) or
                               re.search(r"(?:이|가|은|는)?\s*(?:안\s|못\s|않|없|불가|불가능)", after))
                claims.add((name, negated))
    return claims


def unsupported_additions(source: str, text: str, material_words: Callable[[str], set[str]]) -> list[str]:
    """Flag numeric, canonical-material or recognized functional additions.

    Compare descriptions with factual detail evidence, allowing equivalent
    quantities, material aliases and recognized functional paraphrases.
    """
    reasons = []
    if _quantities(text) - _quantities(source):
        reasons.append("number_added")
    if material_words(text) - material_words(source):
        reasons.append("material_added")
    if _functional_claims(text) - _functional_claims(source):
        reasons.append("claim_added")
    return reasons


def warmth_evidence(materials, evidence: str) -> bool:
    """Only source facts support warmth; plain quilting/padding names do not."""
    def positive(pattern, text):
        compact = re.sub(r"\s+", "", text)
        for match in re.finditer(pattern, compact, re.I):
            if match.start() and compact[match.start()-1] == "무":continue
            following = re.split(r"[.!?\n,;]", compact[match.end():], maxsplit=1)[0][:24]
            if re.search(r"없|아님|아닌|아니|않|미포함|제외|생략|^X", following, re.I):continue
            return True
        return False
    if positive(r"기모|울혼방|(?:퀼팅|패딩)[^.!?]{0,15}충전재|충전재[^.!?]{0,15}(?:퀼팅|패딩)", evidence):return True
    names = [str(m.get("name", "")) for m in materials if m.get("ratio", 1) != 0]
    return any(positive(r"울|양모|캐시미어|wool|cashmere", name) for name in names)


def size_advice(text: str, fit: str, *, faq_answer: bool) -> bool:
    """Allow only optional one-size-down FAQ advice backed by an oversize fit."""
    compact = re.sub(r"\s+", "", text)
    counts = [re.sub(r"\s+", "", value) for value in
              re.findall(r"(?<![가-힣\d])(?:한|두|세|네|\d+)\s*(?:치수|사이즈)", text)]
    advice = counts or re.search(r"정사이즈|사이즈(?:업|다운)|(?:큰|작은)사이즈|크게나와|작게나와|평소(?:입으시는|입던|보다|입는)?사이즈.{0,10}(?:고르|골라|선택|추천)", compact)
    if not advice:
        return False
    oversize = re.sub(r"[\s_-]+", "", str(fit)).lower() in {"over", "semiover", "오버핏", "세미오버핏"}
    down = re.search(r"한(?:사이즈|치수)작게", compact)
    chart = re.search(r"실측표|상세치수|사이즈표", compact)
    soft = re.search(r"골라도|고르셔도|선택해도|추천|권해|권합니다", compact)
    forbidden = (any(count not in ("한사이즈", "한치수") for count in counts) or
                 re.search(r"꼭|무조건|반드시|정사이즈|크게|큰사이즈|작은사이즈|사이즈업|작게나와", compact))
    return not bool(faq_answer and oversize and down and chart and soft and not forbidden)
