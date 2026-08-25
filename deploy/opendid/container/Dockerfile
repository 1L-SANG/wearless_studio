# OpenDID single-container: TA + Issuer + CAS + fm-holder (localhost 배선), besu 없음.
# 상태(pg·wallet)는 컨테이너 밖(RDS·Secrets/EFS) → stateless → Fargate Spot + scale-to-zero.
# JAR 은 build.sh 가 container/jars/ 로 모아둔다(공식 V2.0.0 3개 + 커스텀 fm-holder).
FROM eclipse-temurin:21-jre

RUN apt-get update && apt-get install -y --no-install-recommends curl bash \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/opendid
COPY jars/ /opt/opendid/jars/
COPY entrypoint.sh /opt/opendid/entrypoint.sh
RUN chmod +x /opt/opendid/entrypoint.sh \
    && test -f /opt/opendid/jars/did-ta-server-2.0.0.jar \
    && test -f /opt/opendid/jars/did-issuer-server-2.0.0.jar \
    && test -f /opt/opendid/jars/did-ca-server-2.0.0.jar \
    && test -f /opt/opendid/jars/fm-holder-0.1.0.jar

# holder(:8100)만 노출 — api 서비스가 이 포트로 VC 요청. 서버 간 통신은 컨테이너 내부 localhost.
EXPOSE 8100
HEALTHCHECK --interval=15s --timeout=4s --start-period=90s --retries=4 \
  CMD curl -fsS http://localhost:8100/actuator/health || exit 1

ENTRYPOINT ["/opt/opendid/entrypoint.sh"]
