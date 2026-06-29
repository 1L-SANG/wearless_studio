## 🚀 PR 유형 (하나 이상 선택)

- [ ] Feat: 새로운 기능 추가
- [ ] Fix: 버그 수정
- [ ] Docs: 문서 수정
- [ ] Style: 코드 포맷팅 (세미콜론, 누락 등)
- [ ] Refactor: 코드 리팩토링
- [ ] Chore: 빌드 설정, 환경 변수, 포트 설정 등

## 📝 작업 내용 (Description)

- 예: 3대 소셜(Google, Kakao, GitHub) OAuth 인증 로직 구현
- 예: JWT 발급 및 유저 정보 조회(/auth/me) 기능 완성

## 🛠️ 변경 사항 (Changes)

- `server/services/authService.js`: GitHub 이메일 null 이슈 해결 및 소셜별 분기 처리
- `schema.prisma`: Users 테이블 정의 및 migration 완료
- `docker-compose.yml`: 포트 설정 변경 (5000 -> 5002)

## ✅ 체크리스트 (Self-Check)

- [ ] 로컬 도커 환경에서 서버가 정상적으로 실행되나요? (Port: 5002)
- [ ] `pnpm prisma migrate dev`를 통해 DB 스키마를 최신화했나요?
- [ ] 소셜 로그인 시 `email`이 `undefined`나 `null`로 들어오지 않는 것을 확인했나요?
- [ ] 중요한 Secret Key가 `.env` 외에 코드에 하드코딩되지 않았나요?

## 🧪 테스트 결과 (Test Results)

- [ ] `curl -X POST /auth/login` -> 200 OK & JWT 발급 확인
- [ ] `curl -H "Authorization: Bearer ..."` -> `/auth/me` 유저 정보 정상 반환 확인
