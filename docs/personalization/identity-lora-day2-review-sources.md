# Identity LoRA Day 2 Review Sources

> Scope: 사용자 제공 실험 요약, 평가 설계, 공식 모델 문서와 공개 학습·추론 구현 검토. 실제 생성 이미지, 학습 로그, 사용 중인 ai-toolkit commit과 inference workflow는 확인하지 않았다. 아래의 공개 main 브랜치 동작을 실행 환경의 동작으로 단정하지 않는다.
> 확인일: 2026-09-05

## Request Type

Comprehensive research: SFace 평가 API/모델 근거, 얼굴 인식 평가 프로토콜, pose/illumination/quality bias, 실험 설계 리스크.

## Direct Answer

현재 평가는 longitudinal tracking 용도로는 쓸 수 있지만, 제품 목표의 통과/탈락 판정으로는 편향이 크다. 가장 큰 문제는 홀드아웃 6장이 전부 정면 head-and-shoulders라서 "정면 얼굴로 회귀하는 LoRA"를 과대평가하고, 앉기/돌아봄/전신/따뜻한 실내광 같은 실제 실패 조건을 과소평가한다는 점이다.

기존 SFace 중앙값은 계속 유지하되 이름을 `frontal_gallery_sface_median` 같은 추적 지표로 낮춰야 한다. 제품 합격 지표는 별도로 만들어야 한다: 6장 정면 enrollment gallery는 유지하고, probe는 독립 촬영된 다각도/다조명/다거리 테스트셋으로 나눠 pose, illumination, face size, occlusion별로 stratified report를 내는 편이 낫다.

또 하나의 치명적인 통계 문제: 16개 표본에서 `seed = 42 + i`가 prompt index와 묶여 있으면 prompt 난이도와 seed 운이 분리되지 않는다. 체크포인트 비교에는 유리하지만, "이 설정이 일반적으로 더 좋다"는 결론에는 약하다. 최소한 최종 후보만이라도 prompt별 3-5개 seed를 돌려 confidence interval 또는 bootstrap p10/median을 봐야 한다.

`0.80`과 `0.90+`도 외부적으로 보편적인 의미가 없다. OpenCV의 SFace 문서는 dataset별 cosine threshold가 서로 다름을 보여준다. 따라서 `0.80`은 이 서비스의 내부 operating threshold로 calibration해야 하고, `0.90+ = 암기 의심`은 단독 기준으로 쓰면 안 된다. 암기 의심은 점수보다 train/holdout near-duplicate, recrop leakage, 동일 촬영 burst, 배경/옷/소품 재현, 생성물이 학습 이미지 crop과 직접 닮는지를 함께 봐야 한다.

## Official Docs Evidence

- [OpenCV `cv::FaceRecognizerSF` class reference](https://docs.opencv.org/4.x/da/d09/classcv_1_1FaceRecognizerSF.html) — SFace 사용 흐름은 `alignCrop`으로 얼굴을 정렬/크롭하고 `feature`를 추출한 뒤 `match`로 cosine 또는 L2 거리를 계산한다. 즉 detector/alignment 실패, 얼굴 크롭 품질, landmark 안정성이 점수에 직접 들어간다.
- [OpenCV DNN face detection and recognition tutorial](https://docs.opencv.org/4.x/d0/dd4/tutorial_dnn_face.html) — SFace 예시 threshold가 dataset별로 다르다: LFW cosine 0.363, CALFW 0.340, CPLFW 0.275, AgeDB-30 0.277, CFP-FP 0.212. 따라서 cosine 값은 절대적인 "동일 인물 퍼센트"가 아니며 데이터셋/프로토콜별 calibration이 필요하다.
- [OpenCV Zoo SFace README](https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface) — OpenCV SFace ONNX 모델과 face recognition demo를 제공한다. 검출과 정렬을 거치는 평가이므로 현재 평가에서 YuNet 박스/랜드마크 실패를 별도 분모로 기록해야 한다.
- [NIST FRTE 1:1 Verification](https://pages.nist.gov/frvt/html/frvt11.html) — 얼굴 인증 평가는 FMR/FNMR로 threshold를 잡고 데이터셋별 성능을 본다. NIST도 poor photography, lighting, pitch variation이 false negative에 영향을 준다고 설명한다. 단일 cosine 중앙값만으로 제품 리스크를 닫기 어렵다.
- [NIST FATE Quality](https://pages.nist.gov/frvt/html/frvt_quality.html) — 얼굴 이미지 품질 알고리즘은 pose, illumination, resolution 같은 결함이 recognition error를 예측하는지를 평가한다. 생성 평가도 no-face, 작은 얼굴, 흐림, 조명 mismatch를 score 평균에서 조용히 사라지게 두면 안 된다.
- [NIST Face Challenges: IJB-C](https://www.nist.gov/programs-projects/face-challenges) — IJB-C는 verification, identification, detection, clustering, full-motion video 처리를 포함하고 face images/videos/non-face images를 포함한다. 제품용 평가도 단일 정면 still-image verification보다 넓은 probe 조건이 필요하다.
- [IARPA Janus program](https://www.iarpa.gov/research-programs/janus) — 얼굴 인식은 historically well-lit frontal pose에서 가장 잘 작동하고, poor lighting, low resolution, obstruction, camera angle, expression variation에서 어려워진다고 설명한다. 지금 홀드아웃 구성이 바로 쉬운 쪽에 몰려 있다.

## Source-Reference Evidence

- [SFace upstream repository](https://github.com/zhongyy/SFace) — SFace 논문/코드는 noisy or low-quality training images에 과하게 최적화하지 않도록 intra/inter-class gradient를 조절하는 loss를 제안한다. 여기서 얻을 평가상의 시사점은 단순하다: recognition encoder도 품질 조건에 무관한 oracle이 아니며, 품질/pose strata를 분리해야 한다.
- [OpenCV Zoo `sface.py`](https://github.com/opencv/opencv_zoo/blob/main/models/face_recognition_sface/sface.py) — demo wrapper는 cosine threshold `0.363`을 상수로 둔다. 하지만 OpenCV tutorial은 다른 benchmark에서 threshold가 달라짐을 같이 보여주므로, 이 상수를 제품 threshold로 가져오면 안 된다.
- [InsightFace Python package model zoo](https://github.com/deepinsight/insightface/blob/master/python-package/README.md) — `buffalo_l` 같은 별도 recognition encoder를 독립 평가축으로 둘 수 있다. 단 InsightFace 모델 가중치는 non-commercial research purpose 제한이 있으므로, 제품 자동 판정에 쓰려면 라이선스 확인이 필요하다.

## Practical Protocol

1. 기존 6장 정면 holdout은 enrollment gallery로 유지한다. 단 train에서 나온 recrop, 같은 burst frame, 같은 원본의 crop 파생본은 gallery/probe 양쪽에 걸치지 않게 cluster split한다.
2. probe set은 독립 촬영으로 만든다: 정면, 3/4, 측면에 가까운 yaw, 앉기, 돌아봄, 전신 작은 얼굴, 따뜻한 실내광, 야외광, 표정 변화, 안경/머리카락 가림을 최소 단위로 포함한다.
3. report는 전체 median 하나가 아니라 `median`, `p10`, `fail-rate`를 strata별로 낸다. `n=16`의 p10은 사실상 최악 1-2장에 민감하므로, 최종 후보에서는 prompt별 multi-seed가 필요하다.
4. no-face/detector-fail/multiple-face는 similarity 평균에서 제외하지 말고 별도 실패율로 기록한다. 뒤통수/후면 prompt처럼 얼굴이 없어야 하는 조건은 identity score가 아니라 prompt compliance로 따로 판정한다.
5. SFace 하나만 쓰지 말고 independent encoder 하나를 추가한다. 두 encoder가 같이 오르는지, 한쪽만 오르는지 분리하면 "SFace에 맞춘 얼굴" 착시를 줄일 수 있다.
6. blinded human A/B를 작게 붙인다: 동일 옷/프롬프트에서 prod vs LoRA-inpaint 후보를 섞고, "같은 사람인가"와 "붙인 티가 나는가"를 따로 묻는다. face score가 높아도 머리 경계/피부 조명/표정 보존이 깨지면 제품 실패다.
7. calibration은 target identity만 보면 안 된다. 비슷한 성별/연령/체형의 impostor 생성물을 넣어 target score와 impostor score의 간격을 본다. 사용자가 적은 "타인 최대 0.71"은 좋은 시작이지만, 비슷한 사람 hard negatives가 더 필요하다.

## Caveats / Ambiguity Flags

- `prod 0.763`과 blank-generation `0.67`은 입력 task가 다르다. 전자는 실사 기반 production output이고 후자는 백지 생성이므로 직접적인 난이도 비교가 아니다. 같은 source image에서 inpaint 후보를 비교해야 한다.
- 생성 얼굴과 6장 holdout의 aggregation 방식이 불명확하다. 각 생성물마다 holdout 6장 평균인지, max인지, median인지에 따라 점수 해석이 크게 달라진다. 제품 판정에는 enrollment template embedding 평균 또는 robust mean을 고정하는 편이 낫다.
- `0.90+ = 암기 의심`은 단독 기준으로 부적절하다. 실사끼리 0.853이라는 관측 평균은 개별 점수의 상한이 아니다. 암기는 near-duplicate audit와 train-image retrieval로 따로 봐야 한다.
- prompt와 seed가 confounded되어 있다. 체크포인트 간 비교는 가능하지만, 설정 간 일반화 판단은 약하다.
- SFace/YuNet이 평가 대상 얼굴을 어디서 어떻게 crop했는지 로그가 필요하다. face box area, landmark confidence, yaw/pitch/roll proxy, blur, detector confidence 없이 similarity만 보면 실패 원인을 잃는다.

## Reusable Takeaway

현재 SFace 6장 정면 중앙값은 "같은 쉬운 조건에서 checkpoint 추적" 지표로 남겨라. 제품 합격은 독립 probe, hard negatives, detector failure rate, pose/light/face-size strata, SFace+독립 encoder, blinded human review를 합친 별도 protocol로 판단해야 한다. `0.80`은 외부 표준이 아니라 내부 calibration threshold다.

## Training implementation evidence

- [ai-toolkit SDTrainer](https://github.com/ostris/ai-toolkit/blob/main/extensions_built_in/sd_trainer/SDTrainer.py): normal masked reconstruction and inverted-mask teacher prediction loss are separate terms. The normal mask and inverted prior mask are normalized by their respective means. A nonzero outside floor therefore retains supervision toward the training target outside the face while the prior also pulls toward the base prediction. A training loss mask does not spatially restrict LoRA parameter changes at inference.
- Arithmetic illustration, not a measured gradient ratio: if the face occupies fraction f of the image, face weight is 1, outside weight is 0.1, and per-pixel errors are equal, outside/inside weight mass is `0.1*(1-f)/f`. At f=0.05 this is 1.9. Common mean normalization does not change that ratio. Feathering and actual prediction errors change the real contribution.
- [Custom flowmatch sampler](https://github.com/ostris/ai-toolkit/blob/main/toolkit/samplers/custom_flowmatch_sampler.py): `weighted` uses a linear timestep grid and a separate weight lookup. [BaseSDTrainProcess](https://github.com/ostris/ai-toolkit/blob/main/jobs/process/BaseSDTrainProcess.py) selects indices according to `content_or_style`; balanced uses uniform indices. Thus timestep sampling and loss weighting should be audited separately.
- [Default weighting table](https://github.com/ostris/ai-toolkit/blob/main/toolkit/timestep_weighing/default_weighing_scheme.py) says its weights were calculated with flex.1-alpha. It is not evidence of a Qwen identity-specific optimal distribution.
- With ordinary cosine annealing, initial LR 1e-4, minimum 1e-5, and T_max=1500, LR at step1200 is about 1.8594e-5. The late flattening is compatible with a low learning rate; it does not prove a representational ceiling. This calculation is conditional on the actual scheduler semantics.
- v2/v3 changed crops, mask, prior, scheduler, and early accumulation. Causal contributions are unidentified without ablations. The 22 recrops add views of existing originals, not 22 independent captures. Compare effective image exposures and actual optimizer updates, not the displayed step alone.

## Model alternatives and inference evidence

- [Qwen-Image-Edit-2509 model card](https://huggingface.co/Qwen/Qwen-Image-Edit-2509) documents improved human identity consistency and multi-image editing. Testing enrolled identity references with the existing base is a useful no-training baseline.
- [Diffusers EditPlus source](https://github.com/huggingface/diffusers/blob/main/src/diffusers/pipelines/qwenimage/pipeline_qwenimage_edit_plus.py): the standard pipeline has no `mask_image` or `strength` argument. Output latents are initialized from noise; input images provide conditioning. An edit input is not automatically an img2img latent initialization.
- [Diffusers EditInpaint source](https://github.com/huggingface/diffusers/blob/main/src/diffusers/pipelines/qwenimage/pipeline_qwenimage_edit_inpaint.py): a separate pipeline implements masking and strength. Its documented example uses the original `Qwen/Qwen-Image-Edit`, not 2509 Plus; this review does not establish drop-in 2509/LoRA compatibility. Its mask processor uses `do_binarize=True`, so inference feathering must be verified independently of training feathering.
- [PuLID paper](https://arxiv.org/abs/2404.16022) and [official FLUX notes](https://github.com/ToTheBeginning/PuLID/blob/main/docs/pulid_for_flux.md): identity-conditioned generation is a useful independent baseline, but official FLUX adapters target a different base family than Qwen.
- [InstantID](https://github.com/instantX-research/InstantID) and [IP-Adapter FaceID](https://huggingface.co/h94/IP-Adapter-FaceID): reference identity conditioning is not equivalent to pasting a face. Their official releases are not Qwen plug-ins; increased identity conditioning can trade against text/edit control. Released checkpoints/dependent InsightFace weights carry research/noncommercial restrictions that need separate commercial authorization.
- [BFL Kontext documentation](https://docs.bfl.ai/kontext/kontext_image_editing): local editing and character consistency make Kontext a stage-one comparison candidate, not evidence that it exceeds a particular SFace score on this person.
- [Diffusers LoRA documentation](https://huggingface.co/docs/diffusers/training/lora): rank, alpha, and target modules are separate configuration choices. In an ordinary alpha/rank scaling implementation, compare rank16/alpha16 against rank32/alpha32 to avoid changing scale at the same time. More parameters do not identify identity versus garment when the dataset has no garment variation.

## Proposed next experiment (recommendation, not a completed run)

Use matched production inputs with an unchanged-input control, an encode/decode-and-crop-only diagnostic, and LoRA-off versus v3-1200/v3-1500 under the same masked inference path. Include a lower denoise point around 0.2 alongside the proposed 0.4/0.5/0.6. Screen a small set of hard conditions, then rerun the chosen configuration on unused clothing, independent source captures, and 3-5 shared seeds per case. Record per-input similarity change, final native-resolution score, expected-face detection failures, pose/expression drift, clothing preservation, and blinded naturalness judgments. Do not interpret a higher median alone as success.
