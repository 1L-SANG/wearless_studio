#!/usr/bin/env python3
"""Verify fresh CloudFormation rollout evidence with the existing deploy role."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import time

import yaml


STACK_QUERY = "Stacks[0].{status:StackStatus,updated:LastUpdatedTime,mode:DeploymentConfig.Mode,parameters:Parameters[?ParameterKey=='ContainerImage' || ParameterKey=='WorkloadName'].{key:ParameterKey,value:ParameterValue}}"
RESOURCE_QUERY = "StackResources[?ResourceType=='AWS::ECS::Service' || ResourceType=='AWS::ECS::TaskDefinition'].{logical:LogicalResourceId,type:ResourceType,id:PhysicalResourceId,status:ResourceStatus,updated:Timestamp}"


class NotReady(Exception):
    pass


class TemplateLoader(yaml.SafeLoader):
    pass


def intrinsic(loader, tag, node):
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)
    return {tag if tag in ("Ref", "Condition") else f"Fn::{tag}": value}


TemplateLoader.add_multi_constructor("!", intrinsic)


def require(condition, reason):
    if not condition:
        raise NotReady(reason)


def aws(region, operation, stack, query):
    # No secret values are fetched or printed. Template bodies stay in memory.
    try:
        result = subprocess.run(
            ["aws", "cloudformation", operation, "--stack-name", stack,
             *(["--template-stage", "Processed"] if operation == "get-template" else []),
             "--region", region, "--query", query, "--output", "json",
             "--cli-connect-timeout", "10", "--cli-read-timeout", "30"],
            check=True, capture_output=True, text=True, timeout=45,
            env=dict(os.environ, AWS_PAGER=""),
        )
        return json.loads(result.stdout)
    except (subprocess.SubprocessError, ValueError) as exc:
        raise NotReady(f"CloudFormation {operation} could not be verified") from exc


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    # The workflow records UTC without a suffix, matching the existing wait helper.
    return parsed.replace(tzinfo=None) if parsed.utcoffset() is None else parsed.astimezone(
        timezone.utc).replace(tzinfo=None)


def has_early_success(value):
    if isinstance(value, dict):
        return "EarlySuccessCriteria" in value or any(has_early_success(v) for v in value.values())
    return isinstance(value, list) and any(has_early_success(v) for v in value)


def snapshot(args, name):
    stack = f"{args.app}-{args.env}-{name}"
    summary = aws(args.region, "describe-stacks", stack, STACK_QUERY)
    require(summary["status"] in ("UPDATE_COMPLETE", "CREATE_COMPLETE"), f"{name}: stack is not complete")
    require(summary["mode"] in (None, "STANDARD"), f"{name}: CloudFormation does not use standard stabilization")
    resources = aws(args.region, "describe-stack-resources", stack, RESOURCE_QUERY)
    service = [r for r in resources if r["type"] == "AWS::ECS::Service"]
    definition = [r for r in resources if r["type"] == "AWS::ECS::TaskDefinition"]
    require(len(service) == len(definition) == 1, f"{name}: service/task definition is ambiguous")
    service, definition = service[0], definition[0]
    require(all(resource["status"] in ("UPDATE_COMPLETE", "CREATE_COMPLETE")
                for resource in (service, definition)), f"{name}: resources are not complete")
    body = aws(args.region, "get-template", stack, "TemplateBody")
    template = yaml.load(body, Loader=TemplateLoader) if isinstance(body, str) else body
    service_node = template["Resources"][service["logical"]]
    definition_node = template["Resources"][definition["logical"]]
    require(service_node["Type"] == "AWS::ECS::Service"
            and definition_node["Type"] == "AWS::ECS::TaskDefinition", f"{name}: unexpected resource type")
    properties = service_node["Properties"]
    require(properties["TaskDefinition"] == {"Ref": definition["logical"]},
            f"{name}: service does not reference the current task definition")
    require(not has_early_success(service_node)
            and properties.get("DeploymentController", {}).get("Type", "ECS") == "ECS"
            and properties.get("DeploymentConfiguration", {}).get("Strategy", "ROLLING") == "ROLLING",
            f"{name}: rollout does not use full ECS rolling stabilization")
    require(aws(args.region, "describe-stacks", stack, STACK_QUERY) == summary,
            f"{name}: stack changed during verification")
    containers = definition_node["Properties"]["ContainerDefinitions"]
    require(isinstance(containers, list) and containers, f"{name}: containers missing")
    return summary, service, definition, containers


def workload_matches(args, name, summary, service, definition, containers, ready=None):
    arn = service["id"].split(":", 5)
    require(len(arn) == 6 and arn[2] == "ecs" and arn[3] == args.region, f"{name}: invalid service ARN")
    require(definition["id"].startswith(f"arn:aws:ecs:{args.region}:{arn[4]}:task-definition/"),
            f"{name}: invalid task definition ARN")
    parameters = {p["key"]: p["value"] for p in summary["parameters"]}

    def resolve(value):
        return parameters[value["Ref"]] if isinstance(value, dict) and set(value) == {"Ref"} else value

    main = [c for c in containers if resolve(c["Name"]) == name]
    image = f"{arn[4]}.dkr.ecr.{args.region}.amazonaws.com/{args.app}/{name}:{args.sha}"
    if len(main) != 1 or resolve(main[0]["Image"]) != image:
        return False
    values = [e["Value"] for e in main[0].get("Environment", []) if e["Name"] == "OUTFIT_PROPS_READY"]
    return ready is None or values == [ready]


def capture(args):
    definitions = {}
    noop_allowed = {}
    drain = 0
    for name in ("api", "detail-worker"):
        summary, service, definition, containers = snapshot(args, name)
        definitions[name] = definition["id"]
        noop_allowed[name] = workload_matches(args, name, summary, service, definition, containers,
                                               "off" if name == "api" else None)
        timeouts = [container.get("StopTimeout", 30) for container in containers]
        require(all(type(value) is int and 0 < value <= 120 for value in timeouts),
                f"{name}: old container stop timeouts cannot be bounded")
        # Dependencies can stop containers sequentially. Cover the entire old chain.
        drain = max(drain, sum(timeouts))
    Path(args.state).write_text(json.dumps({"app": args.app, "env": args.env, "sha": args.sha,
                                          "definitions": definitions, "noop_allowed": noop_allowed,
                                          "drain_seconds": drain + 30}))
    print(f"Captured old task definitions; post-stabilization drain bound is {drain + 30}s")


def verify(args, state):
    definitions = {}
    for name in ("api", "detail-worker"):
        summary, service, definition, containers = snapshot(args, name)
        started = args.api_started if name == "api" else args.detail_started
        previous = state["off_api_definition"] if name == "api" and args.api_ready == "on" else state["definitions"][name]
        # Retrying an already deployed off phase may legitimately produce no update.
        # The capture must already prove this exact SHA and the off API gate.
        noop = (definition["id"] == previous and state["noop_allowed"][name]
                and (name != "api" or args.api_ready == "off"))
        require(summary["status"] == "UPDATE_COMPLETE", f"{name}: stack is not complete")
        if not noop:
            require(timestamp(summary["updated"]) > timestamp(started),
                    f"{name}: stack completion predates this deployment")
            require(definition["id"] != previous, f"{name}: old task definition remains")
        for resource in (service, definition):
            require(resource["status"] in ("UPDATE_COMPLETE", "CREATE_COMPLETE")
                    and (noop or timestamp(resource["updated"]) > timestamp(started)),
                    f"{name}: resource completion predates this deployment")
        require(workload_matches(args, name, summary, service, definition, containers,
                                 args.api_ready if name == "api" else None),
                f"{name}: image SHA or API readiness flag differs from this phase")
        definitions[name] = {"id": definition["id"], "summary": summary, "service": service,
                             "definition": definition, "containers": containers}
    return definitions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("capture", "verify"))
    for name in ("app", "env", "sha", "state"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--api-ready", choices=("off", "on"))
    parser.add_argument("--api-started")
    parser.add_argument("--detail-started")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--max-polls", type=int, default=60)
    parser.add_argument("--poll-seconds", type=float, default=10)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.sha) or args.max_polls < 1 or args.poll_seconds < 0:
        parser.error("a full Git SHA and valid polling limits are required")
    if args.phase == "verify" and not all((args.api_ready, args.api_started, args.detail_started)):
        parser.error("verification requires both deployment start times and api-ready")
    for attempt in range(1, args.max_polls + 1):
        try:
            if args.phase == "capture":
                capture(args)
                return 0
            state = json.loads(Path(args.state).read_text())
            require(all(state[key] == getattr(args, key) for key in ("app", "env", "sha")), "capture belongs to another rollout")
            definitions = verify(args, state)
            if args.api_ready == "off":
                print(f"Fresh CF stabilization confirmed; waiting {state['drain_seconds']}s for old container shutdown", flush=True)
                time.sleep(state["drain_seconds"])
                require(verify(args, state) == definitions, "task definitions changed during the drain")
                state["off_api_definition"] = definitions["api"]["id"]
                Path(args.state).write_text(json.dumps(state))
            print(f"Verified fresh CF rollout for api and detail-worker on {args.sha}; OUTFIT_PROPS_READY={args.api_ready}")
            return 0
        except (NotReady, KeyError, IndexError, TypeError, ValueError, OSError, yaml.YAMLError) as exc:
            # Do not print parser errors that may include complete template/env lines.
            reason = str(exc) if isinstance(exc, NotReady) else type(exc).__name__
            print(f"Rollout not verified ({attempt}/{args.max_polls}): {reason}", flush=True)
            if attempt < args.max_polls:
                time.sleep(args.poll_seconds)
    print("::error::Rollout verification failed; do not enable outfit props")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
