# 공개 약관 관리

Wearless 약관의 공식 주소는 `https://wearless.kr` 아래 `/terms`, `/privacy`, `/refund`, `/model-license-terms`다. FaceMarket 약관은 `https://facemarket.wearless.kr` 아래 기존 경로를 유지한다.

문서 원본은 이 폴더의 Markdown, 사업자 정보 원본은 `src/lib/companyInfo.json`이다. `public/legal/`과 랜딩 저장소의 `content/legal/`은 생성된 공개본이므로 직접 수정하지 않는다.

## 문서 수정과 동기화

1. 이 저장소에서 원본 문서 또는 사업자 정보를 수정한다.
2. 이 저장소 루트에서 다음 명령을 실행한다. `--landing-root`에는 수정할 랜딩 체크아웃 또는 워크트리를 지정한다.

```sh
python3 tools/legal_publish.py --landing-root /path/to/landing_page_fasho
```

3. 앱의 `public/legal/`과 랜딩의 `content/legal/` 변경을 각각 검토하고 커밋한다. 자리표시자가 남으면 명령은 실패하며 랜딩으로 내보내지 않는다.
4. 두 저장소의 빌드와 법적 고지 링크를 확인한다. 랜딩은 커밋된 공개본으로 정적 페이지를 만들며 앱 서버를 호출하지 않는다.

## 최초 도메인 이전 배포 순서

랜딩의 네 약관 페이지를 먼저 배포하고 공개 접근을 확인한 뒤, 앱의 링크와 리다이렉트 변경을 배포한다. 반대 순서로 배포하면 아직 없는 랜딩 약관 주소로 사용자를 보낼 수 있다.

앱의 기존 네 주소는 `ai.wearless.kr`에서만 공식 주소로 영구 이동한다. FaceMarket의 `/terms`·`/privacy`에는 이 리다이렉트를 적용하지 않는다.
