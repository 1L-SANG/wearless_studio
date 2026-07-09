#!/usr/bin/env bash
# =============================================================
# OpenDID 스택 기동 (S2 VC 발급/검증용). Java 21 필요.
#   orchestrator(:9001) 를 띄운다. 엔티티/Besu는 대시보드 Start All 로 기동.
# 레포는 wearless_studio 밖(sibling): 기본 ~/devs/did-orchestrator-server.
#   경로 다르면 OPENDID_DIR 로 지정.
# =============================================================
set -uo pipefail
ORCH_DIR="${OPENDID_DIR:-$HOME/devs/did-orchestrator-server/source/did-orchestrator-server}"
JAR="$ORCH_DIR/build/libs/did-orchestrator-server-2.0.0.jar"
export JAVA_HOME="/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home"
export PATH="/opt/homebrew/opt/openjdk@21/bin:/opt/homebrew/bin:$PATH"
LOGDIR="$HOME/devs/wearless_studio/.dev/logs"; mkdir -p "$LOGDIR"

listening() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

# Java 21 확인 (25면 Gradle/부트 안 맞음)
ver=$(java -version 2>&1 | head -1)
echo "$ver" | grep -q '"21' || { echo "✗ Java 21 아님: $ver"; echo "  openjdk@21 설치/PATH 확인"; exit 1; }

if listening 9001; then
  echo "✓ orchestrator 이미 :9001"
else
  [ -f "$JAR" ] || { echo "✗ jar 없음: $JAR"; echo "  clone+build 먼저 (README 참고)"; exit 1; }
  ( cd "$ORCH_DIR"; nohup java -jar "$JAR" >"$LOGDIR/orchestrator.log" 2>&1 & )
  for _ in $(seq 1 40); do
    curl -sS -m3 -o /dev/null http://localhost:9001/ 2>/dev/null && break; sleep 1
  done
  listening 9001 && echo "✓ orchestrator :9001 up" || { echo "✗ 기동 실패 — $LOGDIR/orchestrator.log 확인"; exit 1; }
fi

cat <<'EOF'

→ 브라우저: http://localhost:9001
   · 첫 실행: Generate All → (Repository=Hyperledger Besu) → Start All
   · 이후:   Start All 만 (Generate 상태는 postgre-opendid 볼륨에 보존)
   포트: TA 8090 · Issuer 8091(발급) · Verifier 8092(검증) · API 8093 · CA 8094 · Wallet 8095 + Besu(docker)
   중지: scripts/orchestrator-down.sh
EOF
