"""Execute provisioning against fake local CLI boundaries, without network/DB."""

import json
import os
from pathlib import Path
import subprocess
import sys


def test_provisioning_creates_separate_immutable_schema_without_mutating_v1(tmp_path):
    root = Path(__file__).resolve().parents[2]
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    for command in ("docker", "curl"):
        script = fakebin / command
        script.write_text(f"#!{sys.executable}\n" + '''
import json, os, pathlib, sys
state = pathlib.Path(os.environ['CONTRACT_TEST_STATE'])
if pathlib.Path(sys.argv[0]).name == 'docker':
    query = sys.stdin.read()
    if 'list_vc_plan' in query:
        print(1 if (state / 'issue-profiles').exists() else 0)
    else:
        resource = 'namespaces' if 'namespace' in query else 'vc-schemas' if 'vc_schema' in query else 'issue-profiles'
        print(1 if (state / resource).exists() else '')
else:
    args = sys.argv[1:]
    body = json.loads(args[args.index('-d') + 1])
    url = next(arg for arg in args if arg.startswith('http'))
    resource = url.rsplit('/', 1)[-1]
    (state / resource).write_text(json.dumps(body))
''')
        script.chmod(0o700)
    env = {key: value for key, value in os.environ.items() if not key.startswith("FL_")}
    env.update(PATH=f"{fakebin}:{env['PATH']}", PG_USER="test", CONTRACT_TEST_STATE=str(tmp_path))
    subprocess.run(["bash", str(root / "scripts/issuer-provision-facelicense.sh")],
                   env=env, check=True, capture_output=True, text=True)
    namespace = json.loads((tmp_path / "namespaces").read_text())
    schema = json.loads((tmp_path / "vc-schemas").read_text())
    profile = json.loads((tmp_path / "issue-profiles").read_text())
    assert namespace["namespace"]["id"] == "kr.wearless.facelicense.v2"
    assert {item["id"] for item in namespace["items"]} == {
        "model_did", "license_id", "issued_at", "face_image_digest",
        "agreement_version", "consent_doc_version",
    }
    assert all(item["required"] for item in namespace["items"])
    assert schema["vcSchemaId"] == "facelicense-v2"
    assert profile["vcPlanId"] == "vcplanface0000000002"
