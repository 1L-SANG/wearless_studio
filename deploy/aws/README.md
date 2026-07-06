# AWS 배포 IAM 셋업 (GitHub Actions OIDC)

GitHub Actions가 **장기 액세스키 없이** OIDC로 AWS에 배포하도록 하는 일회성 셋업.
`copilot env init`/`copilot app init` **부트스트랩을 먼저 끝낸 뒤** 실행 (그래야 `wearless-*` 롤/스택이 존재).

두 JSON의 `<ACCOUNT_ID>`를 니 계정번호로 치환:

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
sed -i '' "s/<ACCOUNT_ID>/$ACCOUNT_ID/g" deploy/aws/oidc-trust.json deploy/aws/deploy-permissions.json
```

## 1. GitHub OIDC provider 등록 (계정당 한 번)

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 1c58a3a8518e8759bf075b76b750d4f2df264fcd
```

> 이미 있으면 `EntityAlreadyExists` 뜸 — 무시. thumbprint는 2023년 이후 AWS가 검증 안 하지만 CLI가 값을 요구함.

## 2. 배포 롤 생성 + 권한 부착

```bash
aws iam create-role \
  --role-name gha-wearless-deploy \
  --assume-role-policy-document file://deploy/aws/oidc-trust.json

aws iam put-role-policy \
  --role-name gha-wearless-deploy \
  --policy-name copilot-deploy \
  --policy-document file://deploy/aws/deploy-permissions.json

aws iam get-role --role-name gha-wearless-deploy --query Role.Arn --output text
# ↑ 출력된 ARN 복사
```

## 3. GitHub repo secret 등록

```bash
gh secret set AWS_DEPLOY_ROLE_ARN --repo 1L-SANG/wearless_studio \
  --body "arn:aws:iam::<ACCOUNT_ID>:role/gha-wearless-deploy"
```

이후 `main`에 `server/**` 변경 push → `.github/workflows/deploy-server.yml`가 자동 배포.

---

## 보안 주의

- **이 롤은 강력함** — `wearless-*` CloudFormation 스택 배포 + `wearless-*` IAM 롤 assume 가능.
  Trust는 `repo:1L-SANG/wearless_studio:ref:refs/heads/main` 로 **정확히 이 repo의 main 브랜치에만** 제한됨.
  `:ref:refs/heads/main` 을 `:*` 로 넓히지 말 것 (아무 브랜치·PR이 배포 권한 얻음).
- `deploy-permissions.json`은 `wearless-*` 네이밍으로 스코프됨. 부트스트랩 후 배포가
  `AccessDenied` 나면 에러가 빠진 액션명을 알려줌 → 그것만 추가. 또는 `sts:AssumeRole` 리소스를
  부트스트랩이 만든 정확한 `wearless-prod-EnvManagerRole` ARN으로 좁히면 더 안전.
- 이 파일들엔 시크릿 없음(계정번호만) — 커밋 OK.
