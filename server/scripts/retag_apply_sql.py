"""검수 완료된 재태깅 제안으로 적용 SQL을 생성한다(데이터베이스 실행은 하지 않음).

    cd server
    .venv/bin/python scripts/retag_apply_sql.py
    .venv/bin/python scripts/retag_apply_sql.py --seed  # 시드 정본도 동기화

정확히 60개의 유효한 제안만 받아 ``scripts/retag/apply.sql``을 생성한다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
PROPOSALS_PATH = SERVER_DIR / "scripts" / "retag" / "proposals.json"
SQL_PATH = SERVER_DIR / "scripts" / "retag" / "apply.sql"
SEED_PATH = SERVER_DIR / "seed" / "matching_items.json"
EXPECTED_COUNT = 60

sys.path.insert(0, str(SERVER_DIR))

from app.agents.style_tags import STYLE_TAG_SET  # noqa: E402


def _load_list(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"파일이 없습니다: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 형식 오류: {path}: {exc}") from exc
    if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
        raise ValueError(f"객체 배열이어야 합니다: {path}")
    return data


def validate_proposals(proposals: list[dict]) -> list[dict]:
    if len(proposals) != EXPECTED_COUNT:
        raise ValueError(f"제안이 정확히 {EXPECTED_COUNT}건이어야 합니다: 현재 {len(proposals)}건")
    seen: set[str] = set()
    for proposal in proposals:
        item_id = proposal.get("id")
        tags = proposal.get("proposed_tags")
        if not isinstance(item_id, str) or not item_id:
            raise ValueError("유효하지 않은 제안 id가 있습니다.")
        if item_id in seen:
            raise ValueError(f"중복 제안 id: {item_id}")
        seen.add(item_id)
        if (
            not isinstance(tags, list)
            or not 2 <= len(tags) <= 4
            or len(tags) != len(set(tags))
            or not all(isinstance(tag, str) and tag in STYLE_TAG_SET for tag in tags)
        ):
            raise ValueError(f"{item_id}: 유효하지 않은 proposed_tags: {tags!r}")
    return proposals


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def render_sql(proposals: list[dict]) -> str:
    validated = validate_proposals(proposals)
    lines = [
        "-- AI 재태깅 제안 검수 후 수동 실행. 이 파일을 생성한 스크립트는 DB에 접속하지 않습니다.",
        "BEGIN;",
        "",
    ]
    # 큐레이션 행만 갱신한다 — 사용자 개별 등록(is_custom)은 owner_user_id/project_id 로
    # 구분되므로(마이그레이션 20260807000000), id 가 우연히 겹쳐도 건드리지 않는다.
    guard = "owner_user_id IS NULL AND project_id IS NULL"
    for proposal in validated:
        tags_json = json.dumps(proposal["proposed_tags"], ensure_ascii=False, separators=(",", ":"))
        lines.extend(
            [
                "UPDATE matching_items "
                f"SET style_tags = {_sql_literal(tags_json)}::jsonb "
                f"WHERE id = {_sql_literal(proposal['id'])} AND {guard};",
                "",
            ]
        )

    # 검증 실패가 COMMIT 을 막아야 한다 — SELECT 는 사람이 놓칠 수 있으니
    # DO 블록에서 (id, 기대 태그) 완전 일치 행 수를 세어 다르면 예외로 트랜잭션을 중단한다.
    checks = ",\n    ".join(
        f"({_sql_literal(p['id'])}, {_sql_literal(json.dumps(p['proposed_tags'], ensure_ascii=False, separators=(',', ':')))}::jsonb)"
        for p in validated
    )
    lines.extend(
        [
            f"-- 적용 검증: 기대 태그와 완전 일치하는 큐레이션 행이 {EXPECTED_COUNT}건이 아니면 예외로 롤백된다.",
            "DO $$",
            "DECLARE matched integer;",
            "BEGIN",
            "  SELECT COUNT(*) INTO matched",
            "  FROM matching_items mi",
            "  JOIN (VALUES",
            f"    {checks}",
            "  ) AS expected(id, tags) ON expected.id = mi.id",
            "  WHERE mi.style_tags = expected.tags AND mi.owner_user_id IS NULL AND mi.project_id IS NULL;",
            f"  IF matched <> {EXPECTED_COUNT} THEN",
            f"    RAISE EXCEPTION '재태깅 검증 실패: 기대 {EXPECTED_COUNT}건, 실제 %건 — 트랜잭션을 롤백합니다', matched;",
            "  END IF;",
            "END $$;",
            "",
            "COMMIT;",
            "",
        ]
    )
    return "\n".join(lines)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def sync_seed(proposals: list[dict], seed_path: Path) -> bool:
    """존재하는 시드의 styleTags/style_tags만 제안값으로 교체한다."""
    if not seed_path.exists():
        return False
    seed = _load_list(seed_path)
    by_id: dict[str, dict] = {}
    for item in seed:
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id:
            raise ValueError("시드에 유효하지 않은 id가 있습니다.")
        if item_id in by_id:
            raise ValueError(f"시드 중복 id: {item_id}")
        by_id[item_id] = item

    proposal_ids = {proposal["id"] for proposal in proposals}
    missing = proposal_ids - set(by_id)
    if missing:
        raise ValueError(f"시드에 없는 제안 id: {sorted(missing)}")
    for proposal in proposals:
        item = by_id[proposal["id"]]
        field = "style_tags" if "style_tags" in item and "styleTags" not in item else "styleTags"
        item[field] = list(proposal["proposed_tags"])
    _atomic_write(seed_path, json.dumps(seed, ensure_ascii=False, indent=2) + "\n")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposals", type=Path, default=PROPOSALS_PATH)
    parser.add_argument("--output", type=Path, default=SQL_PATH)
    parser.add_argument("--seed", action="store_true", help="존재하는 시드 JSON도 함께 동기화")
    parser.add_argument("--seed-path", type=Path, default=SEED_PATH)
    args = parser.parse_args()
    try:
        proposals = validate_proposals(_load_list(args.proposals))
        _atomic_write(args.output, render_sql(proposals))
        print(f"생성: {args.output} ({len(proposals)} UPDATE)")
        if args.seed:
            if sync_seed(proposals, args.seed_path):
                print(f"시드 동기화: {args.seed_path}")
            else:
                print(f"시드 없음, 건너뜀: {args.seed_path}")
    except (OSError, ValueError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
