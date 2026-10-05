"""고정 패션 입력으로 페이지 카피 품질을 실측해 마크다운 보고서를 만든다.

실행:
    cd server && .venv/bin/python -m scripts.eval_copy

실제 모델 호출은 ``main``에서만 일어난다. 테스트는 ``evaluate_cases``에 공급자를 주입한다.
"""

import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path

from scripts._env import load_env

from app.agents import copywriter
from app.config import load_settings


_SERVER_DIR = Path(__file__).resolve().parents[1]
_REPO_DIR = _SERVER_DIR.parent
_FIXTURE = _SERVER_DIR / "tests" / "fixtures" / "copy_eval_cases.json"


def load_cases(path: Path | str = _FIXTURE) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


async def evaluate_cases(cases: list[dict], settings, *, generator=copywriter.generate) -> list[dict]:
    rows = []
    for case in cases:
        results = await generator(
            settings,
            blocks=case["blocks"],
            product=case["product"],
            analysis=case["analysis"],
        )
        by_id = {
            str(item.get("blockId")): (item.get("texts") or [{}])[0].get("text", "")
            for item in results or []
            if item.get("blockId") is not None
        }
        for block in copywriter.eligible_blocks(case["blocks"]):
            block_id = str(block["id"])
            headline = by_id.get(block_id, "")
            violations = copywriter.headline_violations(headline) if headline else ["빈 결과"]
            rows.append({
                "category": case["category"],
                "caseId": case["caseId"],
                "blockId": block_id,
                "headline": headline,
                "violations": violations,
            })
    return rows


def _cell(value) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def render_report(rows: list[dict]) -> str:
    violation_count = sum(len(row["violations"]) for row in rows)
    lines = [
        "# AI 카피 품질 평가",
        "",
        f"규칙 위반 수: {violation_count}",
        "",
        "| 종류 | 입력 | 사진 | 소제목 | 위반 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        violations = ", ".join(row["violations"]) or "없음"
        lines.append(
            f"| {_cell(row['category'])} | {_cell(row['caseId'])} | {_cell(row['blockId'])} "
            f"| {_cell(row['headline']) or '(빈 값)'} | {_cell(violations)} |"
        )
    return "\n".join(lines) + "\n"


async def _run(args) -> Path:
    settings = load_settings()
    rows = await evaluate_cases(load_cases(args.fixture), settings)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    output_path.write_text(render_report(rows), encoding="utf-8")
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", default=str(_FIXTURE))
    parser.add_argument("--output-dir", default=str(_REPO_DIR / ".scratch" / "copy-eval"))
    args = parser.parse_args()
    load_env()
    output_path = asyncio.run(_run(args))
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
