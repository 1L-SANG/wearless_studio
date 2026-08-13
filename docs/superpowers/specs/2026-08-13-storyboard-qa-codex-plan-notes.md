# Codex 계획 검토 원문 (2026-08-13)

## ① §C 구체 변경 목록

### 먼저 고쳐야 할 계획 전제

계획서의 “동일 `spaceGroupId`를 유지한 채 개별 컷으로 run만 나눈다”는 수용 기준은 실서버 저장 계약과 충돌한다. 서버는 같은 `spaceGroupId`를 가진 모든 블록의 전역 위치가 연속인지 검사하고, 중간에 다른 블록이 있으면 `space_set_members_not_contiguous`로 PUT을 거부한다. 이 동작은 서버 테스트로도 고정돼 있다. `server/app/agents/space_set_assets.py:545-558`, `server/app/agents/space_set_assets.py:560-575`, `server/app/agents/space_set_assets.py:615-620`, `server/tests/test_space_set_assets.py:458-475`

따라서 서버를 바꾸지 않는 조건에서는 첫 run은 기존 ID를 유지하고, 끊긴 뒤쪽 run은 같은 공간세트 ID를 담은 새 instance `spaceGroupId`로 재키해야 한다. `spaceSetGroupId(setId, uniqueId)`는 set ID와 instance를 분리한 정식 ID 생성 함수이므로 필드 의미와 공간세트 종류는 유지된다. `src/lib/storyboardSpaceSetCatalog.js:251-268`

계획서의 렌더 위치도 일부 드리프트가 있다. run 분리는 현재 `canvasUnits`의 `522-539`, 트레이 본체는 `2581-2623`, 묶음 풀기는 `2416-2424`, 트레이 전용 추가 카드는 `2609-2619`, 외부 추가 카드는 `2683-2693`이다. “계획서 기재 줄번호와 다름”으로 정정해야 한다. `src/features/storyboard/Storyboard.jsx:522-539`, `src/features/storyboard/Storyboard.jsx:2416-2424`, `src/features/storyboard/Storyboard.jsx:2581-2623`, `src/features/storyboard/Storyboard.jsx:2683-2693`

### 함수·컴포넌트별 변경

1. `storyboardSpaceSets.js`에 두 개의 순수 helper를 추가한다.

   - `detachSpaceMembership(block)`: `spaceGroupId`, `spaceVariation`, `spaceSetMemberOrder`를 제거하고 일반 컷의 `refScope`를 복구한다.
   - `rekeySeparatedSpaceRuns(blocks, nextGroupId)`: `groupConsecutiveSpaceRuns` 결과에서 이미 본 `spaceGroupId`가 다시 나타나면 해당 뒤쪽 run 전체에 같은 set ID의 새 instance ID를 배정한다. 첫 run은 기존 ID를 유지한다. 기존 run 분리 helper는 이미 섹션과 연속 ID를 기준으로 작동한다. `src/lib/storyboardSpaceSets.js:31-52`, `src/lib/storyboardSpaceSetCatalog.js:251-268`

2. `canvasUnits`는 “연속한 동일 ID + 동일 섹션”만 한 공간 run으로 만드는 현재 조건을 유지하되, `kind:'tray'`를 `kind:'spaceRun'` 정도로 명명해 UI 의미를 낮춘다. React key는 `spaceGroupId`만 쓰면 재키 전 한 프레임에서 중복될 수 있으므로 `spaceGroupId + 첫 block.id`로 만든다. 현재 run 탐색 조건 자체는 §C의 시각 분리에 정확히 맞는다. `src/features/storyboard/Storyboard.jsx:522-539`, `tests/frontend/storyboard-space-sets.test.mjs:50-64`

3. `renderTray`는 `renderSpaceRun`으로 단순화한다.

   - 얇은 테두리와 헤더는 유지한다. 현재 CSS도 이미 채움 없는 1px 테두리다. `src/styles/features.css:2449-2463`
   - 헤더에 `spaceSetDisplayName(set)` 라벨을 넣고 `장소 세트 변경`은 유지한다. 표시명 helper가 이미 존재한다. `src/lib/spaceSetDisplayNames.js:86-89`, `src/features/storyboard/Storyboard.jsx:2594-2599`
   - `<details className="sb-tray-more">…묶음 풀기…</details>`는 통째로 삭제한다. `src/features/storyboard/Storyboard.jsx:2600-2605`
   - `.sb-tray-add`, `.sb-tray-add-preview`, `.sb-tray-add-label` 예약 카드 UI를 삭제한다. `src/features/storyboard/Storyboard.jsx:2609-2619`, `src/styles/features.css:2517-2540`
   - 세트 전체 드래그는 별도 이질 UI가 아니라 헤더 drag handle로 유지할 수 있다. 현재 `moveSpaceSetRun`은 연속 run 하나를 옮기며 `refScope:'pose'`를 재확정한다. `src/features/storyboard/Storyboard.jsx:2588-2593`, `src/features/storyboard/Storyboard.jsx:2229-2247`, `src/lib/storyboardSpaceSets.js:135-144`

4. 추가 UI는 `StoryboardInsertControl` 하나로 통합한다.

   - 내부 경계는 `targetSpaceGroupId`를 넘기고, 세트 바깥 경계는 `null`을 넘긴다. 현재 `insertControl`과 `onDropAt`가 이미 이 전달 계약을 갖는다. `src/features/storyboard/Storyboard.jsx:2454-2480`
   - 섹션 끝의 별도 `.sb-ghost-card`도 삭제하고, 같은 `StoryboardInsertControl`의 terminal/empty 변형으로 바꾼다. 현재 `.sb-addzone.end { display:none }`이므로 terminal은 명시적으로 표시하도록 바꿔야 한다. `src/features/storyboard/Storyboard.jsx:2683-2693`, `src/styles/features.css:2399-2429`
   - 드래그 중 `.drop-on`은 플러스만 보이고 양옆 이동 selector에는 포함되지 않는다. N5를 위해 `.drop-on`도 카드 `translateX` selector에 포함한다. `src/styles/features.css:2426-2436`
   - 한 공간 run의 마지막 카드 안쪽 addzone은 세트 소속, 테두리 바깥 terminal addzone은 일반 컷으로 정의해 한 종류의 UI로 양 경계를 구분한다. 이 구분값은 기존 `targetSpaceGroupId` 하나로 표현 가능하다. `src/features/storyboard/Storyboard.jsx:2518-2522`, `src/features/storyboard/Storyboard.jsx:2473-2477`

5. 예약 로직은 UI가 아니라 addzone 동작으로 재사용한다.

   - `nextSpaceSetMemberReservation`과 `reservation.blockPatch`는 삭제하지 않는다. 사용하지 않은 멤버를 order와 example ID로 찾아 `spaceGroupId`, `spaceVariation`, `refScope:'pose'`, `spaceSetMemberOrder`를 만드는 저장 가능한 로직이다. `src/lib/storyboardSpaceSets.js:54-80`
   - 공간 run 내부 addzone을 클릭하면 해당 run 전체로 예약을 계산해 `addBlock(..., reservation)`에 넘긴다. 시각적으로는 일반 플러스 하나만 보인다. `addBlock`의 예약 객체 판별과 `blockPatch` 적용 경로는 그대로 재사용한다. `src/features/storyboard/Storyboard.jsx:2070-2075`, `src/features/storyboard/Storyboard.jsx:2150-2160`
   - 이 예약은 B3/N4의 일반 자동배정 예외로 문서화해야 한다. `spaceGroupId`가 있으면서 `exampleId`가 비어 있으면 서버의 pose 참조 검증에서 즉시 거부되기 때문에, 서버 변경 없이 공간세트 내부 빈 컷을 저장할 수 없다. `server/app/agents/space_set_assets.py:356-369`, `server/app/agents/space_set_assets.py:412-428`

6. `addBlock`의 소속 결정은 명시적 target만 사용한다.

   - `targetSpaceGroupId`가 있을 때만 새 블록을 공간 run에 가입시킨다.
   - 현재 “같은 section의 모든 peer가 같은 group이면 암묵 가입”하는 폴백은 삭제한다. 이 폴백은 바깥 terminal addzone에서도 새 컷을 세트에 잘못 넣을 수 있다. `src/features/storyboard/Storyboard.jsx:2136-2147`
   - 일반 addzone 추가에는 `exampleChoice:'manual'`을 넣고 `assignGenerationExamples`가 건너뛰게 한다. 예약이나 드롭된 예시가 있는 경로는 이미 `droppedExample` 분기로 들어가므로 manual-empty 대상에서 제외한다. `src/features/storyboard/Storyboard.jsx:2150-2166`

7. 기존 블록 이동과 신규 추가의 의미를 분리한다.

   - 신규 `addBlock`만 내부 addzone의 `targetSpaceGroupId`에 가입한다.
   - 기존 일반 컷이나 `source:'mine'`을 공간 run 가운데로 이동하면 소속시키지 않고 그 자리에 일반 컷으로 둔 뒤 뒤쪽 공간 run을 재키한다. 이는 “다시 묶기 없음” 원칙에도 맞는다. 현재 helper는 mine 진입을 막지만 Storyboard가 직후 ID를 다시 덮어써 그 보호를 무효화하고 있으므로 `2305-2310`의 무조건 재부여 블록을 삭제해야 한다. `src/lib/storyboardSpaceSets.js:106-132`, `src/features/storyboard/Storyboard.jsx:2297-2312`
   - 기존 세트 멤버를 같은 ID run 안에서 재정렬할 때만 소속을 유지하고, 바깥이나 다른 공간 run으로 이동하면 detach한다.
   - 묶음 풀기 메뉴가 없어지므로 “먼저 묶음을 푼 뒤 이동” 차단도 제거해야 한다. 그렇지 않으면 세트 멤버는 다른 렌더 그룹으로 영구 이동 불가 상태가 된다. `src/features/storyboard/Storyboard.jsx:2261-2267`
   - `nudgeBlock`도 이 규칙을 공유해야 한다. 현재 이웃의 ID를 무조건 target으로 넘기므로 일반 컷을 세트에 가입시킬 수 있다. `src/features/storyboard/Storyboard.jsx:2317-2334`

8. 세트 멤버를 “내 이미지”로 전환할 때 현재 위치를 유지한다.

   현재 코드는 mine 전환 시 해당 컷을 run 끝으로 옮겨 남은 세트를 다시 붙인다. §C에서는 이 블록을 그 자리에 둔 채 space 필드만 지우고 뒤쪽 run을 재키해야 한다. 따라서 `patch`의 `1919-1924` 이동 블록을 `detach + rekey`로 교체한다. `src/features/storyboard/Storyboard.jsx:1904-1929`

9. 삭제 범위는 다음과 같다.

   - `dissolveSpaceGroup` 함수와 `dissolveSpaceSet` import. `src/features/storyboard/Storyboard.jsx:52-60`, `src/features/storyboard/Storyboard.jsx:2416-2424`
   - 트레이 more-menu JSX와 대응 CSS. `src/features/storyboard/Storyboard.jsx:2600-2605`, `src/styles/features.css:2493-2515`
   - 트레이 예약 카드 JSX/CSS. `src/features/storyboard/Storyboard.jsx:2609-2619`, `src/styles/features.css:2517-2540`
   - 섹션 끝 `.sb-ghost-card` 추가 버튼. `src/features/storyboard/Storyboard.jsx:2683-2693`

### 저장·조립 계약 확인

`blocks:list` 저장 계약 자체는 유지된다. HTTP 라우트는 여전히 list body를 canonicalize한 뒤 JSONB에 통째로 저장하며, canonicalizer는 `dict(block)`에서 시작하므로 `exampleChoice` 같은 신규 필드도 제거하지 않는다. `server/app/routes.py:1937-1947`, `server/app/agents/content_roles.py:161-188`, `server/app/repo.py:1805-1826`

단, 동일 ID가 떨어진 상태는 저장 불가이고 위 재키를 적용한 뒤에만 계약이 안전하다. 새 ID도 같은 set ID를 포함하므로 서버는 각 연속 run을 같은 발행 공간세트의 별도 instance로 검증한다. `server/app/agents/space_set_assets.py:560-575`, `src/lib/storyboardSpaceSetCatalog.js:251-268`

mock 조립기는 입력 배열 순서대로 순회하고 `spaceGroupId`는 생성예시 이미지 선택 범위를 판별하는 데만 사용한다. 서버 조립기도 storyboard 순서를 그대로 따르며 실제 행 조립은 `sectionLayout/layoutRowId`로만 결정한다. 따라서 유효한 같은-set 재키는 결과 블록 순서나 행 조립을 깨지 않는다. `src/mock/db.js:249-288`, `src/mock/db.js:316-356`, `server/app/agents/page_assembler.py:279-318`, `server/app/agents/page_assembler.py:410-435`

---

## ② N7 근본 원인 + 수정 지점

### 확정된 버그 경로

mock 시드의 기준색은 `name:'블랙', swatchId:'black'`이다. 신규 입력 초기화는 `swatchId`와 이미지만 비우고 `name`은 복사하므로, 새 화면에서도 숨은 `name:'블랙'`이 남는다. `src/mock/db.js:388-405`, `src/features/product-input/ProductInput.jsx:959-960`

핑크를 선택하면 UI는 `swatchColors`에서 `swatchId`를 찾아 “핑크”로 표시하지만, 실제 상태 변경은 `{...color, swatchId}`뿐이며 `name`은 갱신하지 않는다. `src/features/product-input/ProductInput.jsx:268-274`, `src/features/product-input/ProductInput.jsx:1010-1012`, `src/mock/db.js:108-120`

분석 완료 후 저장도 `name`과 `swatchId`를 서로 독립된 metadata 필드로 보존한다. mock은 patch를 그대로 DB product에 합치며, HTTP는 동일한 colors patch를 Product JSONB로 전달한다. 어느 경로에도 swatch label을 `name`으로 복사하는 단계가 없다. `src/features/product-input/saveRouting.js:86-100`, `src/features/product-input/saveRouting.js:137-153`, `src/mock/api.js:236-243`, `src/lib/api/httpAdapter.js:417-420`, `server/app/models.py:120-149`, `server/app/repo.py:439-469`

분석 결과의 `swatchSuggestions`도 별도 analysis 필드일 뿐 Product의 `colors[].name`을 바꾸지 않는다. mock 분석 역시 DB analysis 복사본만 반환한다. `src/lib/api/httpAdapter.js:277-296`, `server/app/workers/analyze_job.py:109-127`, `server/app/repo.py:1743-1785`, `src/mock/api.js:296-310`

콘티는 Product를 다시 읽은 뒤 `color.name || '색상'`만 label로 쓰므로 `swatchId:'pink', name:'블랙'`에서 블랙이 표시된다. 계획서의 `:1503`은 현재도 정확하며 전체 범위는 `1501-1519`다. `src/features/storyboard/storyboardEntryPrefetch.js:9-17`, `src/features/storyboard/Storyboard.jsx:1501-1519`

### 근본 수정 지점: 정확히 한 곳

수정 지점은 ProductInput 저장부가 아니라 `prepareStoryboardEntry`의 `allColorOpts` 생성부 한 곳으로 확정하는 것이 안전하다. 타입 계약도 `swatchId`를 색상의 정체성으로 보고 `name·hex`를 파생값으로 정의한다. `src/lib/types.js:86-91`, `src/features/storyboard/Storyboard.jsx:1501-1505`

제안 우선순위는 다음과 같다.

1. `hydratedCatalogs.swatchColors.find(s => s.id === color.swatchId)?.label`
2. `color.name?.trim()`
3. ``색상 ${index + 1}``

hex는 이미 `swatchId → name → 기본색` 순서의 `hexFor`를 사용하므로 그대로 둔다. `src/features/storyboard/Storyboard.jsx:84-89`

이 한 곳을 고치면 mock과 HTTP, 이미 저장된 불일치 데이터까지 모두 복구된다. ProductInput에서 `name`을 동기화하는 방식은 이후 저장에만 효과가 있어 기존 `swatchId/name` 불일치 레코드를 고치지 못하고, `swatchId`가 정체성이라는 타입 계약과도 중복된다. `src/lib/types.js:86-91`, `server/app/models.py:120-129`

---

## ③ N6 지점 + 재현 경로

정확한 렌더 지점은 캡션의 `sb-match-chip`이다. `sb-val-changed`, 트레이 예약 카드, 갤러리 항목이 아니다. 캡션은 방향·샷·색상을 먼저 그리고 맨 마지막에 매칭 칩을 추가한다. `src/features/storyboard/Storyboard.jsx:258-280`

캡션은 `justify-content:flex-end`이고 방향·샷 영역은 축소 가능하지만 매칭 칩은 `flex:none`이다. 따라서 칩이 생기면 기존 “정면 · 미디움샷 · 블랙” 영역이 왼쪽으로 밀린다. 칩 배경도 `#f6f2ea`, 글자는 갈색이어서 오너가 본 노란 계열 요소와 일치한다. `src/styles/features.css:2352-2375`, `src/styles/features.css:2383-2394`

mock 코드상 재현 경로는 다음과 같다. 브라우저 실조작은 수행하지 않았으므로 시각 재현 완료 여부는 `(가설: 브라우저 스모크 미수행)`이다.

1. `/create/storyboard`에 진입한다. 해당 라우트는 `Storyboard`를 렌더한다. `src/App.jsx:541-550`
2. `Section 1`을 펼치고 두 번째 컷을 선택한다. 기본 콘티의 두 번째 블록은 `horizon/front/medium`, 기준색, `matchIds:[]`이다. `src/lib/api/shapes.js:30-37`, `src/lib/api/shapes.js:126-129`
3. 인스펙터에서 `매칭 의류 바꾸기`를 누른다. mock은 top/women 기준 후보 목록을 갖고 있으며, worn 컷에만 이 UI가 노출된다. `src/mock/db.js:180-194`, `src/features/storyboard/Storyboard.jsx:1374-1395`
4. 후보 하나를 클릭하면 현재 Set 토글 로직이 `matchIds`에 ID를 추가한다. `src/features/storyboard/Storyboard.jsx:1383-1388`
5. 캡션은 `matchIds[0]`으로 후보를 찾고 맨 오른쪽에 `sb-match-chip`을 추가한다. `src/features/storyboard/Storyboard.jsx:247-249`, `src/features/storyboard/Storyboard.jsx:268-279`

수정은 JSX에서 `sb-match-chip`을 `sb-caption-values`보다 앞에 배치하고, CSS를 흰 배경·검정 테두리·검정 글자로 바꾸면 된다. N8 이후에도 `matchIds[0]` 계약은 그대로 유효하다. `src/features/storyboard/Storyboard.jsx:247-280`, `src/styles/features.css:2383-2394`

---

## ④ 배치별(B1~B5) 구현 노트

### B1 — 소품·문구·CSS

- 1-02: `StoryboardMedia`의 `<i>카드를 열어 다시 시도</i>`만 확정 문구로 교체한다. missing 판정은 `source !== 'mine' && !exampleId`이므로 N4 빈 카드에도 같은 기반이 쓰인다. `src/features/storyboard/Storyboard.jsx:309-326`
- 1-05: `selectAnalysisComposeMode`를 재사용하되 적용 순서는 `현재 보드 flush → composeMode PATCH → 성공 시 reload`로 만든다. helper는 최신 요청만 롤백하지만 실패 시 `false`로 resolve하므로, `ComposeModeSummary`도 반환값이 `true`일 때만 닫아야 한다. `src/features/analysis/composeModeSelection.js:9-33`, `src/features/storyboard/Storyboard.jsx:1544-1555`, `src/features/storyboard/Storyboard.jsx:2779-2793`, `src/features/storyboard/Storyboard.jsx:2825-2833`
- 1-09: 공용 `Toggle`을 실제 `<button type="button" role="switch">` 또는 checkbox로 바꾸고, `setCopywriting`이 PATCH promise를 반환하도록 한다. 현재 setter는 optimistic set 뒤 promise를 반환하거나 catch하지 않아 롤백이 불가능하다. 최신 요청 ID와 confirmed 값을 두는 패턴을 1-05와 동일하게 적용한다. `src/components/ui.jsx:176-178`, `src/store/useAppStore.js:421-425`
- 1-10: group 카드에는 이동 두 개까지 합쳐 네 버튼이 있으므로 모든 버튼을 44px 가로 1열로 늘리면 작은 카드 폭을 넘을 수 있다. 액션 영역을 2×2 grid로 만들고 아이콘의 시각 크기는 유지하는 방식이 안전하다. `src/features/storyboard/Storyboard.jsx:285-306`, `src/styles/features.css:2286-2312`, `src/styles/features.css:2048-2055`
- 1-11: `@media(max-width:900px)`의 전체 count 숨김을 없애고 비용만 남기는 축약 label을 별도 span으로 제공한다. 현재 비용은 `.sb-ab-count` 안에 있어 부모와 함께 사라진다. `src/features/storyboard/Storyboard.jsx:2864-2867`, `src/styles/features.css:1229-1241`
- 1-12: `loadError`를 문자열이 아닌 `{kind:'notFound'|'network', message}`로 둔다. 시작 시 projectId가 있었는데 `loadProject()`가 null이면 404로 보고 `/library` 버튼만 표시하고, entry GET의 `error.status===404`도 같은 상태로 합친다. 현재 404는 store에서 projectId를 지운 뒤 null을 반환하고 Storyboard는 `/create/input`으로 보낸다. `src/store/useAppStore.js:280-307`, `src/features/storyboard/Storyboard.jsx:1699-1706`, `src/features/storyboard/Storyboard.jsx:1764-1766`, `src/App.jsx:509-512`
- N1: `.sb-stack`의 고정 `200×258`을 `width:var(--sb-card-w); aspect-ratio:3/4`로 맞춘다. 펼친 카드는 최대 184px의 같은 변수와 3:4 비율을 쓴다. `src/styles/features.css:2048-2055`, `src/styles/features.css:2146-2169`, `src/styles/features.css:2212-2237`
- N3: 대상은 전역 `.toast-host`가 아니라 `sb-undo-bar`다. undo bar는 `top:72px`로 고정돼 있고, Storyboard는 이미 topnav와 복수 `job-ribbon-stack` 높이를 관찰해 `inspectorTop`을 계산한다. undo bar에도 같은 값을 inline top/CSS 변수로 적용한다. `src/styles/features.css:1142-1163`, `src/features/storyboard/Storyboard.jsx:1665-1691`, `src/styles/app.css:130-152`
- N9: `showUndo` 메시지를 항상 `${operationCount}건 변경`으로 만들고, dismiss를 “exiting class 설정 → animation 종료 후 제거”의 두 단계로 바꾼다. 현재 timer는 즉시 state를 제거해 fade할 시간이 없다. `src/features/storyboard/Storyboard.jsx:1863-1892`, `src/features/storyboard/Storyboard.jsx:2835-2854`

훅 주의: 1-05/1-09의 request ref, N9의 exiting state나 timer를 추가한다면 모두 Storyboard의 state/ref 구역에 두고 `1838`의 early return 아래에는 새 hook을 만들지 않는다. `src/features/storyboard/Storyboard.jsx:1615-1659`, `src/features/storyboard/Storyboard.jsx:1838-1844`

신규 테스트:

- compose mode 최신 실패만 롤백, 실패 modal 유지, 동일 값 재시도, flush 실패 시 PATCH/reload 미호출. 기존 helper 테스트를 확장한다. `tests/frontend/compose-mode-location.test.mjs:46-101`
- 카피 토글 Enter/Space 조작, 최신 실패만 롤백, 토스트 1회. `src/components/ui.jsx:176-178`, `src/store/useAppStore.js:421-425`
- 44px computed target과 136px 카드에서 2×2 액션 비침범. `src/styles/features.css:2048-2055`, `src/styles/features.css:2286-2312`
- 899px/560px에서 비용 표시 유지. `src/styles/features.css:1229-1241`
- 404는 보관함 CTA·재시도 없음, 네트워크는 retry 표시. `src/features/storyboard/Storyboard.jsx:1699-1766`
- 리본 0/1/2개와 resize 후 undo top offset, fade 및 reduced-motion 즉시 제거. `src/features/storyboard/Storyboard.jsx:1665-1691`, `src/styles/features.css:1142-1163`

### B2 — 자동저장 개편

① 현재 직렬화 구조는 유지해야 한다. `sbSaveNow`는 모든 PUT을 하나의 모듈 promise chain에 붙이고, 실행 시점의 snapshot을 읽으며, 성공한 동일 snapshot은 생략한다. 새 lifecycle flush도 반드시 이 함수를 거쳐야 구 PUT이 최신 PUT 뒤에 도착하는 문제를 피할 수 있다. `src/features/storyboard/storyboardPersistence.js:4-35`

② 디바운스는 `1500`을 `10000`으로 바꾸되 `directSaveSnapshots` skip과 첫 로드 skip은 유지한다. undo는 `setBlocks`로 이전 snapshot을 다시 만들기 때문에 10초 타이머가 재예약되고, 이미 비행 중인 저장이 있더라도 chain 뒤에 undo snapshot이 줄선다. `src/features/storyboard/Storyboard.jsx:1788-1810`, `src/features/storyboard/Storyboard.jsx:1897-1902`

③ 계획서의 “`sbPending` 재시도 체인이 이미 있음”은 현재 코드와 다르다. 실패 시 Map에 snapshot만 넣고, 실제 재시도는 이후 다른 저장 호출이나 재진입 reconciliation이 있어야 일어난다. `src/features/storyboard/storyboardPersistence.js:18-34`, `src/features/storyboard/Storyboard.jsx:1718-1738`

따라서 persistence 모듈에 silent retry scheduler를 추가해야 한다.

- 실패 시 project별 backoff timer 예약.
- timer 실행 시점에 `sbPending.get(projectId)`가 여전히 같은 snapshot일 때만 `sbSaveNow` 재호출.
- `online` 이벤트에서는 대기 시간을 건너뛰고 재시도.
- atomic 저장 실패는 호출부가 pending을 삭제하고 UI를 rollback하므로 timer가 Map 재검사 후 no-op해야 한다. `src/features/storyboard/Storyboard.jsx:1965-1970`, `src/features/storyboard/Storyboard.jsx:2000-2007`

④ `pagehide`와 `visibilitychange:hidden` hook은 `latestBlocks`, `pidRef`, 기존 timer를 사용하며 early return 위에 둔다. 언마운트 cleanup도 같은 `flushLatest(pid,{keepalive:true})`를 호출하게 통합해 double-fire를 `sbLastSaved` identity 비교로 흡수한다. `src/features/storyboard/Storyboard.jsx:1789-1796`, `src/features/storyboard/Storyboard.jsx:1812-1817`, `src/features/storyboard/storyboardPersistence.js:22-30`

⑤ HTTP 어댑터는 현재 `saveStoryboard`의 options를 버리고 일반 fetch만 호출한다. `http()`에 `keepalive` 옵션을 추가하고 `saveStoryboard`가 이를 전달해야 1-07이 실제 배선된다. mock은 이미 options를 받으므로 영향이 없다. `src/lib/api/httpAdapter.js:60-100`, `src/lib/api/httpAdapter.js:475-477`, `src/mock/api.js:509-515`

⑥ 상단 save banner를 단순 삭제하면 명시적 작업 실패까지 사라진다. 배경 autosave 실패만 조용히 만들고, 다음 단계 flush 실패는 페이지 이동을 막은 채 토스트, atomic picker 실패는 기존 inline error, 세트 변경 실패는 set picker error로 남겨야 한다. 현재 하나의 `saveError`가 이 경로들을 모두 공유한다. `src/features/storyboard/Storyboard.jsx:1752-1762`, `src/features/storyboard/Storyboard.jsx:1930-1973`, `src/features/storyboard/Storyboard.jsx:2410-2414`, `src/features/storyboard/Storyboard.jsx:2797-2812`, `src/features/storyboard/Storyboard.jsx:2856-2858`

신규 테스트:

- fake timer 기준 9,999ms 미저장/10,000ms 저장, 연속 편집 latest snapshot 1건. `src/features/storyboard/Storyboard.jsx:1797-1810`
- 지연된 첫 PUT 뒤에 최신 snapshot이 직렬 저장되는 순서. `src/features/storyboard/storyboardPersistence.js:18-35`
- 실패→pending→silent retry 성공, atomic rollback이 pending을 지우면 retry no-op. `src/features/storyboard/storyboardPersistence.js:28-34`, `src/features/storyboard/Storyboard.jsx:1965-1970`
- hidden/pagehide/unmount가 같은 pid와 최신 snapshot을 전달하고 중복 PUT을 만들지 않는지 검증. `src/features/storyboard/Storyboard.jsx:1789-1817`
- keepalive 옵션이 `http()` fetch까지 전달되는 adapter 테스트. `src/lib/api/httpAdapter.js:60-100`, `src/lib/api/httpAdapter.js:475-477`
- 재진입 GET이 계속 `sbSaveIdle` 뒤에 실행되는지 검증. `src/features/storyboard/Storyboard.jsx:1714-1715`

### B3 — 컷 추가·드래그·내 이미지

- 1-01: `pickAnyImage`를 `CLIENT_ONLY`에서 제거하고 `pickRefImage`의 file picker/upload 공통 helper를 재사용한다. 계획서의 `pickRefImage:698`은 현재 `733-749`로 드리프트했다. `src/lib/api/index.js:16-20`, `src/lib/api/httpAdapter.js:733-749`
- 모든 Storyboard 호출은 `api.pickAnyImage(projectId)`로 바꾸고 `{assetId,url}` 중 URL만 `ownImages/thumb`에 저장한다. `MineImageTab`은 이미 취소 null을 return하지만 `addMineBlock` 자체도 방어해야 한다. `src/features/storyboard/Storyboard.jsx:662-671`, `src/features/storyboard/Storyboard.jsx:1075-1077`, `src/features/storyboard/Storyboard.jsx:1260-1268`, `src/features/storyboard/Storyboard.jsx:2187-2199`
- Editor 의류 업로드는 이미 `uploadPhoto → uploaded.url` 실경로를 사용하므로 코드 변경 대상이 아니라 회귀 테스트 대상이다. 이 부분은 “계획서 기재와 다름”이다. `src/features/editor/Editor.jsx:1265-1309`
- N4: 일반 `addBlock`에만 `exampleChoice:'manual'`을 넣고 `assignGenerationExamples`가 이를 건너뛴다. 초기 진입의 두 자동배정 호출은 그대로 두어 기존 보드 자동배치를 유지한다. `src/features/storyboard/Storyboard.jsx:1483-1500`, `src/features/storyboard/Storyboard.jsx:2127-2166`, `src/lib/generationExamples.js:177-216`
- 서버 canonicalizer는 block dict를 복사하고 `exampleChoice`를 제거하지 않으며, blank standalone block은 example/space 검증 대상이 아니다. 따라서 신규 필드는 HTTP round-trip에 안전하다. `server/app/agents/content_roles.py:161-188`, `server/app/routes.py:1939-1959`, `server/app/repo.py:1817-1826`
- 사용자가 예시를 고르면 `generationExampleSelectionPatch`에서 `exampleChoice:null`을 함께 반환해 manual 대기 상태를 종료한다. `src/lib/storyboardExampleSelection.js:9-27`
- N5: drag source와 dataTransfer는 이미 배선돼 있고 addzone drop도 `addBlock`으로 이어진다. 빠진 부분은 `.drop-on`에서 양옆 이동 CSS가 발화하지 않는 점과 terminal hit area다. `src/features/storyboard/Storyboard.jsx:918-926`, `src/features/storyboard/Storyboard.jsx:2223-2253`, `src/styles/features.css:2399-2436`

신규 테스트:

- HTTP picker의 파일 취소는 null, 선택은 upload 1회 및 `{assetId,url}` 반환; mock placeholder 유지. `src/lib/api/httpAdapter.js:733-749`, `src/mock/api.js:644-647`
- 취소 시 빈 `ownImages:[null]` 블록이 생기지 않는다. `src/features/storyboard/Storyboard.jsx:2187-2199`
- 초기 시드는 자동배정되지만 일반 추가 컷은 reload 뒤에도 manual-empty로 남는다. `src/features/storyboard/Storyboard.jsx:1483-1500`, `src/lib/generationExamples.js:177-216`
- 생성예시 drag dataTransfer→addzone→정확한 index 삽입, `.drop-on` 양옆 이동 및 reduced-motion 비이동. `src/features/storyboard/Storyboard.jsx:918-926`, `src/styles/features.css:2426-2436`, `src/styles/features.css:2617-2630`
- Editor 기존 업로드 경로 회귀. `src/features/editor/Editor.jsx:1265-1309`

### B4 — 매칭·색상

- N6: `sb-match-chip`을 캡션 최좌측으로 이동하고 흰 배경·검정 테두리로 바꾼다. `src/features/storyboard/Storyboard.jsx:258-280`, `src/styles/features.css:2383-2394`
- N7: `prepareStoryboardEntry`의 label 파생 한 곳만 `swatch label → name → 색상 N`으로 바꾼다. `src/features/storyboard/Storyboard.jsx:1501-1505`
- N8: 수직 3열 grid를 가로 flex/auto-flow와 `overflow-x:auto`로 바꾸고 클릭은 `on ? [] : [m.id]`로 한다. 배열 저장 shape는 유지된다. `src/features/storyboard/Storyboard.jsx:1381-1389`, `src/styles/features.css:2006-2010`
- N6과 N8은 `StoryboardCaption`이 이미 `matchIds[0]`만 읽으므로 서로 자연스럽게 맞물린다. `src/features/storyboard/Storyboard.jsx:247-249`

신규 테스트:

- swatch label 우선, unknown swatch는 name, 둘 다 없으면 색상 N; mock/http 동일 product shape. `src/features/storyboard/Storyboard.jsx:1501-1505`
- matching 선택 전후 캡션 DOM 순서와 흰색/검정 테두리 CSS. `src/features/storyboard/Storyboard.jsx:258-280`
- 후보 A→B 클릭 시 `[B]`, B 재클릭 시 `[]`, 복수 ID가 저장되지 않음. `src/features/storyboard/Storyboard.jsx:1383-1388`
- 가로 스크롤·키보드 포커스가 인스펙터 세로 스크롤과 충돌하지 않음. `src/styles/features.css:2006-2010`

### B5 — 공간세트 단순화

구현 순서는 `순수 detach/rekey helper → 이동 경로 → addzone 통합 → renderSpaceRun → 메뉴/CSS 삭제`가 안전하다. 모든 보드 mutation이 먼저 서버-valid 배열을 만들게 한 뒤 UI를 바꾸면 중간 단계에서 저장 불가 상태를 만들지 않는다. `src/lib/storyboardSpaceSets.js:31-144`, `server/app/agents/space_set_assets.py:615-620`

Undo는 새로 확대하지 않는다. 세트 교체는 기존 `atomicBoardChange`의 스냅샷 undo를 유지하고, drag/add/순서 변경은 현재처럼 undo를 추가하지 않는다. 묶음 풀기 삭제로 해당 atomic undo 경로만 함께 사라진다. `src/features/storyboard/Storyboard.jsx:1979-2011`, `src/features/storyboard/Storyboard.jsx:2356-2419`

신규 테스트:

- `[set A, set A, mine, set A]`를 detach/rekey하면 앞 run은 기존 ID, 뒤 run은 같은 set ID의 새 instance이며 서버 연속성 검사를 만족한다. `src/lib/storyboardSpaceSets.js:31-52`, `src/lib/storyboardSpaceSetCatalog.js:251-268`, `server/tests/test_space_set_assets.py:458-475`
- 동일 ID가 떨어진 두 run을 허용하던 기존 프런트 테스트는 “시각 분리 + 저장 전 재키” 계약으로 갱신한다. `tests/frontend/storyboard-space-sets.test.mjs:50-64`
- mine 전환이 현재 위치를 유지하고 양옆 공간 run을 분리한다. 현재 “끝으로 이동” 테스트도 새 결정에 맞게 변경한다. `src/features/storyboard/Storyboard.jsx:1919-1924`, `tests/frontend/storyboard-space-sets.test.mjs:457-469`
- 일반 기존 컷 drop은 세트 가입 없이 경계를 나누고, 신규 내부 plus/예약만 세트 가입한다. `src/features/storyboard/Storyboard.jsx:2070-2166`, `src/features/storyboard/Storyboard.jsx:2297-2312`
- 추가 affordance가 `StoryboardInsertControl` 한 종류뿐이며 empty/terminal/set-internal에서 모두 동작한다. `src/features/storyboard/Storyboard.jsx:199-224`, `src/features/storyboard/Storyboard.jsx:2454-2480`
- 묶음 풀기 메뉴·`dissolveSpaceSet` 호출·예약 카드 class가 소스에서 사라졌는지 확인한다. `src/features/storyboard/Storyboard.jsx:52-60`, `src/features/storyboard/Storyboard.jsx:2416-2424`, `src/features/storyboard/Storyboard.jsx:2600-2619`
- 예약 addzone은 `spaceSetMemberOrder/refScope:'pose'`를 유지하고 소진 후 일반 manual add로 전환한다. 기존 예약 테스트를 UI가 아닌 addzone 계약으로 갱신한다. `tests/frontend/storyboard-space-sets.test.mjs:372-432`
- mock 조립기와 HTTP save/reload 스모크에서 순서·mine·row layout이 동일하게 복원되는지 확인한다. `src/mock/db.js:316-356`, `server/app/agents/page_assembler.py:410-435`

---

## ⑤ 위험/보완 제안

1. 가장 큰 위험은 계획서의 동일-ID run 분리다. 프런트 렌더 테스트만 보면 지원되는 것처럼 보이지만 HTTP PUT은 거부한다. 오너의 “시각적으로 끊긴다”와 “공간세트 의미를 유지한다”를 지키려면 뒤 run만 같은 set의 새 instance ID로 재키해야 한다. exact ID 값까지 고정하려면 서버 변경이 불가피하다. `docs/superpowers/specs/2026-08-13-storyboard-qa-fixes.md:85-98`, `server/app/agents/space_set_assets.py:615-620`

2. N4와 공간 내부 addzone에는 계약 충돌이 있다. 일반 추가 컷은 manual-empty로 저장 가능하지만, `spaceGroupId`를 가진 빈 컷은 서버 pose 검증에서 실패한다. 공간 내부 addzone은 기존 reservation을 명시적 예외로 쓰고, 일반/외부 addzone만 manual-empty로 만드는 것이 서버 수정 없이 두 결정을 지키는 방법이다. `docs/superpowers/specs/2026-08-13-storyboard-qa-fixes.md:47-51`, `src/lib/storyboardSpaceSets.js:66-80`, `server/app/agents/space_set_assets.py:356-369`

3. manual-empty 컷은 현재 “다음” 버튼을 막지 않는다. 생성 게이트는 `contentRole/cutType`만 검사하며 `exampleId`는 검사하지 않는다. N4가 반드시 사용자 선택을 요구한다면 `exampleChoice==='manual' && !exampleId`도 CTA disabled와 함수 방어 조건에 포함해야 한다. `src/features/storyboard/Storyboard.jsx:2797-2812`, `src/features/storyboard/Storyboard.jsx:2873-2875`

4. `exampleChoice`는 현재 기본 콘티 지문에 포함되지 않는다. 사용자가 다른 컷을 지워 결과적으로 기본 시드와 같은 형태가 된 경우 manual 정책을 무시한 채 “손대지 않은 기본 콘티”로 오인될 수 있으므로 fingerprint에 포함하는 편이 안전하다. `src/lib/api/shapes.js:194-230`, `src/lib/generationExamples.js:230-236`

5. 1-04의 계획 문구와 달리 `sbPending`은 재시도 스케줄러가 아니다. 배너만 먼저 제거하면 실패 snapshot은 재진입 전까지 메모리에만 남고 탭 종료 시 사라질 수 있다. 반드시 silent retry를 먼저 구현한 뒤 배너를 제거해야 한다. `docs/superpowers/specs/2026-08-13-storyboard-qa-fixes.md:21-22`, `src/features/storyboard/storyboardPersistence.js:7-34`

6. pagehide에서 별도 beacon을 직렬 체인과 병렬로 보내면 먼저 시작한 오래된 PUT이 나중에 도착해 최신 값을 덮을 수 있다. 반대로 chain 뒤에만 줄이면 이미 비행 중인 저장 때문에 pagehide 순간 바로 fetch가 시작되지 않을 수 있다. 서버 버전 필드가 없는 현재 계약에서는 모든 storyboard PUT을 keepalive 가능하게 만들고, `visibilitychange:hidden`에서 일찍 enqueue하며, pagehide는 같은 snapshot을 중복 보강하는 방식이 가장 안전하다. `src/features/storyboard/storyboardPersistence.js:4-35`, `src/lib/api/httpAdapter.js:475-477`

7. 묶음 풀기를 삭제하면서 `applySingleMove`의 “먼저 묶음을 풀라”는 차단을 남기면 해제 수단이 없는 dead-end가 된다. 교차 그룹 이동은 detach로 바꿔야 한다. `src/features/storyboard/Storyboard.jsx:2261-2267`, `src/features/storyboard/Storyboard.jsx:2416-2424`

8. mine 보호 로직도 현재 이중 구현으로 깨져 있다. pure helper는 mine을 공간세트에 넣지 않지만 Storyboard가 직후 ID를 무조건 다시 설정한다. §C 작업 시 반드시 `2305-2310`을 제거하지 않으면 mine이 경계를 나누지 못한다. `src/lib/storyboardSpaceSets.js:116-128`, `src/features/storyboard/Storyboard.jsx:2305-2310`

9. B1/B2에서 새 request state, lifecycle effect, fade state를 early return 아래에 두면 로드 상태에 따라 hook 수가 달라진다. 모든 hook은 현재 state/ref/effect 구역인 `1610-1837` 안에 추가해야 한다. `docs/superpowers/specs/2026-08-13-storyboard-qa-fixes.md:7-8`, `src/features/storyboard/Storyboard.jsx:1610-1659`, `src/features/storyboard/Storyboard.jsx:1838-1844`

10. 1-10을 공용 버튼 CSS 한 줄로 처리하면 네 액션이 작은 카드 폭을 넘는다. 44px hit target을 지키되 group 카드 액션은 2×2 배치로 만드는 것이 오너 결정을 유지하면서 겹침을 피한다. `src/features/storyboard/Storyboard.jsx:285-306`, `src/styles/features.css:2048-2055`, `src/styles/features.css:2286-2312`

---

## ⑥ 계획서(2026-08-13-storyboard-qa-fixes.md)에 추가할 문구

### §C 저장 계약 보정

“동일 `spaceGroupId`가 비연속 위치에 남는 상태는 실서버의 `space_set_members_not_contiguous` 검증에 의해 저장될 수 없다. 개별컷이 세트를 가르면 첫 run은 기존 ID를 유지하고 뒤 run은 동일 set ID의 새 instance `spaceGroupId`로 재키한다. 이는 공간세트 종류·`spaceVariation`·`refScope:'pose'` 의미를 보존하면서 각 저장 group을 연속하게 만든다. 수용 기준의 ‘동일 ID 유지’는 ‘같은 공간세트 의미 유지, 뒤 run은 새 instance ID’로 정정한다.” `server/app/agents/space_set_assets.py:545-558`, `server/app/agents/space_set_assets.py:615-620`, `src/lib/storyboardSpaceSetCatalog.js:251-268`

### §C 구현 앵커 보정

“현재 정확한 앵커는 run 렌더 `Storyboard.jsx:522-539`, 묶음 풀기 `:2416-2424`, 공간 run 렌더 `:2581-2623`, 트레이 예약 추가 카드 `:2609-2619`, 외부 ghost 추가 카드 `:2683-2693`이다. 트레이·ghost 두 추가 UI는 `StoryboardInsertControl(:199-224, :2454-2480)` 한 종류로 통합한다.” `src/features/storyboard/Storyboard.jsx:199-224`, `src/features/storyboard/Storyboard.jsx:522-539`, `src/features/storyboard/Storyboard.jsx:2416-2424`, `src/features/storyboard/Storyboard.jsx:2454-2480`, `src/features/storyboard/Storyboard.jsx:2581-2623`, `src/features/storyboard/Storyboard.jsx:2683-2693`

### N4 예외와 생성 게이트

“`exampleChoice:'manual'`은 `requestedExample/reservation`이 없는 일반 사용자 추가 컷에 적용한다. 공간세트 내부 addzone은 저장 시 pose example이 필수이므로 기존 reservation을 명시적 세트 추가 예외로 재사용한다. manual-empty 컷이 남아 있으면 ‘다음’ CTA도 막아 사용자가 예시를 고르도록 한다.” `src/lib/storyboardSpaceSets.js:66-80`, `server/app/agents/space_set_assets.py:356-369`, `src/features/storyboard/Storyboard.jsx:2797-2812`

### N6 확정 결과

“N6의 노란 요소는 `StoryboardCaption` 최우측의 `.sb-match-chip`으로 확정한다. `matchIds[0]`이 생길 때 flex-none 칩이 추가되어 방향·샷·색상 영역을 왼쪽으로 민다. 칩을 caption 최좌측으로 이동하고 흰 배경·검정 테두리로 변경한다.” `src/features/storyboard/Storyboard.jsx:247-280`, `src/styles/features.css:2352-2394`

### N7 확정 결과

“N7의 근본 수정 지점은 ProductInput 저장부가 아니라 `prepareStoryboardEntry`의 `allColorOpts` label 파생 한 곳이다. 우선순위는 swatch catalog label → `color.name` → `색상 N`이다. ProductInput은 `swatchId`만 바꾸고 `name`은 갱신하지 않으며, mock/http 모두 두 필드를 독립 저장한다.” `src/features/product-input/ProductInput.jsx:1010-1012`, `src/features/product-input/saveRouting.js:86-100`, `src/features/storyboard/Storyboard.jsx:1501-1505`

### 1-04/1-07 저장 수명주기 보정

“현재 `sbPending`은 보관 Map일 뿐 자체 재시도 체인이 아니다. B2에서 project별 silent retry scheduler와 online 재시도를 먼저 추가한다. 10초 debounce, hidden/pagehide/unmount flush는 모두 기존 `sbSaveNow` 직렬 체인을 거치고, HTTP `saveStoryboard`가 keepalive 옵션을 실제 fetch까지 전달하게 한다. 배경 저장 실패만 무표시로 하고 명시적 작업 실패는 inline error 또는 toast로 남긴다.” `src/features/storyboard/storyboardPersistence.js:7-35`, `src/features/storyboard/Storyboard.jsx:1788-1817`, `src/lib/api/httpAdapter.js:60-100`, `src/lib/api/httpAdapter.js:475-477`

### B1 훅·토스트 주의

“N3은 전역 toast가 아니라 `.sb-undo-bar`의 위치 문제로 처리한다. 기존 `inspectorTop` 측정값을 undo bar에도 적용해 topnav와 복수 job-ribbon 높이를 공유한다. B1/B2에서 추가되는 state/ref/effect는 모두 Storyboard 로딩 early-return 위에 둔다.” `src/features/storyboard/Storyboard.jsx:1665-1691`, `src/features/storyboard/Storyboard.jsx:1838-1844`, `src/styles/features.css:1142-1163`