# 폰트 라이선스

이 서비스가 쓰는 폰트와 각 라이선스를 한곳에 모은 목록이다.
**전부 SIL Open Font License 1.1(OFL)** 이라 상업적 사용·임베딩·유료 제품 번들이 모두 허용된다.

OFL이 요구하는 조건은 셋이다.

1. 폰트 파일 자체를 **단독 상품으로 팔지 않을 것** — 해당 없음
2. 폰트 파일을 **배포할 때 저작권 표시와 라이선스 전문을 함께 둘 것** — 이 폴더가 그 역할
3. 파생물도 **OFL을 유지할 것**(재라이선스 불가) — 폰트를 수정하지 않으므로 해당 없음

셀러가 받아 가는 상세페이지 PNG에는 아무 조건도 붙지 않는다. 렌더된 이미지는 폰트 데이터가 아니라
픽셀이므로 '폰트 배포'가 아니고, 출처 표기 의무도 없다. 조건 2가 걸리는 건 `.woff2`/`.ttf` 파일을
브라우저에 내려보내는 행위뿐이다.

## 저장소에 번들해 직접 서빙하는 폰트

`src/styles/tokens.css`의 `@font-face`가 이 파일들을 가리킨다. **조건 2가 적용되는 대상이다.**

| 폰트 | 파일 | 라이선스 | 저작권 |
|---|---|---|---|
| Pretendard | `../PretendardVariable.woff2` | [OFL 1.1](./OFL-Pretendard.txt) | Kil Hyung-jin 외 (Source·Inter·M PLUS 1 포함) |
| Cormorant | `../Cormorant-VariableFont_wght.woff2` | [OFL 1.1](./OFL-Cormorant.txt) | The Cormorant Project Authors |
| Gowun Dodum (고운돋움) | `../GowunDodum-Regular.woff2` | [OFL 1.1](./OFL-GowunDodum.txt) | The Gowun Dodum Project Authors (류양희) |

**Reserved Font Name 주의** — Pretendard의 OFL에는 예약 폰트명(`Pretendard`, `Source`, `Inter`,
`M PLUS 1`)이 걸려 있다. 폰트를 수정하면 그 이름을 쓸 수 없다. 지금은 원본을 그대로 쓰므로 문제없다.
Cormorant·Gowun Dodum의 OFL에는 예약 폰트명 문구가 없다.

**Cormorant 파일 출처** — 원본 가변 TTF(539KB)를 형식만 woff2로 변환한 것이다(164KB). 글리프 3,138개·굵기 축 300~700 그대로.

**Gowun Dodum 파일 출처** — 구글 폰트 공식 저장소의 원본 TTF
(`google/fonts` → `ofl/gowundodum/GowunDodum-Regular.ttf`, 6.89MB)를 **형식만 woff2로 변환**한 것이다
(436KB). 글자 모양은 손대지 않았고 글자 수도 그대로 11,172자 전체다. 서브셋하지 않은 이유는
에디터에서 셀러가 어떤 글자를 칠지 알 수 없기 때문이다 — 빠진 글자가 있으면 그 글자만 다른
글씨체로 튀어 최종 산출물의 결함이 된다.

## Google Fonts CDN에서 불러오는 폰트

`index.html`의 `<link>`가 불러온다. 파일을 우리가 배포하지 않으므로 **조건 2는 적용되지 않는다.**
기록용으로만 남긴다.

| 폰트 | 쓰임 | 라이선스 |
|---|---|---|
| Cal Sans | 디스플레이(제목) | OFL 1.1 |
| Inter | Pretendard 폴백 | OFL 1.1 |
| Playfair Display | 장식 | OFL 1.1 |
| Roboto Mono | 숫자·라벨 | OFL 1.1 |

CDN을 끊고 자체 호스팅으로 바꾸는 순간 이 4종도 조건 2의 대상이 된다. 그때는 각 OFL 전문을
이 폴더에 함께 넣어야 한다.

## 폰트를 새로 추가할 때

1. 라이선스가 상업적 사용·임베딩을 허용하는지 먼저 확인한다(OFL·Apache-2.0이면 대개 가능).
2. 번들한다면 라이선스 전문을 이 폴더에 넣고 위 표에 한 줄 추가한다.
3. 배선은 네 군데다 — `src/styles/tokens.css`(@font-face + 토큰),
   `src/mock/db.js`(`catalogs.fonts`), `src/features/editor/Editor.jsx`(`FONT_MAP`),
   그리고 필요하면 `index.html`.
