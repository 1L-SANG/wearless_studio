# Wearless ComfyUI 실험 노드

서버(`server/app`)의 이미지 생성·QC 코드를 **그대로 import**해서 ComfyUI 그래프에서
쓰기 위한 얇은 노드 묶음. 프롬프트·QC 로직을 복제하지 않으므로 서버와 결과가 갈리지 않는다.

## 노드

| 노드 | 하는 일 | 재사용하는 서버 코드 |
|---|---|---|
| Wearless: 상태 점검 | 경로·키·모델 기본값 확인 | `config.load_settings` |
| Wearless: 이미지 생성 | Gemini/gpt-image 생성 (모델명으로 자동 분기) | `gemini_image.GeminiImageClient.generate_content_image` |
| Wearless: QC 판정 | 상품사진 대비 동일성 판정(로고·프린트 포함) | `image_qc.verdict` |
| Wearless: best-of 게이트 | 불합격 시 추가 생성 후 최선 후보 채택 | `image_qc.best_of` |
| Wearless: 컷 생성 | 컷 계약(cut_spec)으로 컷 생성 | `cut_generator.build_prompt` / `generate` |
| Wearless: 마네킹 프롬프트 | 마네킹 프롬프트 렌더링 | `prompts.render_mannequin_prompt` |

## 설치

1. 이 저장소를 ComfyUI 가 있는 PC에 clone
2. `wearless_nodes` 폴더를 ComfyUI 의 `custom_nodes/` 로 복사(또는 심볼릭 링크)
3. 환경변수 설정 후 ComfyUI 재시작
   - `WEARLESS_SERVER_DIR` = clone 한 저장소의 `server` 절대경로
   - `GEMINI_API_KEY`, `OPENAI_API_KEY` (워크플로 파일에는 저장되지 않는다)
4. 그래프에 **Wearless: 상태 점검** 노드를 놓고 실행해 `import_error: null` 확인

## 주의

- 실험 전용이다. 프로덕션 파이프라인의 잡 큐·크레딧·재시도는 서버에만 있다.
- 실험에서 프롬프트를 고쳤다면 서버 코드에 반영하는 것까지가 한 사이클이다.
- API 호출은 실제 과금된다. 원장 기록(`image_usage`)은 노드에서 꺼둔다.
