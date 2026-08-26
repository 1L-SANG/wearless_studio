"""sam2 온디맨드 기동/종료 — `want` 판정(순수)과 ECS/SNS 어댑터.

boto3 는 이 파일에만 산다. `SAM_AUTOSCALE=off` 면 클라이언트를 만들지도 않는다.
정본: docs/superpowers/specs/2026-08-21-sam2-on-demand-scaling-design.md
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

log = logging.getLogger("wearless.sam_autoscale")

#: 수요를 만드는 잡. 셋 중 하나라도 pending/running 이면 켜져 있어야 한다.
SAM_KINDS = ("sam_preprocess", "matching_cutout", "editor_garment_mask")

#: 세 태그 모두 정확히 일치해야 한다. 실측(2026-08-21): Copilot 이 ECS 서비스에 이 태그를 단다.
#: 서비스명에는 랜덤 접미사가 붙어(…-6uWul9L25eM7) 박아 두면 스택 재생성 시 조용히 깨진다.
#: service 만 바꿔 sam2·opendid 등 다른 scale-to-zero 서비스에 재사용한다(어댑터 인스턴스별).
def _required_tags(service: str) -> dict:
    return {"copilot-application": "wearless", "copilot-environment": "prod",
            "copilot-service": service}


_REQUIRED_TAGS = _required_tags("sam2")  # 하위호환(직접 참조하는 외부 코드 대비)


# ── want 판정 (순수) ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DemandSnapshot:
    active_sam_jobs: int
    last_sam_finished_at: datetime | None
    last_upload_at: datetime | None


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def want_running(snap: DemandSnapshot, *, idle_minutes: int,
                 now: datetime | None = None) -> bool:
    """세 조건 중 하나라도 참이면 1대. 전부 거짓일 때만 0대.

    조건 2(마지막 SAM 잡 종료)는 "어제 컷을 오늘 열어 색감 조정" 경로를 잡는다 — 그 잡은
    1초 만에 unavailable 로 끝나 60초 뒤엔 active 로 안 보인다. 실패한 잡도 "방금 SAM 이
    필요했다"는 증거다.
    """
    if snap.active_sam_jobs > 0:
        return True
    now = now or datetime.now(timezone.utc)
    horizon = now - timedelta(minutes=int(idle_minutes))
    for ts in (_utc(snap.last_sam_finished_at), _utc(snap.last_upload_at)):
        if ts is not None and ts > horizon:
            return True
    return False


# ── ECS/SNS 어댑터 ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EcsTarget:
    cluster_arn: str
    service_arn: str


@dataclass(frozen=True)
class ServiceState:
    desired: int
    running: int
    pending: int
    oldest_started_at: datetime | None


class SamAutoscaleAdapter:
    """ECS/SNS 호출을 한 곳에. off 면 클라이언트를 만들지 않는다.

    boto3 는 동기다 — 모든 호출을 `asyncio.to_thread` 로 격리한다(`app/r2.py` 관례).
    예외는 삼키지 않고 올린다: 훅·reconciler 가 각자 맥락에 맞게 삼킨다.
    """

    def __init__(self, settings, *, service="sam2", enabled_attr="sam_autoscale",
                 topic_attr="sam_alert_topic_arn", ecs=None, sns=None):
        self._settings = settings
        self._service = service
        self._required_tags = _required_tags(service)
        self._topic_attr = topic_attr
        self.enabled = getattr(settings, enabled_attr, "off") == "on"
        self._ecs = ecs
        self._sns = sns
        self._target: EcsTarget | None = None
        if self.enabled and (ecs is None or sns is None):
            import boto3
            from botocore.config import Config
            # reconciler 가 느려도 잡 처리에 영향이 없도록 짧게. 재시도는 boto 기본 대신 2회.
            cfg = Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 2})
            region = "ap-northeast-2"
            self._ecs = ecs or boto3.client("ecs", region_name=region, config=cfg)
            self._sns = sns or boto3.client("sns", region_name=region, config=cfg)

    # ── 탐색 ──
    async def discover(self) -> EcsTarget | None:
        """태그로 sam2 서비스를 찾는다. 0개·2개 이상이면 None — 임의 선택하지 않는다.

        클러스터부터 나열하는 이유: Copilot 이 태스크에 주는 COPILOT_* 환경변수에 클러스터 ARN 이
        **없다**(실측 2026-08-21: SERVICE_NAME·APPLICATION_NAME·ENVIRONMENT_NAME·
        SERVICE_DISCOVERY_ENDPOINT·LB_DNS 뿐). 그래서 ListClusters 권한이 필요하다.
        결과는 프로세스 수명 동안 캐시한다.
        """
        if not self.enabled:
            return None
        if self._target is not None:
            return self._target
        self._target = await asyncio.to_thread(self._discover_sync)
        return self._target

    def forget_target(self) -> None:
        """ServiceNotFound 를 받은 호출자가 부른다 — 다음 discover 가 한 번 더 찾는다(스택 재생성)."""
        self._target = None

    def _paged(self, op: str, key: str, **kw) -> list:
        """boto3 paginator 가 있으면 쓰고(실 클라이언트), 없으면(테스트 대역) 단일 호출."""
        client = self._ecs
        if hasattr(client, "get_paginator"):
            out = []
            for page in client.get_paginator(op).paginate(**kw):
                out.extend(page.get(key, []))
            return out
        return list(getattr(client, op)(**kw).get(key, []))

    def _discover_sync(self) -> EcsTarget | None:
        matches: list[EcsTarget] = []
        for cluster in self._paged("list_clusters", "clusterArns"):
            arns = self._paged("list_services", "serviceArns", cluster=cluster)
            # DescribeServices 는 한 번에 10개까지 — 11번째 서비스의 중복을 놓치지 않게 쪼갠다.
            for i in range(0, len(arns), 10):
                desc = self._ecs.describe_services(
                    cluster=cluster, services=arns[i:i + 10], include=["TAGS"])
                for svc in desc.get("services", []):
                    tags = {t.get("key"): t.get("value") for t in (svc.get("tags") or [])}
                    if all(tags.get(k) == v for k, v in self._required_tags.items()):
                        matches.append(EcsTarget(cluster_arn=cluster,
                                                 service_arn=svc["serviceArn"]))
        if len(matches) != 1:
            log.warning("%s service discovery matched %d services — autoscale disabled",
                        self._service, len(matches))
            return None
        return matches[0]

    # ── 상태·조작 ──
    async def describe(self, target: EcsTarget) -> ServiceState:
        return await asyncio.to_thread(self._describe_sync, target)

    def _describe_sync(self, target: EcsTarget) -> ServiceState:
        svc = self._ecs.describe_services(
            cluster=target.cluster_arn, services=[target.service_arn])["services"][0]
        oldest = None
        arns = self._ecs.list_tasks(cluster=target.cluster_arn, serviceName=target.service_arn,
                                    desiredStatus="RUNNING").get("taskArns", [])
        if arns:
            tasks = self._ecs.describe_tasks(cluster=target.cluster_arn, tasks=arns).get("tasks", [])
            starts = [t.get("startedAt") for t in tasks if t.get("startedAt")]
            oldest = min(starts) if starts else None
        return ServiceState(desired=int(svc.get("desiredCount") or 0),
                            running=int(svc.get("runningCount") or 0),
                            pending=int(svc.get("pendingCount") or 0),
                            oldest_started_at=oldest)

    async def set_desired(self, target: EcsTarget, count: int) -> None:
        """UpdateService 는 같은 값을 다시 넣어도 no-op 이다(태스크 재시작 없음)."""
        await asyncio.to_thread(
            self._ecs.update_service, cluster=target.cluster_arn,
            service=target.service_arn, desiredCount=int(count))

    async def notify(self, subject: str, body: str) -> None:
        topic = getattr(self._settings, self._topic_attr, None)
        if not self.enabled or not topic or self._sns is None:
            return
        await asyncio.to_thread(self._sns.publish, TopicArn=topic,
                                Subject=subject[:100], Message=body)
