# FaceMarket v2 증서 배포

새 발급은 `facelicense-v2`를 사용한다. 가변 사용 조건은 PostgreSQL에서 판정하며 증서에는 모델 DID, 라이선스 ID, 저장된 발급 시각, 얼굴 증거 해시, 계약 버전, 해당 등록의 동의 버전만 넣는다.

기존 v1 namespace와 schema, 발급 기록, Holder 멱등 기록은 변경하거나 지우지 않는다. 기존 VC의 검증과 폐기 API는 그대로 사용한다. 이 문서 작성 중 운영 적용과 실발급은 실행하지 않았다.

## 배포 전 확인

- Issuer와 TAS, Holder 설정과 보관 데이터의 백업을 확보한다.
- 기존 발급 요청을 마치고 새 요청을 잠시 막은 상태에서 순서대로 적용한다. 새 Python API와 예전 Holder 또는 그 반대 조합은 발급하지 못한다.
- 기존 pending 라이선스에 v1 발급 시도가 있는지 확인한다. 같은 `fm-license:<UUID>` 키의 기존 결과가 v1이라면 v2 요청은 의도적으로 충돌한다. 키나 멱등 기록을 삭제하지 말고 기존 발급 결과와 폐기 여부를 운영자가 먼저 확인한다.
- `FL_NAMESPACE_ID`, `FL_VC_SCHEMA`, `FL_VC_PLAN`에 예전 값을 강제로 넣는 배포 환경이 있으면 아래 v2 값으로 맞춘다.

## 적용 순서

1. Issuer 관리 API와 운영 PostgreSQL 컨테이너를 사용할 수 있는 배포 호스트에서 새 리소스를 추가한다. `PG_USER`와 `PG_CONTAINER`는 해당 호스트의 승인된 환경값을 사용한다.

   ```sh
   FL_NAMESPACE_ID=kr.wearless.facelicense.v2 \
   FL_VC_SCHEMA=facelicense-v2 \
   FL_VC_PLAN=vcplanface0000000002 \
   bash scripts/issuer-provision-facelicense.sh
   ```

   스크립트가 `facelicense_plan=present`를 반환하는지 확인한다. 기존 리소스가 있으면 생성하지 않는다. 새 namespace의 여섯 필수 claim과 schema 2.0, Issuer 및 TAS의 `vcplanface0000000002` 연결을 확인한다. 기존 v1 리소스를 갱신하는 명령은 없다.

2. JDK 21을 사용해 새 Holder를 빌드하고 기존 Holder 배포 절차로 교체한다. Holder의 기존 data-dir은 유지한다.

   ```sh
   cd services/fm-holder
   ./gradlew test bootJar --no-daemon
   ```

3. 새 Python API를 배포한다. HTTP 발급과 백그라운드 재시도 모두 같은 저장된 `created_at`과 등록 동의 버전을 사용한다. 현재 서버 시각이나 최신 동의 문서 상수로 대체하지 않는다.

4. 승인된 테스트 모델로 배포 호스트의 관리 모드 스모크를 실행한다. 이 명령은 실제 VC를 발급하고 폐기하며 Holder를 재시작하므로 별도 운영 승인이 필요하다. 실제 서비스 환경과 재시작 권한을 기존 배포 절차에 따라 로드한 뒤 실행한다.

   ```sh
   OPENDID_SMOKE_MODE=managed bash deploy/opendid/smoke.sh
   ```

5. 모델 등록에서 발급 성공, 동일 요청 재시도의 같은 VC 반환, 마이페이지 조건 변경 후 현재 DB 조건 적용, 증서 폐기를 확인한 뒤 신규 등록을 다시 연다.

롤백 시에도 v2 schema와 발급 및 멱등 기록을 보존한다. 이미 시도된 키를 다른 계약으로 다시 발급하지 않는다.
