# Weavy "The Perfect Character Sheet" 워크플로우 분석 → AI 가상모델 적용 인사이트

> 분석일 2026-07-04. 대상: app.weavy.ai 커뮤니티 플로우 "the perfect character sheet" (사용자 복제본, flow/rnz8NO4dKUpiLyQpq6iLtP).
> 방법: CDP로 캔버스 DOM에서 45노드·41엣지 전문 추출. 원본 덤프: 세션 스크래치 `weavy_charsheet_full.txt`.
> 관련 문서: [weavy_fashion_proto_insights.md](weavy_fashion_proto_insights.md) (다른 플로우 — 플랫레이 렌더링).

## 1. 워크플로우 전체 구조

**용도**: 텍스트 설명 1개로 AI 캐릭터(가상 인물)를 "탄생"시키고, 멀티뷰 캐릭터 시트까지 만드는 파이프라인. 전 생성 노드가 **Gemini 3 (Nano Banana Pro)**.

```
[① Talent Description]  Subject / Style / Context 3분할 프롬프트 → Concatenator
        ↓
[② Enhancer]  LLM(NB Pro Enhancer)이 결합프롬프트 + 의상이미지(image 1)를 받아
              → 의상을 텍스트에 짜넣고 + 구도·조명 스펙 + "No text" 추가한 상세 프롬프트로 재작성
        ↓  Gemini 1792×2400 생성 = 정면 포트레이트 (아이덴티티 원본 탄생)
[③ Editing(선택)]  "20살 늙게+수염" 같은 짧은 의도 → Enhancer가 보존조항
              ("keep all facial features, hairstyle... same lighting, composition") 자동 삽입 → 편집 생성
              → 스위치: 원본/편집본 중 선택해 진행
        ↓
[④ Multipicture Sedcard]  ★ "2x2 멀티픽처 한 장" 생성:
              "show a 2x2 multipicture set of the talent in different angles
               (front, left profile, right profile) and expressions (smiling, witty)"
              → Crop 노드로 각 뷰를 낱장 분해 (스티키: "Choose 4K if you want 2K output after cropping")
        ↓  (분해된 정면/좌측면/우측면이 reroute 노드로 하류에 공급)
[⑤ Single Studio Portrait]  프로덕션 컷:
              "photorealistic full body shot, identity-preserving image of the talent from image 1,
               ... wearing the exact outfit from image 2, ... ensure no text present"
              image 1=캐릭터 크롭, image 2=의상 → 1536×2752
[⑥ Kling Video(유료)]  포트레이트를 first frame으로 핸드헬드 무빙 영상
[⑦ Final Character Sheet]  "3면 전신 A-pose 시트" 생성:
              "character reference sheet ... same individual ... consistent facial identity,
               physique, proportions ... three full-body standing views: front, profile, back,
               neutral A-pose, plain neutral white background, subtle ground shadow,
               soft three-point lighting, no text" → 2400×1792 → Compositor → Export
```

## 2. 핵심 인사이트 (우선순위순)

### A. ★★★ 그리드 생성 → 크롭 분해 (아이덴티티 팩 부트스트랩의 정답)
멀티뷰를 **낱장으로 4번 생성하지 않고, "2x2 한 장"으로 생성 후 Crop으로 분해**한다.
- 한 캔버스 안의 4개 뷰는 모델이 **한 번의 생성에서 같은 인물로 그리므로** 컷 간(cross-image) 드리프트가 원천 차단됨. 크로스 이미지 일관성보다 인-이미지 일관성이 압도적으로 쉬움.
- 해상도 보존: 4K로 생성해야 크롭 후 2K 확보 (스티키 노트 명시).
- **우리 팩 제작 레시피 변경**: 미드저니 `--oref` 낱장 4회 대신 → 기존 정면 베이스컷을 레퍼런스로 Gemini 4K에서 ①"2x2 각도×표정 세드카드" 1장 + ②"3면 전신 A-pose 시트" 1장 생성 → 크롭 → 큐레이션. 생성 2회로 팩 완성. 미드저니는 대안/보완.

### B. ★★★ 얼굴 텍스트 묘사의 정확한 규칙 — "탄생 단계에서만"
이 플로우는 얼굴을 텍스트로 상세 묘사한다(주근깨·광대·눈색…). 단, **레퍼런스 이미지가 아직 없는 '캐릭터 탄생' 단계에서만**. 정면 포트레이트가 생긴 이후의 모든 하류 프롬프트는 "the talent from image 1"로만 지칭하고 얼굴을 다시 묘사하지 않는다.
→ 우리 규칙 정밀화: **생성(with reference) 시 얼굴 텍스트 묘사 금지 / 신규 가상모델 창조 시에만 텍스트 묘사 허용**. 신규 모델을 미드저니 없이 Gemini만으로 만들 때 Subject 블록 서식(피부→골격→이목구비→헤어→표정 순)을 차용.

### C. ★★ 프롬프트 인핸서 패스 (보존조항 자동 삽입 LLM)
짧은 사용자 의도("20살 늙게, 수염")를 LLM이 **보존조항이 포함된 상세 지시문으로 재작성**: "keeping all facial features, hairstyle, and clothing consistent with the original... Maintain the same lighting, background, composition, and camera angle... no text". 사람이 보존조항을 매번 쓰지 않아도 됨.
→ 컷 에이전트의 프롬프트 조립기에 채택: 셀러 입력·컷 스펙을 최종 이미지 프롬프트로 빌드할 때 IDENTITY/보존 조항을 코드가 항상 주입 (LLM 재작성이든 템플릿이든). 의상 통합도 동일 패턴 — enhancer가 "matches the outfit in image 1"처럼 이미지 참조를 텍스트에 짜넣음.

### D. ★★ 차용할 문구 (실증된 identity 문구들)
- 생성 컷: `photorealistic ..., identity-preserving image of the talent from image 1, ... wearing the exact outfit from image 2`
- 캐릭터 시트: `A professional character reference sheet showing the same individual from the uploaded reference image with consistent facial identity, physique, proportions, and clothing design, ... three full-body standing views: front view, profile and back view, each in a neutral A-pose, ... plain neutral white background, subtle ground shadow, soft three-point lighting`
- 전 생성 프롬프트 말미: `no text in image` (이전 플로우의 NO TEXT 규칙과 동일 — 필수)

### E. ★ Subject/Style/Context 3분할 + Concatenator
정체성(Subject) / 사진 스타일(Style) / 배경·조명(Context)을 분리된 재사용 블록으로 관리 후 결합. 컷타입이 바뀌어도 Subject·Style은 고정, Context만 교체 → 우리 블록 조립 구조([[CUT]][[SHOT]] 방식)와 동형. 검증 재확인.

### F. 백로그
- 편집 분기+스위치(원본/편집본 선택): 모델 변형(나이·헤어) 기능의 UX 참고.
- Kling first-frame 영상("handheld, natural micro-shakes, subtle breathing motion"): 상세페이지 영상컷 후보.
- Compositor(레이어 합성)+Export: 최종 시트 배경 합성 방식.

## 3. 우리 스파이크에 반영할 것
1. 팩 제작 조건에 **그리드-크롭 방식 추가** (미드저니 낱장 vs Gemini 2x2 그리드-크롭 비교 — 사실상 후자가 기본값 후보).
2. 전신 레퍼런스는 "3면 A-pose 시트" 프롬프트 전문 차용.
3. 프로덕션 컷 프롬프트에 D의 identity 문구 반영.
4. 유의: 그리드-크롭 뷰는 얼굴이 작게 나올 수 있음 → 얼굴 앵커는 여전히 원본 정면 클로즈업을 항상 포함 (기존 규칙 유지).
