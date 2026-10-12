# Vercel 프론트 운영 배포

2026-10-07 기준. 정상 배포 경로는 main의 Git 자동 배포다. CLI prebuilt 자동 배포는 Sensitive 환경변수의 `[SENSITIVE]` 자리표시를 빌드에 넣어, 정상 Git 배포를 목 화면으로 덮어쓴 사고 때문에 중단했다.

## 수동 비상 배포

Git 자동 배포가 `commit author did not have contributing access` 작성자 검사로 막혔을 때만 GitHub Actions의 `vercel-deploy`를 `workflow_dispatch`로 실행한다. 배포할 main 커밋을 확인하고 main 브랜치를 선택한다.

실행 전 Vercel Production 환경변수를 확인한다.

- 모든 `VITE_` 변수는 Sensitive로 저장하지 않는다. 해당 변수를 Sensitive가 아닌 값으로 다시 저장한다. 프론트 빌드에 포함되는 값이므로 실제 비밀값을 넣지 않는다.
- `VITE_API_MODE`는 정확히 `http`, `VITE_API_BASE_URL`은 `https://`로 시작하는 올바른 API URL이어야 한다.
- 저장소에 `VERCEL_TOKEN` 시크릿이 있어야 한다.

워크플로는 `vercel@62.5.0`을 사용한다. `vercel pull` 직후 `.vercel/.env.production.local`의 `VITE_` 자리표시를 검사하고, Vite도 `VERCEL_ENV=production` 빌드에서 최종 값을 검사한다. 검사가 실패하면 Vercel 값을 고친 뒤 다시 실행한다.

## 목 모드 확인과 복구

1. 운영에 배포된 JS에서 `[SENSITIVE]`를 검색한다. API 모드의 `!=="http"` 또는 `!="http"` 비교 앞에 들어간 값도 확인한다. `http` 이외의 값이면 목 모드다. 최소화 과정에서 비교가 상수로 접힐 수 있어 문자열이 없다는 것만으로 정상이라고 판단하지 않는다.
2. 실제 화면에서 API를 쓰는 동작을 한 뒤 API 로그의 `/v1` 요청이 사라졌는지 확인한다. 평소 요청이 있던 흐름에서 요청이 없어졌다면 목 모드를 의심한다.
3. Vercel 대시보드에서 최신 `source=git` 배포가 READY인지, 대상 커밋이 맞는지 확인하고 Promote한다. ai, facemarket, toss, admin 운영 도메인이 해당 배포를 가리키는지 확인한다.

CLI prebuilt 배포를 Redeploy해도 같은 잘못된 산출물이라 복구되지 않는다. 정상 Git 배포를 Promote한 뒤 배포 JS와 `/v1` 요청을 다시 확인한다.
