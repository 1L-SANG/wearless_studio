#!/usr/bin/env bash
# =============================================================
# OpenDID 스택 중지: orchestrator(:9001) + 엔티티 JVM(8090-8095) + Besu·postgres 도커.
#   Generate All 상태(월렛·DID)는 postgre-opendid 볼륨에 보존 → 다음 Start All 시 재사용.
# =============================================================
set -uo pipefail
export PATH="/opt/homebrew/bin:$PATH"

echo "JVM 중지:"
for p in 9001 8090 8091 8092 8093 8094 8095; do
  pid=$(lsof -nP -tiTCP:"$p" -sTCP:LISTEN 2>/dev/null)
  [ -n "$pid" ] && kill "$pid" 2>/dev/null && echo "  ✓ :$p" || echo "  - :$p 없음"
done

echo "도커 중지(데이터 보존):"
for c in opendid-besu-node postgre-opendid; do
  docker stop "$c" >/dev/null 2>&1 && echo "  ✓ $c" || echo "  - $c 없음"
done
echo "✅ OpenDID down"
