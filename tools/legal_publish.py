"""documents/legal/*.md → public/legal/*.md 공개본 생성.
내부 메모(문서 성격 블록·채울 값 표·변호사 검토 포인트·개정 메모)를 걷어내고 자리표시자를 채운 뒤
문서 간 링크를 라우트로 바꾼다. 남은 [대괄호]가 있으면 목록으로 출력한다(공개 차단 신호).
실행: python3 tools/legal_publish.py [--landing-root /path/to/landing_page_fasho]
"""
import argparse, json, re, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC, OUT = ROOT / "documents/legal", ROOT / "public/legal"
OUT.mkdir(parents=True, exist_ok=True)
EFFECTIVE_DATE = "2026-09-11"
year, month, day = (int(part) for part in EFFECTIVE_DATE.split("-"))
EFFECTIVE = f"{year}년 {month}월 {day}일"
COMPANY = json.loads((ROOT / "src/lib/companyInfo.json").read_text())
CO = dict(name=COMPANY["name"], ceo=COMPANY["representative"], brn=COMPANY["businessRegistrationNumber"],
          addr=COMPANY["address"], tel=COMPANY["phone"], email=COMPANY["email"])
# (파일, 슬러그, 앱, 제목)
DOCS = [
    ("10_wearless_terms_of_service_v1.md", "terms-seller", "seller", "Wearless 이용약관"),
    ("11_wearless_privacy_policy_v1.md", "privacy-seller", "seller", "Wearless 개인정보 처리방침"),
    ("12_wearless_refund_policy_v1.md", "refund", "seller", "Wearless 환불 정책"),
    ("05_facemarket_seller_license_terms_v1.md", "seller-license-terms", "both", "FaceMarket 셀러 라이선스 이용조건"),
    ("01_facemarket_terms_of_service_model_v1.md", "terms-model", "facemarket", "FaceMarket 모델 이용약관"),
    ("02_facemarket_likeness_license_agreement_v1.md", "license-agreement", "facemarket", "FaceMarket 초상 라이선스 표준계약서"),
    ("03_facemarket_privacy_policy_v1.md", "privacy-model", "facemarket", "FaceMarket 개인정보 처리방침"),
    ("06_facemarket_legal_faq_v1.md", "answers", "facemarket", "FaceMarket 법적 FAQ"),
]
SELLER, FM = "https://wearless.kr", "https://facemarket.wearless.kr"
LANDING_DOCUMENTS = {
    "terms-seller": ("/terms", "이용약관"),
    "privacy-seller": ("/privacy", "개인정보 처리방침"),
    "refund": ("/refund", "환불 정책"),
    "seller-license-terms": ("/model-license-terms", "모델 라이선스 이용조건"),
}
# 원본 파일 접두 → (셀러 앱에서의 경로, 페이스마켓 앱에서의 경로)
ROUTES = {"10_": ("/terms", f"{SELLER}/terms"), "11_": ("/privacy", f"{SELLER}/privacy"), "12_": ("/refund", f"{SELLER}/refund"),
          "05_": ("/model-license-terms", "/seller-terms"), "01_": (f"{FM}/terms", "/terms"), "02_": (f"{FM}/license-agreement", "/license-agreement"),
          "03_": (f"{FM}/privacy", "/privacy"), "06_": (f"{FM}/answers", "/answers"), "00_": (None, None), "04_": (None, None)}
DROP_HEADINGS = [r"^##+ 회사에서 채워야 하는 값", r"^##+ 회사가 채워야 하는 값", r"^##+ 변호사 검토", r"^##+ 초기 화면 하단에 게시",
                 r"^##+ 계약 요지 \(모델 화면", r"^##+ 셀러 화면에 표시하는 요지", r"^##+ 부록\. `llms\.txt`"]
SUBS = [
    (r"\[회사명\]", CO["name"]), (r"\[시행일\]", EFFECTIVE), (r"\[대표자\]", CO["ceo"]), (r"\[주소\]", CO["addr"]),
    (r"\[CPO 성명\]", COMPANY["privacyOfficer"]), (r"\[CPO 직책\]", "대표(CEO)"), (r"\[CPO 전화\]", CO["tel"]), (r"\[CPO 이메일\]", CO["email"]),
    (r"\[이메일\]", CO["email"]), (r"\[부서명 — 예: 모델 지원팀\]", "고객지원"), (r"\[부서 이메일\]", CO["email"]), (r"\[평일 10:00–18:00\]", "평일 10:00–18:00"),
    (r"\[본인확인기관명\]", "통신사 본인확인 서비스(CX 표준인증창 운영사)"),
    (r"\[모바일 신분증 검증 서비스 제공자\]", "정부 모바일 신분증 검증 연동 사업자(확정 시 갱신)"),
    (r"\[N\]장", "회사가 안내한 장수의"), (r"\[등 N장\]", "등 회사가 안내한 장수"),
    (r"\[배분 비율: 오너 확정\]", "배분 비율은 모델 몫과 같은 70%로 한다."),
    (r"\s*\[오너 확정[^\]]*\]", ""), (r"\[v2\]", "(다음 버전에서 제공)"), (r"\[현재 미사용\]", "(현재 미사용)"),
    (r"\[최소 지급액 1만원\.\]", "최소 지급액 1만원."), (r"\[사용료의 70%\]", "사용료의 70%"),
    (r"\[(10만원|100만원|50만원|70%|20%|10%|10일|6개월|2\.5배|10,000원|5,000원|30일|1만원|20,000원|10명|3일|1년|48시간)\]", r"\1"),
    (r"\[\[?사업소득 3\.3% / 기타소득 — 확정 후 기재\]\]?", "사업소득 3.3%"),
    (r"\[· 주민등록번호 — [^\]]+\]", "· 주민등록번호 — 소득세법 제145조에 따른 원천징수 신고를 위해 법령상 수집(사업소득 3.3% 원천징수)"),
    (r"\[날짜\]", EFFECTIVE),
    (r"\[(설정 > [^\]]+|Digital DNA 관리 → [^\]]+|정산 → 내역)\]", r"「\1」"),  # UI 경로 표기
]
def strip_head_block(lines):
    """H1 다음의 '> ' 메모 블록과 뒤따르는 --- 제거."""
    out, i = [], 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("# ") and not out:
            out.append(re.sub(r"\s*\(v1 초안\).*$", "", ln)); i += 1
            while i < len(lines) and (lines[i].startswith(">") or lines[i].strip() == ""): i += 1
            while i < len(lines) and lines[i].strip() in ("---", ""): i += 1
            continue
        out.append(ln); i += 1
    return out
def drop_sections(lines):
    out, skip_level, in_fence = [], None, False
    for ln in lines:
        if ln.strip().startswith("```"):
            in_fence = not in_fence
            if not skip_level: out.append(ln)
            continue
        m = None if in_fence else re.match(r"^(#+) ", ln)  # 코드펜스 안의 '#'은 제목이 아니다
        if m:
            lvl = len(m.group(1))
            if skip_level and lvl <= skip_level: skip_level = None
            if any(re.match(p, ln) for p in DROP_HEADINGS): skip_level = lvl; continue
        if skip_level: continue
        out.append(ln)
    return out
def drop_revision_notes(text):
    return re.sub(r"(?m)^> \*\*2026-09-07 개정\*\*.*(?:\n>.*)*\n?", "", text)
def rewrite_links(text, app):
    def repl(m):
        key = m.group(1)
        seller_path, fm_path = ROUTES.get(key, (None, None))
        target = seller_path if app == "seller" else fm_path
        if app == "both":  # 두 앱에서 같은 파일을 서빙하므로 앱 밖 문서는 절대 주소로
            if key in ("10_", "11_", "12_"): target = f"{SELLER}{seller_path}"
            elif fm_path: target = fm_path if fm_path.startswith("http") else f"{FM}{fm_path}"
            else: target = None
        return f"]({target})" if target else "]"
    text = re.sub(r"\]\((0\d_|1\d_)[a-z_]+_v1\.md(?:#[^)]*)?\)", repl, text)
    return re.sub(r"\[([^\]]+)\]\]", r"\1", text)  # 대상 없는 링크 → 텍스트
def broken_bold(text):
    """마크다운 표준(CommonMark)에서 강조로 인식되지 않는 ** 를 찾는다.
    닫는 ** 앞이 문장부호(따옴표·괄호·링크 닫힘)이고 바로 뒤에 조사 같은 글자가 붙으면
    렌더러(marked)가 ** 를 글자 그대로 내보낸다. 예: **"서비스"**란 → "**서비스**"란 으로 쓴다.
    2026-09-11 실서버 이용약관에서 12곳이 그대로 노출돼 추가했다."""
    import unicodedata
    punct = lambda c: unicodedata.category(c).startswith("P")
    found = []
    for m in re.finditer(r"\*\*([^*\n]+?)\*\*", text):
        inner, after = m.group(1), text[m.end():m.end() + 1]
        before = text[m.start() - 1] if m.start() else ""
        close_fail = punct(inner[-1]) and after and not after.isspace() and not punct(after)
        open_fail = punct(inner[0]) and before and not before.isspace() and not punct(before)
        if close_fail or open_fail: found.append(m.group(0) + after)
    return found

def publish():
    manifest, leftovers = [], {}
    for fn, slug, app, title in DOCS:
        text = (SRC / fn).read_text()
        lines = drop_sections(strip_head_block(text.splitlines()))
        text = "\n".join(lines)
        text = drop_revision_notes(text)
        for pat, rep in SUBS: text = re.sub(pat, rep, text)
        text = rewrite_links(text, app)
        text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
        text = re.sub(r"(?m)^---\s*\n(---\s*\n)+", "---\n", text)
        text = re.sub(r"\n---\s*$", "\n", text).rstrip() + "\n"  # 문서 끝 구분선·빈 줄 제거
        left = sorted(set(re.findall(r"\[[^\]\n]{1,40}\](?!\()", text)))
        if left: leftovers[slug] = left
        bold = broken_bold(text)
        if bold: leftovers[slug] = leftovers.get(slug, []) + [f"굵게 미인식 {b}" for b in bold]
        (OUT / f"{slug}.md").write_text(text)
        manifest.append({"slug": slug, "app": app, "title": title, "version": "v1.1", "effectiveDate": EFFECTIVE_DATE, "source": fn})
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    # llms.txt — 06 부록 코드블록
    faq = (SRC / "06_facemarket_legal_faq_v1.md").read_text()
    m = re.search(r"## 부록\. `llms\.txt` 초안.*?```\n(.*?)```", faq, re.S)
    if m:
        llms = m.group(1)
        for pat, rep in SUBS: llms = re.sub(pat, rep, llms)
        (ROOT / "public/llms.txt").write_text(llms)
    return manifest, leftovers

def export_landing(landing_root, manifest):
    """랜딩은 커밋된 공개본으로 빌드한다. 앱 서버에 런타임 의존하지 않는다."""
    destination = landing_root / "content/legal"
    destination.mkdir(parents=True, exist_ok=True)
    documents = []
    for document in manifest:
        slug = document["slug"]
        if slug not in LANDING_DOCUMENTS:
            continue
        path, label = LANDING_DOCUMENTS[slug]
        documents.append({**document, "path": path, "label": label, "canonicalUrl": f"{SELLER}{path}"})
        (destination / f"{slug}.md").write_text((OUT / f"{slug}.md").read_text())
    for filename, content in (("manifest.json", documents), ("company-info.json", COMPANY)):
        (destination / filename).write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--landing-root", type=pathlib.Path, help="랜딩 저장소 경로. Wearless 공개본 4종과 사업자 정보를 내보냅니다.")
    args = parser.parse_args()
    if args.landing_root and not (args.landing_root / "package.json").is_file():
        parser.error("--landing-root에는 package.json이 있는 랜딩 저장소를 지정하세요.")
    manifest, leftovers = publish()
    print(f"published {len(manifest)} docs → public/legal/ (+ public/llms.txt)")
    for d in manifest: print(f"  {d['slug']:22s} {d['app']:10s} {d['title']}")
    if leftovers:
        print("\n남은 자리표시자·미인식 강조 (공개 전 해결):")
        for k, v in leftovers.items(): print(f"  {k}: {', '.join(v)}")
        sys.exit(1)
    if args.landing_root:
        export_landing(args.landing_root, manifest)
        print(f"exported {len(LANDING_DOCUMENTS)} Wearless docs → {args.landing_root / 'content/legal'}")
