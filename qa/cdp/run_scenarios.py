"""검수 게이트 브라우저 QA — CDP 자동화 (Phase 3 P0-C 9/N).

스크린샷만으로 "전부 통과"를 주장하지 않기 위해, 시나리오마다 클릭하고 상태를 읽어
구조화 결과(JSON)로 남긴다. 자동으로 못 재는 것은 manual 로 표시하고 통과로 세지 않는다.

전제: 깨끗한 프로필 Chrome + dev 서버.

    npm run dev
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \\
      --user-data-dir=/tmp/chrome-qa --no-first-run --remote-debugging-port=9333 \\
      http://localhost:5173/qa-review-gate.html
    python qa/cdp/run_scenarios.py --out qa/cdp/results.json
"""

from __future__ import annotations

import argparse
import base64
import json
import pathlib
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from cdp_client import CDP, page_ws  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
SHOTS = ROOT / "docs" / "shadow"
RESULTS: list[dict] = []
C: CDP | None = None


def js(e):
    return C.js(e)


def wait(ms=450):
    time.sleep(ms / 1000)


def reconnect():
    global C
    C = CDP(page_ws())


def reload_page():
    C.call("Page.enable")
    C.call("Page.reload", ignoreCache=True)
    time.sleep(2.2)
    reconnect()


def record(sid, name, passed, detail="", kind="automated", shot=None):
    RESULTS.append({"scenarioId": sid, "name": name, "kind": kind,
                    "result": "pass" if passed else "fail", "detail": str(detail),
                    "screenshot": shot})
    print(f"{'PASS' if passed else 'FAIL'} {sid} {name}"
          + (f"  {detail}" if detail else ""))


def shot(name):
    r = C.call("Page.captureScreenshot", format="png")
    SHOTS.mkdir(parents=True, exist_ok=True)
    (SHOTS / name).write_bytes(base64.b64decode(r["data"]))
    return f"docs/shadow/{name}"


# ── 화면 조작 헬퍼 ──────────────────────────────────────────────────────────
def mode(m): js(f'document.querySelector("[data-qa=mode-{m}]").click()')
def canvas(): return js('+document.querySelector("[data-qa=canvas-count]").textContent')
def apic(): return js('+document.querySelector("[data-qa=api-count]").textContent')
def overlays(): return js('document.querySelectorAll(".overlay").length')
def logtext(): return js('JSON.stringify([...document.querySelectorAll("[data-qa=log] li")].map(e=>e.textContent))')
def ward(i): js(f'document.querySelectorAll(".ward-cell")[{i}].click()')
def btn(t): return js(f'(()=>{{const b=[...document.querySelectorAll(".overlay button")].find(x=>x.textContent.trim()==="{t}");if(b)b.click();return !!b;}})()')
def slots(): return js('[...document.querySelectorAll(".overlay button")].filter(b=>b.title==="사진 넣기"||b.title==="사진 바꾸기").length')
def click_slot(i): js(f'[...document.querySelectorAll(".overlay button")].filter(b=>b.title==="사진 넣기"||b.title==="사진 바꾸기")[{i}].click()')
def filled(): return js('[...document.querySelectorAll(".overlay button")].filter(b=>(b.title==="사진 넣기"||b.title==="사진 바꾸기")&&b.querySelector("img")).length')
def picks(): return js('[...document.querySelectorAll(".overlay button")].filter(b=>b.querySelector("img")&&b.title!=="사진 바꾸기").length')
def pick(i): js(f'[...document.querySelectorAll(".overlay button")].filter(b=>b.querySelector("img")&&b.title!=="사진 바꾸기")[{i}].click()')


# 셀 순서: 0 plain · 1 pass · 2 unreviewed · 3 accepted · 4 rejected · 5 nosource · 6 long · 7 broken
def group_a():
    reload_page(); mode("ok")
    record("A1", "plain 즉시 삽입", (ward(0), wait(), canvas() == 1 and apic() == 0)[-1])
    record("A2", "pass 즉시 삽입", (ward(1), wait(), canvas() == 2 and apic() == 0)[-1])
    record("A3", "accepted 즉시 삽입·API 0", (ward(3), wait(), canvas() == 3 and apic() == 0)[-1])
    ward(2); wait()
    record("A4", "미검수 → 비교 모달·삽입 0", overlays() == 1 and canvas() == 3,
           shot=shot("qa-02-review-dialog.png"))
    record("A5", "모달에 원본+결과 2장",
           js('document.querySelectorAll(".overlay figure img").length') == 2)
    btn("닫기"); wait()
    record("A6", "닫기 = 반영 0", overlays() == 0 and canvas() == 3 and apic() == 0)
    ward(4); wait()
    record("A7", "rejected 도 모달 재표시", overlays() == 1 and canvas() == 3)
    btn("확인 후 사용"); wait(700)
    record("A8", "rejected→accepted 후 삽입", canvas() == 4 and apic() == 1)
    record("A9", "배지가 확인함으로 갱신",
           js('[...document.querySelectorAll(".ward-qc")].some(e=>e.className.includes("accepted"))'),
           shot=shot("qa-01-wardrobe-badges.png"))


def group_fail():
    reload_page(); mode("fail"); ward(2); wait(); btn("확인 후 사용"); wait(700)
    record("F1", "기록 실패 → 삽입 0·모달 유지", canvas() == 0 and overlays() == 1)
    mode("ok"); btn("확인 후 사용"); wait(700)
    record("F2", "재시도 성공 → 삽입 1", canvas() == 1)
    reload_page(); mode("slow"); ward(2); wait()
    js('(()=>{const b=[...document.querySelectorAll(".overlay button")].find(x=>x.textContent.trim()==="확인 후 사용");b.click();b.click();b.click();})()')
    wait(300)
    record("F3", "기록 중 버튼 비활성",
           js('[...document.querySelectorAll(".overlay button")].every(b=>b.disabled)'))
    time.sleep(2.2)
    record("F4", "중복 클릭 → API 1회·삽입 1회", apic() == 1 and canvas() == 1)


def group_b():
    reload_page(); mode("ok")
    js('document.querySelector("[data-qa=open-feature]").click()'); wait()
    record("B1", "FeatureIconsForm 슬롯 3개", slots() == 3)
    click_slot(0); wait()
    record("B2", "PhotoPicker 열림", picks() > 0)
    pick(2); wait(700)
    record("B3", "미검수 → 폼 미반영", filled() == 0)
    record("B4", "검수 모달이 폼 위에", overlays() >= 2 and js(
        '(()=>{const o=[...document.querySelectorAll(".overlay")];return o[o.length-1].textContent.includes("편집 결과를 확인");})()'))
    btn("확인 후 사용"); wait(800)
    record("B5", "승인 후 슬롯 0에만 반영", filled() == 1 and apic() == 1)
    click_slot(2); wait(); pick(4); wait(700)
    record("B6", "rejected 도 모달", overlays() >= 2)
    btn("확인 후 사용"); wait(800)
    record("B7", "마지막 슬롯 반영", filled() == 2 and apic() == 2)
    mode("fail"); click_slot(1); wait(); pick(5); wait(700)
    btn("확인 후 사용"); wait(800)
    record("B8", "기록 실패 → 폼 미반영", filled() == 2 and overlays() >= 2)
    btn("닫기"); wait()
    record("B9", "닫기 = 반영 0", filled() == 2)
    reload_page(); mode("slow")
    js('document.querySelector("[data-qa=open-feature]").click()'); wait()
    click_slot(0); wait(); pick(2); wait(400); btn("확인 후 사용"); wait(300)
    js('(()=>{const b=[...document.querySelectorAll(".overlay button")].find(x=>x.textContent.trim()==="취소");if(b)b.click();})()')
    time.sleep(2.2)
    record("B10", "검수 중 폼 닫힘 → 반영 0·크래시 없음",
           js('!!document.querySelector("[data-qa=reset]")') and overlays() == 0)
    reload_page(); mode("ok")
    js('document.querySelector("[data-qa=open-model]").click()'); wait()
    record("B11", "ModelInfoForm 슬롯 렌더", slots() == 1)
    click_slot(0); wait(); pick(2); wait(700)
    record("B12", "모델: 미검수 미반영", filled() == 0)
    btn("확인 후 사용"); wait(800)
    record("B13", "모델: 승인 후 반영", filled() == 1)


def group_c():
    reload_page(); mode("ok")
    js('document.querySelector("[data-qa=pending-slot]").click()'); wait()
    ward(2); wait()
    record("C1", "pendingSlot 도 게이트 통과", overlays() == 1 and canvas() == 0)
    btn("확인 후 사용"); wait(700)
    record("C2", "승인 후 목적 보존(SLOT)", "SLOT ←" in logtext())
    reload_page(); mode("ok"); ward(2); wait(); btn("확인 후 사용"); wait(700)
    record("C3", "일반 캔버스 경로", "CANVAS ←" in logtext() and canvas() == 1)
    reload_page(); mode("slow"); ward(2); wait(); btn("확인 후 사용"); wait(200)
    record("C4", "기록 중 오버레이가 뒤 클릭 차단", overlays() == 1)
    time.sleep(2.2)
    record("C5", "첫 승인만 삽입", canvas() == 1)
    ward(5); wait()
    record("C6", "다음 검수 모달", overlays() == 1)
    btn("닫기"); wait()
    record("C7", "취소 후 삽입 없음", canvas() == 1)


def group_d():
    reload_page(); mode("ok")
    ward(5); wait(700)
    record("D1", "sourceSrc 누락 fallback",
           js('document.querySelector(".overlay").textContent.includes("원본을 불러올 수 없어요")'))
    record("D2", "결과 이미지 1장 렌더",
           js('document.querySelectorAll(".overlay figure img").length') == 1)
    btn("닫기"); wait(); ward(6); wait(700)
    record("D3", "긴 사유에도 가로 스크롤 없음",
           js('document.documentElement.scrollWidth <= window.innerWidth + 1'),
           shot=shot("qa-03-long-reasons.png"))
    record("D4", "위반/변경 목록 표시",
           js('document.querySelector(".overlay").textContent.includes("바뀌면 안 되는")'))
    btn("닫기"); wait(); ward(7); wait(900)
    record("D5", "깨진 이미지에도 모달 유지", overlays() == 1)
    btn("닫기"); wait()
    C.call("Emulation.setDeviceMetricsOverride", width=390, height=780,
           deviceScaleFactor=2, mobile=True)
    wait(400); ward(2); wait(700)
    record("D6", "390px 가로 스크롤 없음",
           js('document.documentElement.scrollWidth <= window.innerWidth + 1'),
           shot=shot("qa-04-narrow-390.png"))
    cols = js('getComputedStyle(document.querySelector(".overlay .modal div[style*=grid]")).gridTemplateColumns')
    record("D7", "비교 2열 유지", len(cols.split()) == 2, cols)
    record("D8", "좁은 화면에서도 두 이미지 보임",
           js('[...document.querySelectorAll(".overlay figure img")].every(i=>i.getBoundingClientRect().width>40)'))
    C.call("Emulation.clearDeviceMetricsOverride")
    reload_page(); mode("ok"); ward(2); wait(700)
    js('[...document.querySelectorAll(".overlay button")].pop().focus()')
    record("D9", "모달 버튼 focus 가능",
           js('document.activeElement.closest(".overlay") !== null'))
    js('window.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true}))')
    wait(400)
    record("D10", "ESC 로 모달 닫힘", overlays() == 0 and canvas() == 0)
    # 자동으로 못 재는 것 — 통과로 세지 않는다.
    record("M1", "색약 사용자가 배지 3종을 구분하는가", False, kind="manual",
           detail="색 외에 아이콘·텍스트가 함께 있으나 실사용자 확인 필요")
    record("M2", "스크린리더 낭독 순서", False, kind="manual",
           detail="미검증 — 모달에 role/aria 미지정")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "qa" / "cdp" / "results.json"))
    ap.add_argument("--cdp", default="http://localhost:9333")
    a = ap.parse_args()
    reconnect()
    ver = json.load(urllib.request.urlopen(a.cdp + "/json/version"))
    started = time.time()
    for g in (group_a, group_fail, group_b, group_c, group_d):
        g()
    auto = [r for r in RESULTS if r["kind"] == "automated"]
    out = {
        "startedAt": started,
        "finishedAt": time.time(),
        "commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT),
                                 capture_output=True, text=True).stdout.strip(),
        "browser": ver.get("Browser"),
        "userAgent": ver.get("User-Agent"),
        "viewport": {"default": js("window.innerWidth + 'x' + window.innerHeight"),
                     "narrowTested": "390x780"},
        "harnessUrl": js("location.href"),
        "totals": {"automated": len(auto),
                   "automatedPass": sum(1 for r in auto if r["result"] == "pass"),
                   "manualUnverified": sum(1 for r in RESULTS if r["kind"] == "manual")},
        "scenarios": RESULTS,
    }
    pathlib.Path(a.out).write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    t = out["totals"]
    print(f"\n자동 {t['automatedPass']}/{t['automated']} pass · manual 미검증 "
          f"{t['manualUnverified']}건 → {a.out}")
    return 0 if t["automatedPass"] == t["automated"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
