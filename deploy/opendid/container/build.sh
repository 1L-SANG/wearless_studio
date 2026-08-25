#!/usr/bin/env bash
# OpenDID 컨테이너 이미지 빌드 — 공식 V2.0.0 JAR 3개(TA/Issuer/CAS) + 커스텀 fm-holder 를
# container/jars/ 로 모은 뒤 docker build. JAR·wallet·secret 은 git·이미지에 커밋 금지(.gitignore).
#
# JAR 소싱:
#   - 공식 3개: 기본은 로컬 오케스트레이터 jars/ 에서 복사(이미 download.sh 로 받아둠).
#     CI/재현빌드는 OmniOneID GitHub releases 에서 받도록 교체(download.sh <ver> 참고).
#   - fm-holder: services/fm-holder gradlew bootJar 산출물.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
ORCH="${OPENDID_ORCH_DIR:-$HOME/devs/did-orchestrator-server/source/did-orchestrator-server}"
VER="${OPENDID_VERSION:-2.0.0}"
IMAGE="${IMAGE:-opendid:local}"

mkdir -p "$HERE/jars"
echo "== gather official V2.0.0 jars =="
for pair in "TA:did-ta-server" "Issuer:did-issuer-server" "CA:did-ca-server"; do
  dir="${pair%%:*}"; name="${pair##*:}"
  src="$ORCH/jars/$dir/$name-$VER.jar"
  [ -f "$src" ] || { echo "MISSING $src — run orchestrator download.sh $VER, or set OPENDID_ORCH_DIR"; exit 1; }
  cp "$src" "$HERE/jars/$name-$VER.jar"
done
echo "== build + copy fm-holder (Java 21 강제 — gradle 은 Java 25 미지원) =="
J21="$(/usr/libexec/java_home -v 21 2>/dev/null || echo /opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home)"
[ -x "$J21/bin/java" ] || { echo "Java 21 not found (need JDK 21 for the OpenDID gradle build)"; exit 1; }
( cd "$REPO/services/fm-holder" && JAVA_HOME="$J21" ./gradlew -q bootJar )
cp "$REPO/services/fm-holder/build/libs/fm-holder-0.1.0.jar" "$HERE/jars/"

echo "== docker build $IMAGE =="
docker build -t "$IMAGE" "$HERE"
echo "done: $IMAGE"
