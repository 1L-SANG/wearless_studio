# us-east-1 ECS Fargate 비용·CPU 적정화

조사일: 2026-08-26 (Asia/Seoul)
범위: Linux/x86_64·Linux/ARM64 Fargate On-Demand/Spot, API `0.5 vCPU / 4 GB`, 상세페이지 worker `1 또는 2 vCPU / 4 GB`
성격: AWS 공식 가격을 사용한 **계산 추정치**. 현재 서울 리소스의 실제 청구액이 아니다.

현재 실행 위치 `ap-northeast-2`는 이전 배치의 비교 기준이고, 목표 리전은 `us-east-1`이다. 조사 시점에 `us-east-1`에는 기존 ECS cluster가 없었으므로 리전 변경은 task definition의 리전만 바꾸는 작업이 아니다. ECR 이미지, VPC/subnet/security group, ALB/DNS/인증서, secrets, 로그, 데이터 접근 경로를 함께 재구성하고 리전 간 데이터 전송 비용을 별도로 확인해야 한다.

## 결론

- 최우선은 호출 전·후 동기 이미지 변환을 `asyncio.to_thread`로 옮기고 로컬 CPU 동시성을 제한해 이벤트 루프의 직접 점유를 제거하는 것이다. `1 vCPU` 증설은 처리시간을 줄이는 보조책일 뿐 ALB health 응답을 보장하지 않는다. 코드 배포가 불가능한 당일 응급조치라면 `DETAIL_CUT_CONCURRENCY=2`가 무료지만, 5개 동시 호출이 필수라면 임시 조치로만 쓴다.
- 분리 후의 비용 최적점 후보는 **API: ARM `0.5/4` On-Demand**, **worker: x86 `1/4` Spot scale-to-zero**다. API ARM 호환성과 부하 테스트를 먼저 통과해야 한다.
- AWS Fargate 자체에는 ARM Spot 가격이 있지만 AWS Copilot 서비스 매니페스트는 현재 ARM 컨테이너의 Fargate Spot을 지원하지 않는다고 명시한다. 이 제한을 우회해도 60시간 기준 x86 Spot 대비 절감액은 약 `$0.22`뿐이라 현재는 우회하지 않는다.
- provider 호출 5개가 동시에 대기한다고 `5 vCPU`가 필요한 것은 아니다. 현재 호출은 async 네트워크 I/O다. CPU는 호출 전 레퍼런스 JPEG→PNG 변환과 요청 구성, 응답 후 JSON/base64 처리·이미지 QC 양쪽에서 필요하며, 실장애의 직접 동결 증거는 호출 전 변환에 더 강하게 맞는다.

## 공식 단가와 시점

| 청구 차원 | x86 On-Demand | ARM On-Demand | x86 Spot | ARM Spot |
|---|---:|---:|---:|---:|
| vCPU-hour | $0.04048000 | $0.03238000 | $0.01260682 | $0.01008421 |
| GB-hour | $0.00444500 | $0.00356000 | $0.00138432 | $0.00110870 |

- On-Demand는 AWS Price List의 us-east-1 버전 원본이다. 가격 조건 effective date는 `2026-07-01T00:00:00Z`, 파일 publication date는 `2026-07-07T16:06:51Z`다.
- Spot은 AWS Fargate 가격 페이지가 읽는 공식 가격 원본을 2026-08-26에 조회했다. HTTP `Last-Modified`는 `2026-08-26 05:17:39 GMT`, ETag는 `39417ff98ca5263c6965142fce104f30`이었다. Spot은 수급에 따라 변하므로 아래 계산은 그 시점의 스냅샷이다.
- 실행 중인 public IPv4는 상용 리전 공통 **$0.005/IP-hour**다. 아래 표는 각 태스크가 public IPv4 하나를 쓴다고 가정해 포함했다.

계산식은 다음과 같다.

```text
시간당 컴퓨트 = vCPU 수 × vCPU 단가 + 메모리 GB × GB 단가
예상 합계(h) = h × (시간당 컴퓨트 + public IPv4 $0.005)
절감액 = 같은 사양 x86 On-Demand 예상 합계 - 비교안 예상 합계
```

Fargate 가격 외에 ALB, CloudWatch Logs, 데이터 전송, ECR, NAT Gateway, 세금은 제외했다. AWS가 게시한 단가는 **인용 단가**, 아래 합계는 **산술 계산 추정치**다.

## API: 0.5 vCPU / 4 GB, 월 730시간

| 실행 방식 | 컴퓨트/월 | public IPv4 | 계산 합계 | x86 On-Demand 대비 절감 |
|---|---:|---:|---:|---:|
| x86 On-Demand | $27.7546 | $3.6500 | **$31.4046** | 기준 |
| ARM On-Demand | $22.2139 | $3.6500 | **$25.8639** | $5.5407 (17.64%) |
| x86 Spot | $8.6437 | $3.6500 | **$12.2937** | $19.1109 (60.85%) |
| ARM Spot | $6.9181 | $3.6500 | **$10.5681** | $20.8365 (66.35%) |

Spot 두 행은 비용 상한 비교용이다. 현재처럼 API가 한 태스크이고 dispatcher까지 품으면 Spot 중단 시 웹과 작업 소비자가 함께 사라진다. 최소 On-Demand API 한 개를 유지해야 한다.

### 상시 결합 태스크의 CPU별 On-Demand 월 추정

| 사양 | x86 합계 | ARM 합계 | ARM 절감 |
|---|---:|---:|---:|
| 0.5 vCPU / 4 GB | $31.4046 | $25.8639 | $5.5407 (17.64%) |
| 1 vCPU / 4 GB | $46.1798 | $37.6826 | $8.4972 (18.40%) |
| 2 vCPU / 4 GB | $75.7302 | $61.3200 | $14.4102 (19.03%) |

모두 730시간과 public IPv4 하나를 포함한 계산이다. x86 `0.5→1 vCPU`의 증분은 월 **$14.7752**, `1→2 vCPU`의 증분은 월 **$29.5504**다. CPU 증설은 동기 구간을 짧게 만들 수 있지만 이벤트 루프를 계속 막는 구조는 남으므로, 동기 작업 offload와 CPU 동시성 제한 뒤의 부하 테스트에서만 크기 조정 근거로 쓴다.

## 상세 worker: 활성 시간만 실행한다고 가정

현재처럼 dispatcher가 상시 API 프로세스 안에서 실행되면 worker만 30/60/120시간 청구되는 구조가 아니다. 아래 표는 worker를 별도 ECS Service/RunTask로 분리하고 유휴 시 0개까지 내릴 수 있을 때만 성립한다. 각 셀은 `예상 합계 (절감액, 절감률)`이며, 절감 기준은 같은 시간·사양의 x86 On-Demand다.

### 1 vCPU / 4 GB

| 활성 시간 | x86 On-Demand | ARM On-Demand | x86 Spot | ARM Spot |
|---:|---:|---:|---:|---:|
| 30h | $1.8978 | $1.5486 ($0.3492, 18.40%) | $0.6943 ($1.2035, 63.41%) | $0.5856 ($1.3122, 69.14%) |
| 60h | $3.7956 | $3.0972 ($0.6984, 18.40%) | $1.3886 ($2.4070, 63.41%) | $1.1711 ($2.6245, 69.14%) |
| 120h | $7.5912 | $6.1944 ($1.3968, 18.40%) | $2.7773 ($4.8139, 63.41%) | $2.3423 ($5.2489, 69.14%) |

### 2 vCPU / 4 GB

| 활성 시간 | x86 On-Demand | ARM On-Demand | x86 Spot | ARM Spot |
|---:|---:|---:|---:|---:|
| 30h | $3.1122 | $2.5200 ($0.5922, 19.03%) | $1.0725 ($2.0397, 65.54%) | $0.8881 ($2.2241, 71.46%) |
| 60h | $6.2244 | $5.0400 ($1.1844, 19.03%) | $2.1451 ($4.0793, 65.54%) | $1.7762 ($4.4482, 71.46%) |
| 120h | $12.4488 | $10.0800 ($2.3688, 19.03%) | $4.2901 ($8.1587, 65.54%) | $3.5524 ($8.8964, 71.46%) |

public IPv4가 비용 절감률을 희석한다. 예를 들어 API ARM On-Demand의 컴퓨트 자체는 x86 대비 약 19.96% 저렴하지만, 동일한 IPv4 비용을 포함하면 총절감률은 17.64%다. private subnet으로 옮길 때는 NAT Gateway의 시간·처리 비용을 별도로 비교해야 하므로 public IP 제거가 자동 절감이라는 뜻은 아니다.

## 호출 동시성 5와 CPU의 관계

코드상 생성 호출은 `httpx.AsyncClient`의 `await client.post(...)`이고, 컷들은 `asyncio.Semaphore`와 `asyncio.gather`로 조절된다.

- 네트워크 응답 대기 5개는 CPU 코어 5개를 점유하지 않는다. `1 vCPU`에서도 여러 I/O를 동시에 기다릴 수 있다.
- 다만 세마포어가 provider 요청 한 줄만이 아니라 컷 파이프라인 전체를 감싼다. 호출 전에 각 컷의 레퍼런스가 JPEG→PNG로 동기 변환되고, 여러 응답이 비슷한 때 도착하면 `res.json()`, base64 decode, QC 패킷/그리드 구성도 단일 이벤트 루프에서 몰릴 수 있다.
- 컨테이너 명령은 worker 수를 지정하지 않은 단일 Uvicorn 프로세스다. vCPU를 2로 올려도 Python 동기 구간이 자동으로 2개 코어에 고르게 퍼진다고 보장할 수 없다. OpenCV 등 native 코드의 내부 스레딩은 별도 실측 대상이다.
- 따라서 `provider concurrency=5`와 `task vCPU=5`를 연결하면 안 된다. Fargate의 관련 유효 CPU 크기도 `0.5, 1, 2, 4 vCPU`처럼 정해져 있어 5 vCPU 사양은 없다.

근거 코드:

- provider async 호출: `server/app/agents/gemini_image.py:323-327`
- PNG 동기 변환: `server/app/agents/gemini_image.py:67-84`, `306-310`
- 응답 JSON/base64 동기 처리: `server/app/agents/gemini_image.py:343-358`
- 생성 semaphore/gather: `server/app/workers/detail_page_job.py:241-244`, `630-646`
- R2 업로드는 thread offload: `server/app/workers/detail_page_job.py:595`
- 단일 Uvicorn 명령: `server/Dockerfile:36`

## 권장 실험 순서

1. **분리 전:** 현재 2K와 provider 동시 호출 5개는 유지하되, 동기 이미지 변환을 스레드로 옮기고 로컬 CPU 변환 동시성은 1로 제한한다. p95 API latency, health-check 실패, CPU p95, 이벤트 루프 지연, 생성 성공률을 함께 기록한다.
2. **worker 분리:** API는 On-Demand로 유지하고 x86 Spot worker를 `1 vCPU / 4 GB`로 시작한다. provider 요청 5개를 동시에 기다리되 로컬 CPU 작업은 1개씩 처리한다. 1 vCPU에서 worker 처리시간이 실제 요구를 못 맞출 때만 `2 vCPU / 4 GB`와 비교한다.
3. **ARM 검증:** 현재 Copilot manifest가 `linux/x86_64`로 고정되어 있으므로 ARM64 이미지를 따로 빌드해 OpenCV SFace/YuNet ONNX, Pillow, psycopg, 생성/QC 경로를 smoke·부하 테스트한다. us-east-1의 `use1-az3`는 Fargate ARM64 미지원이므로 placement도 확인한다.
4. **Spot 적용:** 현행 `detail_page` 계약은 stale lease를 재큐하지 않고 `error`로 종결하며 예약 크레딧을 해제하므로, 작업 실패와 이미 발생한 provider 실비 폐기를 감수하면 x86 Spot 이전에 컷 재개 기능은 필수가 아니다. 이후 자동 재개·재큐를 추가할 때만 완료 컷 체크포인트와 호출 멱등성을 선행한다. AWS는 Spot 중단 2분 전 신호를 주지만 On-Demand로 자동 대체하지 않는다.

현재 Copilot 운용 조건까지 포함하면 `ARM API 0.5/4 On-Demand + x86 worker 1/4 Spot scale-to-zero`가 첫 후보다. ARM Spot 우회는 실제 worker 사용량이 커져 절감액이 운영 복잡도를 넘을 때만 다시 검토한다.

## Savings Plans·오토스케일링

- Compute Savings Plans는 Fargate에 적용되지만 1년 또는 3년의 시간당 사용 약정이다. 리전 이전과 worker 분리 후 **항상 켜져 있을 On-Demand API의 바닥 사용량**이 안정된 뒤 Cost Explorer 권고로 commitment를 정한다. 간헐적인 30/60/120시간 worker 추정치만 보고 선약정하지 않는다.
- ECS Service Auto Scaling은 CloudWatch CPU·메모리 또는 사용자 지표로 target tracking/step scaling을 할 수 있다. worker의 비용 목표는 queue depth/oldest-job-age 같은 사용자 지표로 0→N을 제어하는 것이지만, 여러 task가 같은 job을 잡지 않도록 원자적 claim과 lease가 먼저다.
- Compute Optimizer는 ECS Fargate 서비스의 CPU·메모리 right-sizing 근거로 쓸 수 있다. 공식 요구사항은 최근 14일 중 최소 24시간의 utilization metric이다. 일부 step/target-tracking 정책은 추천 범위를 제한하므로 정책을 붙이기 전후의 조건을 함께 기록한다.

## 공식 AWS 출처

- [AWS Price List — Amazon ECS, us-east-1, 2026-07-07 버전 JSON](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonECS/20260707160651/us-east-1/index.json)
- [AWS Fargate Pricing](https://aws.amazon.com/fargate/pricing/)
- [AWS Fargate Spot price JSON](https://dftu77xade0tc.cloudfront.net/fargate-spot-prices.json)
- [AWS Copilot Worker Service manifest — ARM 컨테이너의 Spot 제한](https://aws.github.io/copilot-cli/docs/manifest/worker-service/)
- [Amazon VPC pricing — Public IPv4 address](https://aws.amazon.com/vpc/pricing/)
- [Amazon ECS Fargate capacity providers — Spot interruption behavior](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/fargate-capacity-providers.html)
- [Amazon ECS ARM64 workloads — platform and Availability Zone caveat](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/ecs-arm64.html)
- [Specify ARM64 in an ECS task definition](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/ecs-arm-specifying.html)
- [Fargate task CPU and memory combinations](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-cpu-memory-error.html)
- [Savings Plans — Fargate eligibility and commitment](https://docs.aws.amazon.com/savingsplans/latest/userguide/what-is-savings-plans.html)
- [Amazon ECS Service Auto Scaling](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/service-auto-scaling.html)
- [Compute Optimizer requirements for ECS services on Fargate](https://docs.aws.amazon.com/compute-optimizer/latest/ug/requirements.html#requirements-ecs)

## 가격 갱신 주의

배포 결정을 내리는 날에는 위 Price List와 Spot JSON을 다시 조회해야 한다. 특히 Spot은 고정 약정 단가가 아니며, Savings Plans 할인도 계정의 약정 기간·시간당 commitment·결제 옵션에 따라 달라져 이 표에 섞지 않았다. Fargate Savings Plans는 On-Demand 사용량에 적용할 수 있지만 30/60/120시간만 쓰는 scale-to-zero worker에는 먼저 On-Demand 실사용량을 측정한 뒤 commitment를 정해야 한다.
