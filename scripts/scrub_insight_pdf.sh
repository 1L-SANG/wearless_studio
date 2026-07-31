#!/usr/bin/env bash
# 유료 전자책 PDF만 git 이력에서 제거한다 (insight/*.json 8개는 이력에 유지).
# 대상: feat/orb-motion-fx 브랜치의 오염 구간(2a59bca 이후 first-parent 체인)만 재작성.
# 작성자·날짜·메시지 전부 보존, 실행 전 백업 브랜치 자동 생성. push는 하지 않는다.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
trap 'echo ""; echo "❌ 실패(라인 $LINENO): 중간 오류로 멈췄습니다. 이력은 아무것도 변경되지 않았습니다."' ERR
echo "== 이력 청소 시작 =="

BRANCH=feat/orb-motion-fx
BASE=bb7d58a          # 이 커밋까지는 깨끗함 — 재작성하지 않는 경계
TAINT_MERGE=2a59bca   # PDF가 실수로 들어간 병합 커밋

[ "$(git rev-parse --abbrev-ref HEAD)" = "$BRANCH" ] || { echo "중단: $BRANCH 브랜치에서 실행하세요"; exit 1; }
git diff --quiet --cached || { echo "중단: 스테이징된 변경이 있습니다. 먼저 커밋/해제하세요"; exit 1; }

# 저장된 실제 경로명을 git에서 직접 얻는다 (macOS 한글 정규화 차이 회피)
# core.quotepath=off — 한글 경로를 따옴표·이스케이프 없이 날것으로 받아야 grep과 제거가 정확히 맞는다
PDF="$(git -c core.quotepath=off ls-tree --name-only "$TAINT_MERGE" insight/ | grep '\.pdf$')"
[ -n "$PDF" ] && echo "제거 대상: $PDF" || { echo "중단: 대상 PDF를 찾지 못함"; exit 1; }

IDX="${TMPDIR:-/tmp}/scrub_index_$$"; rm -f "$IDX"
newtree() {  # 커밋 $1 의 트리에서 PDF 경로만 뺀 새 트리
  GIT_INDEX_FILE="$IDX" git read-tree "$1"
  GIT_INDEX_FILE="$IDX" git update-index --force-remove "$PDF" 2>/dev/null || true
  GIT_INDEX_FILE="$IDX" git write-tree
  rm -f "$IDX"
}

prev="" ; merge_new=""
for c in $(git rev-list --reverse --first-parent "$BASE..HEAD"); do
  t=$(newtree "$c")
  parents=() ; first=1
  for p in $(git rev-parse "$c^@"); do
    if [ $first -eq 1 ]; then parents+=(-p "${prev:-$p}"); first=0
    else parents+=(-p "$p"); fi
  done
  new=$(git log -1 --format=%B "$c" | \
    GIT_AUTHOR_NAME="$(git log -1 --format=%an "$c")" \
    GIT_AUTHOR_EMAIL="$(git log -1 --format=%ae "$c")" \
    GIT_AUTHOR_DATE="$(git log -1 --format=%aI "$c")" \
    GIT_COMMITTER_NAME="$(git log -1 --format=%cn "$c")" \
    GIT_COMMITTER_EMAIL="$(git log -1 --format=%ce "$c")" \
    GIT_COMMITTER_DATE="$(git log -1 --format=%cI "$c")" \
    git commit-tree "$t" "${parents[@]}")
  [ "$c" = "$(git rev-parse "$TAINT_MERGE")" ] && merge_new="$new"
  prev="$new"
done

old="$(git rev-parse HEAD)"
git branch "backup/pre-pdf-scrub" "$old" 2>/dev/null || echo "(백업 브랜치가 이미 있음 — 기존 것 유지)"
git update-ref -m "scrub: 유료 PDF만 이력에서 제거" "refs/heads/$BRANCH" "$prev" "$old"

echo ""
echo "===== 검증 ====="
echo "① 최근 커밋(메시지 동일·해시만 변경):" && git log --oneline -6
echo ""
echo "② 이력 전체에 insight PDF가 남았는가 (아무것도 안 나와야 정상):"
if git rev-list --objects HEAD | grep "insight/.*\.pdf"; then echo "실패: PDF가 아직 이력에 있음 — backup/pre-pdf-scrub 로 복구 가능"; exit 1
else echo "  → 없음. 제거 성공"; fi
echo ""
echo "③ 병합 커밋에 JSON 8개는 유지됐는가:" && git ls-tree "$merge_new" insight/ | sed 's/^/  /'
echo ""
echo "④ 원격과의 관계 (일반 push 가능 여부):"
if git merge-base --is-ancestor "origin/$BRANCH" HEAD 2>/dev/null; then echo "  → 원격 tip이 조상 그대로 → 일반 push 가능(강제 push 불필요)"
else echo "  → 강제 push(--force-with-lease) 필요"; fi
echo ""
echo "완료. 백업은 backup/pre-pdf-scrub (로컬 전용 — 절대 push 금지, PR 머지 후 git branch -D 로 삭제)."
