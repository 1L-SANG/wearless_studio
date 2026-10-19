"""Run the real deployment blocks without contacting AWS or generating images."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
SHA = "1234567890abcdef1234567890abcdef12345678"


# These doubles replace only the external commands. Manifest rendering, shell
# short-circuiting, and rollout verification all execute their production code.
FAKE_COMMAND = r'''
import json
import os
from pathlib import Path
import re
import sys

root = Path.cwd()
scenario = os.environ["DEPLOY_TEST_SCENARIO"]
sha = os.environ["GITHUB_SHA"]
old_sha = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
args = sys.argv[1:]
tool = Path(sys.argv[0]).name
log_path = root / "events.jsonl"

def log(**event):
    with log_path.open("a") as stream:
        stream.write(json.dumps(event) + "\n")

def arg(name):
    return args[args.index(name) + 1]

def ready():
    text = (root / "copilot/api/manifest.yml").read_text()
    match = re.search(r'^  OUTFIT_PROPS_READY: ["\']?(\w+)', text, re.M)
    return match.group(1) if match else None

if tool == "python3":
    if args and args[0].endswith("verify-copilot-rollout.py"):
        args += ["--max-polls", "1", "--poll-seconds", "0"]
        import runpy
        import types
        sys.argv = [args[0], *args[1:]]
        if "--api-started" in args:
            (root / "verification-starts.json").write_text(json.dumps({
                "api": arg("--api-started"), "detail-worker": arg("--detail-started")}))
        module = runpy.run_path(args[0], run_name="rollout_verifier")
        module["main"].__globals__["time"] = types.SimpleNamespace(sleep=lambda seconds: log(tool="sleep", seconds=seconds))
        sys.exit(module["main"]())
    os.execv(sys.executable, [sys.executable, *args])

if tool == "uv":
    assert args[:4] == ["run", "--no-project", "--with", "PyYAML==6.0.3"], args
    index = args.index("python3")
    os.execv(str(root / "bin/python3"), [str(root / "bin/python3"), *args[index + 1:]])

if tool == "copilot":
    service = arg("--name")
    log(tool=tool, service=service, ready=ready(), sha=arg("--tag"), args=args)
    if scenario == "first_api_deploy_fails" and service == "api" and ready() == "off":
        sys.exit(9)
    sys.exit(0)

if tool == "wait":
    log(tool=tool, stack=args[0], ready=ready())
    if scenario == "detail_wait_fails" and args[0].endswith("detail-worker"):
        sys.exit(9)
    if scenario == "first_api_wait_fails" and args[0].endswith("-api") and ready() == "off":
        sys.exit(9)
    sys.exit(0)

assert tool == "aws", tool
assert arg("--region") == "us-east-1", args
assert "--query" in args and arg("--output") == "json", args
namespace, operation = args[:2]
log(tool=tool, namespace=namespace, operation=operation)
if scenario == "aws_fails":
    sys.exit(9)
assert namespace == "cloudformation", "The existing deploy role does not permit ECS reads"

service = "detail-worker" if any("detail-worker" in value for value in args) else "api"
events = [json.loads(line) for line in log_path.read_text().splitlines()]
deploys = [event for event in events if event["tool"] == "copilot" and event["service"] == service]
version = len(deploys) + 1
deployed = bool(deploys)
rerun = scenario in ("rerun_off_same_sha", "rerun_wrong_sha_noop", "rerun_gate_on_noop")
if rerun:
    version = max(1, len(deploys)) if service == "api" else 1
cluster = "wearless-use1-Cluster-test"
service_arn = f"arn:aws:ecs:us-east-1:439328746001:service/{cluster}/{service}"
definition = f"arn:aws:ecs:us-east-1:439328746001:task-definition/{service}:{version}"
old_definition = f"arn:aws:ecs:us-east-1:439328746001:task-definition/{service}:1"
image_sha = sha if deployed or rerun else old_sha
if scenario == "rerun_wrong_sha_noop":
    image_sha = old_sha
if deployed and ((scenario == "old_detail_image" and service == "detail-worker")
                 or (scenario == "old_api_image" and service == "api")):
    image_sha = old_sha
image = f"439328746001.dkr.ecr.us-east-1.amazonaws.com/wearless/{service}:{image_sha}"
updated = "2999-01-01T00:00:00.123Z" if deployed else "2000-01-01T00:00:00Z"
if rerun and version == 1:
    updated = "2000-01-01T00:00:00Z"
status = "UPDATE_COMPLETE"
if deployed and scenario == "cloudformation_in_progress":
    status = "UPDATE_IN_PROGRESS"
if deployed and scenario == "stack_changes_during_drain" and any(event["tool"] == "sleep" for event in events):
    status = "UPDATE_IN_PROGRESS"

if operation == "describe-stacks":
    if deployed and scenario == "equal_stack_completion":
        updated = json.loads((root / "verification-starts.json").read_text())[service] + "Z"
    result = {"status": status, "updated": "2000-01-01T00:00:00Z" if deployed and scenario == "stale_stack_completion" else updated,
              "parameters": [{"key": "ContainerImage", "value": image}, {"key": "WorkloadName", "value": service}]}
elif operation == "describe-stack-resources":
    result = [
        {"logical": "Service", "type": "AWS::ECS::Service", "id": service_arn, "status": "UPDATE_COMPLETE",
         "updated": "2000-01-01T00:00:00Z" if deployed and scenario == "stale_service_completion" else updated},
        {"logical": "TaskDefinition", "type": "AWS::ECS::TaskDefinition", "id": old_definition if scenario == "unchanged_api_definition" and service == "api" else definition,
         "status": "UPDATE_COMPLETE", "updated": "2000-01-01T00:00:00Z" if deployed and scenario == "stale_task_definition_completion" else updated},
    ]
    if scenario == "capture_resource_in_progress":
        result[0]["status"] = "UPDATE_IN_PROGRESS"
elif operation == "get-template":
    assert arg("--template-stage") == "Processed"
    gate = "on" if scenario in ("api_flag_already_on", "rerun_gate_on_noop") else ready()
    if rerun and not deploys and scenario != "rerun_gate_on_noop":
        gate = "off"
    timeout = 90 if service == "api" else 60
    if scenario == "detail_drain_longer":
        timeout = 30 if service == "api" else 120
    containers = [{"Name": {"Ref": "WorkloadName"}, "Image": {"Ref": "ContainerImage"},
                   "StopTimeout": timeout, "Environment": [{"Name": "OUTFIT_PROPS_READY", "Value": gate}] if service == "api" else []}]
    if service == "api":
        containers.append({"Name": "cloudflared", "Image": "cloudflare/cloudflared@sha256:pinned", "StopTimeout": 60,
                           "DependsOn": [{"ContainerName": "api", "Condition": "HEALTHY"}]})
    if scenario == "default_timeouts":
        for container in containers:
            container.pop("StopTimeout")
    properties = {"TaskDefinition": {"Ref": "TaskDefinition"},
                  "DeploymentConfiguration": {"MinimumHealthyPercent": 100, "MaximumPercent": 200}}
    if deployed and scenario == "early_success":
        properties["DeploymentConfiguration"]["EarlySuccessCriteria"] = "RESOURCE_UPDATE_COMPLETE"
    if deployed and scenario == "wrong_task_definition_ref":
        properties["TaskDefinition"] = {"Ref": "OtherTaskDefinition"}
    if deployed and scenario == "unsupported_controller":
        properties["DeploymentController"] = {"Type": "EXTERNAL"}
    # The real CLI returns a YAML string with CloudFormation intrinsic tags.
    import yaml
    body = yaml.safe_dump({"Resources": {
        "TaskDefinition": {"Type": "AWS::ECS::TaskDefinition", "Properties": {"ContainerDefinitions": containers}},
        "Service": {"Type": "AWS::ECS::Service", "Properties": properties},
    }})
    body = re.sub(r"(?m)^(\s*)Image:\n\1  Ref: ContainerImage$", r"\1Image: !Ref ContainerImage", body)
    body = re.sub(r"(?m)^(\s*)Name:\n\1  Ref: WorkloadName$", r"\1Name: !Ref WorkloadName", body)
    result = body
else:
    raise AssertionError(args)
if operation == "describe-stacks":
    raw = {"Stacks": [{"StackStatus": result["status"], "LastUpdatedTime": result["updated"],
                       "Parameters": [{"ParameterKey": p["key"], "ParameterValue": p["value"]} for p in result["parameters"]]}]}
    if scenario == "express_stack" or (scenario == "express_after_deploy" and deployed):
        raw["Stacks"][0]["DeploymentConfig"] = {"Mode": "EXPRESS"}
    elif scenario == "standard_stack":
        raw["Stacks"][0]["DeploymentConfig"] = {"Mode": "STANDARD"}
elif operation == "describe-stack-resources":
    raw = {"StackResources": [{"LogicalResourceId": r["logical"], "ResourceType": r["type"],
                               "PhysicalResourceId": r["id"], "ResourceStatus": r["status"], "Timestamp": r["updated"]} for r in result]}
else:
    raw = {"TemplateBody": result}
import jmespath
print(json.dumps(jmespath.search(arg("--query"), raw)))
'''


@pytest.fixture
def deployment_workspace(tmp_path):
    shutil.copytree(ROOT / "copilot/api", tmp_path / "copilot/api")
    shutil.copytree(ROOT / "scripts/ci", tmp_path / "scripts/ci")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    command = f"#!{sys.executable}\n" + FAKE_COMMAND
    for name in ("copilot", "aws", "python3", "uv"):
        path = bin_dir / name
        path.write_text(command)
        path.chmod(0o755)
    wait = tmp_path / "scripts/ci/wait-copilot-stack.sh"
    wait.write_text(command.replace('tool = Path(sys.argv[0]).name', 'tool = "wait"'))
    wait.chmod(0o755)
    return tmp_path


def run_rollout(workspace, scenario="healthy"):
    workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-server.yml").read_text())
    steps = workflow["jobs"]["deploy-api"]["steps"]
    blocks = [step["run"] for step in steps if any(
        marker in step.get("run", "") for marker in ("copilot svc deploy", "verify-copilot-rollout.py"))]
    env = dict(os.environ, PATH=f"{workspace / 'bin'}:{os.environ['PATH']}",
               GITHUB_SHA=SHA, COPILOT_APP="wearless", COPILOT_ENV="use1", COPILOT_SVC="api",
               DEPLOY_TEST_SCENARIO=scenario, RUNNER_TEMP=str(workspace))
    output = ""
    for block in blocks:
        result = subprocess.run(["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", block],
                                cwd=workspace, env=env, text=True, capture_output=True, timeout=10)
        output += result.stdout + result.stderr
        if result.returncode:
            break
    events_path = workspace / "events.jsonl"
    events = [json.loads(line) for line in events_path.read_text().splitlines()] if events_path.exists() else []
    return result.returncode, output, events


@pytest.mark.parametrize(("scenario", "drain_seconds"), [
    ("healthy", 180), ("default_timeouts", 90), ("detail_drain_longer", 150),
    ("rerun_off_same_sha", 180), ("standard_stack", 180)])
def test_enable_only_after_disabled_rollout_and_convergence(deployment_workspace, scenario, drain_seconds):
    code, output, events = run_rollout(deployment_workspace, scenario)
    assert code == 0, output
    deploys = [event for event in events if event["tool"] == "copilot"]
    assert [(event["service"], event["ready"]) for event in deploys] == [
        ("detail-worker", "on"), ("api", "off"), ("api", "on")]
    assert {event["sha"] for event in deploys} == {SHA}
    assert all("--detach" in event["args"] for event in deploys)
    off_index = events.index(deploys[1])
    on_index = events.index(deploys[2])
    assert sum(event.get("operation") == "get-template" for event in events[:events.index(deploys[0])]) == 2
    between = events[off_index + 1:on_index]
    assert between[0]["tool"] == "wait"
    sleeps = [event for event in between if event["tool"] == "sleep"]
    assert sleeps == [{"tool": "sleep", "seconds": drain_seconds}]
    sleep_index = between.index(sleeps[0])
    assert any(event.get("operation") == "get-template" for event in between[:sleep_index])
    assert any(event.get("operation") == "get-template" for event in between[sleep_index + 1:])
    assert {event["namespace"] for event in events if event["tool"] == "aws"} == {"cloudformation"}
    assert events[on_index + 1]["tool"] == "wait"


@pytest.mark.parametrize("scenario", [
    "first_api_deploy_fails", "first_api_wait_fails", "unchanged_api_definition", "old_api_image", "old_detail_image",
    "stale_stack_completion", "stale_service_completion", "stale_task_definition_completion",
    "equal_stack_completion", "rerun_wrong_sha_noop", "rerun_gate_on_noop",
    "cloudformation_in_progress", "api_flag_already_on", "early_success", "wrong_task_definition_ref",
    "unsupported_controller", "stack_changes_during_drain",
    "express_after_deploy",
])
def test_first_rollout_failure_never_enables_props(deployment_workspace, scenario):
    code, output, events = run_rollout(deployment_workspace, scenario)
    assert code != 0, output
    api_deploys = [event for event in events if event["tool"] == "copilot" and event["service"] == "api"]
    assert [event["ready"] for event in api_deploys] == ["off"]


def test_detail_wait_failure_never_deploys_api(deployment_workspace):
    code, output, events = run_rollout(deployment_workspace, "detail_wait_fails")
    assert code != 0, output
    assert [event["service"] for event in events if event["tool"] == "copilot"] == ["detail-worker"]


@pytest.mark.parametrize("scenario", ["aws_fails", "capture_resource_in_progress", "express_stack"])
def test_capture_failure_never_deploys_any_service(deployment_workspace, scenario):
    code, output, events = run_rollout(deployment_workspace, scenario)
    assert code != 0, output
    assert not any(event["tool"] == "copilot" for event in events)


@pytest.mark.parametrize("mutation", ["missing", "duplicate"])
def test_invalid_ready_manifest_stops_before_api_deploy(deployment_workspace, mutation):
    manifest = deployment_workspace / "copilot/api/manifest.yml"
    text = manifest.read_text()
    if mutation == "missing":
        text = "\n".join(line for line in text.splitlines() if "OUTFIT_PROPS_READY:" not in line) + "\n"
    else:
        text += '\n  OUTFIT_PROPS_READY: "on"\n'
    manifest.write_text(text)
    code, output, events = run_rollout(deployment_workspace)
    assert code != 0, output
    assert not any(event.get("service") == "api" for event in events)
