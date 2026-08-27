"""ECS/SNS 어댑터 — boto3 대역으로 계약만 고정한다. 실제 AWS 는 부르지 않는다."""

import asyncio
from datetime import datetime, timezone

import pytest

from app.services.sam_autoscale import EcsTarget, SamAutoscaleAdapter, ServiceState
from conftest import make_settings

CLUSTER = "arn:aws:ecs:ap-northeast-2:1:cluster/wearless-prod-Cluster-x"
SAM2 = "arn:aws:ecs:ap-northeast-2:1:service/wearless-prod-Cluster-x/wearless-prod-sam2-Service-y"
API = "arn:aws:ecs:ap-northeast-2:1:service/wearless-prod-Cluster-x/wearless-prod-api-Service-z"


def _tags(service):
    return [{"key": "copilot-application", "value": "wearless"},
            {"key": "copilot-environment", "value": "prod"},
            {"key": "copilot-service", "value": service}]


class _Ecs:
    """boto3 ecs 클라이언트 대역. get_paginator 가 없으므로 어댑터는 단일 호출 경로를 탄다."""

    def __init__(self, services=None, tasks=None):
        self.services = services if services is not None else {
            SAM2: {"serviceArn": SAM2, "desiredCount": 0, "runningCount": 0,
                   "pendingCount": 0, "tags": _tags("sam2")},
            API: {"serviceArn": API, "desiredCount": 1, "runningCount": 1,
                  "pendingCount": 0, "tags": _tags("api")},
        }
        self.tasks = tasks or []
        self.updates = []
        self.describe_batches = []

    def list_clusters(self):
        return {"clusterArns": [CLUSTER]}

    def list_services(self, cluster):
        return {"serviceArns": list(self.services)}

    def describe_services(self, cluster, services, include=()):
        self.describe_batches.append(len(services))
        return {"services": [self.services[a] for a in services if a in self.services]}

    def update_service(self, cluster, service, desiredCount):
        self.updates.append((service, desiredCount))
        self.services[service]["desiredCount"] = desiredCount
        return {"service": self.services[service]}

    def list_tasks(self, cluster, serviceName, desiredStatus="RUNNING"):
        return {"taskArns": [t["taskArn"] for t in self.tasks]}

    def describe_tasks(self, cluster, tasks):
        return {"tasks": [t for t in self.tasks if t["taskArn"] in tasks]}


class _Sns:
    def __init__(self):
        self.published = []

    def publish(self, TopicArn, Subject, Message):
        self.published.append((TopicArn, Subject, Message))
        return {"MessageId": "m"}


def _adapter(ecs=None, sns=None, **over):
    values = {"sam_autoscale": "on", "sam_alert_topic_arn": "arn:aws:sns:ap-northeast-2:1:t"}
    values.update(over)                       # 같은 키를 두 번 넘기면 TypeError — 병합한다
    s = make_settings(**values)
    return SamAutoscaleAdapter(s, ecs=ecs or _Ecs(), sns=sns or _Sns())


def test_disabled_adapter_never_builds_clients():
    s = make_settings(sam_autoscale="off")
    a = SamAutoscaleAdapter(s)
    assert a.enabled is False
    assert asyncio.run(a.discover()) is None


def test_discover_matches_all_three_copilot_tags():
    a = _adapter()
    t = asyncio.run(a.discover())
    assert t == EcsTarget(cluster_arn=CLUSTER, service_arn=SAM2)


def test_discover_refuses_ambiguity():
    ecs = _Ecs()
    ecs.services["dup"] = {"serviceArn": "dup", "desiredCount": 0, "runningCount": 0,
                           "pendingCount": 0, "tags": _tags("sam2")}
    a = _adapter(ecs=ecs)
    assert asyncio.run(a.discover()) is None


def test_discover_refuses_partial_tag_match():
    ecs = _Ecs()
    ecs.services[SAM2]["tags"] = [{"key": "copilot-service", "value": "sam2"}]   # env/app 없음
    a = _adapter(ecs=ecs)
    assert asyncio.run(a.discover()) is None


def test_discover_chunks_describe_services_by_ten():
    """DescribeServices 는 한 번에 10개까지 — 11번째 서비스의 중복 sam2 를 놓치면 안 된다."""
    ecs = _Ecs()
    for i in range(12):
        ecs.services[f"svc{i}"] = {"serviceArn": f"svc{i}", "desiredCount": 0, "runningCount": 0,
                                   "pendingCount": 0, "tags": _tags("other")}
    ecs.services["dup"] = {"serviceArn": "dup", "desiredCount": 0, "runningCount": 0,
                           "pendingCount": 0, "tags": _tags("sam2")}
    a = _adapter(ecs=ecs)
    assert asyncio.run(a.discover()) is None, "15개 중 2개 매칭 → 거부"
    assert max(ecs.describe_batches) <= 10


def test_discover_is_cached_per_process_and_forget_target_retries():
    ecs = _Ecs()
    a = _adapter(ecs=ecs)
    asyncio.run(a.discover())
    ecs.services.clear()                      # 이후 호출은 ECS 를 안 본다
    assert asyncio.run(a.discover()) == EcsTarget(cluster_arn=CLUSTER, service_arn=SAM2)
    a.forget_target()                         # ServiceNotFound 뒤 — 한 번 더 찾는다
    assert asyncio.run(a.discover()) is None


def test_describe_reports_counts_and_oldest_task_start():
    started = datetime(2026, 8, 21, 3, 46, 45, tzinfo=timezone.utc)
    later = datetime(2026, 8, 21, 4, 0, 0, tzinfo=timezone.utc)
    ecs = _Ecs(tasks=[{"taskArn": "t1", "startedAt": later},
                      {"taskArn": "t2", "startedAt": started}])
    ecs.services[SAM2].update(desiredCount=1, runningCount=1)
    a = _adapter(ecs=ecs)
    t = asyncio.run(a.discover())
    st = asyncio.run(a.describe(t))
    assert st == ServiceState(desired=1, running=1, pending=0, oldest_started_at=started)


def test_describe_without_tasks_has_no_start_time():
    a = _adapter()
    t = asyncio.run(a.discover())
    st = asyncio.run(a.describe(t))
    assert st.oldest_started_at is None


def test_set_desired_calls_update_service_once():
    ecs = _Ecs()
    a = _adapter(ecs=ecs)
    t = asyncio.run(a.discover())
    asyncio.run(a.set_desired(t, 1))
    assert ecs.updates == [(SAM2, 1)]


def test_notify_publishes_to_the_configured_topic():
    sns = _Sns()
    a = _adapter(sns=sns)
    asyncio.run(a.notify("sam2 test", "hello"))
    assert sns.published == [("arn:aws:sns:ap-northeast-2:1:t", "sam2 test", "hello")]


def test_notify_is_a_noop_without_a_topic():
    sns = _Sns()
    a = _adapter(sns=sns, sam_alert_topic_arn=None)
    asyncio.run(a.notify("x", "y"))
    assert sns.published == []


def test_aws_errors_surface_as_exceptions_for_the_caller_to_swallow():
    class _Boom(_Ecs):
        def list_clusters(self):
            raise RuntimeError("throttled")
    a = _adapter(ecs=_Boom())
    with pytest.raises(RuntimeError):
        asyncio.run(a.discover())


# ── task IP (콜드스타트 직결) ─────────────────────────────────────────────────
#
# 실측(2026-08-27 prod use1): DescribeTasks 는 태스크가 아직 PROVISIONING 인 +5초에 이미
# privateIPv4Address 를 준다. 같은 태스크가 RUNNING 이 되는 건 +112.7초, Service Connect 로
# 호출 가능해지는 건 +146.7초다 — 그래서 이 조회가 100초 이상 빠르다.

def _task(arn, ip=None, extra_attachments=()):
    details = [{"name": "networkInterfaceId", "value": "eni-1"}]
    if ip:
        details.append({"name": "privateIPv4Address", "value": ip})
    return {"taskArn": arn,
            "attachments": [*extra_attachments,
                            {"type": "ElasticNetworkInterface", "status": "ATTACHED",
                             "details": details}]}


def test_task_ips_reads_the_private_ip():
    ecs = _Ecs(tasks=[_task("t1", "10.0.0.205")])
    a = _adapter(ecs=ecs)
    target = EcsTarget(cluster_arn=CLUSTER, service_arn=SAM2)
    assert asyncio.run(a.task_ips(target)) == ["10.0.0.205"]


def test_task_ips_is_empty_when_the_service_is_at_zero():
    a = _adapter(ecs=_Ecs(tasks=[]))
    target = EcsTarget(cluster_arn=CLUSTER, service_arn=SAM2)
    assert asyncio.run(a.task_ips(target)) == []


def test_task_ips_ignores_attachments_without_an_ip():
    """Service Connect 첨부는 details 가 비어 있다 — 그걸 IP 로 착각하면 안 된다."""
    sc = {"type": "ServiceConnect", "status": "ATTACHED", "details": []}
    ecs = _Ecs(tasks=[_task("t1", "10.0.0.7", extra_attachments=(sc,)),
                      _task("t2", None)])
    a = _adapter(ecs=ecs)
    target = EcsTarget(cluster_arn=CLUSTER, service_arn=SAM2)
    assert asyncio.run(a.task_ips(target)) == ["10.0.0.7"]
