"""Mock-only QA subjects must satisfy the real persisted recommendation contract."""
import json
from pathlib import Path
import subprocess
from urllib.parse import unquote_to_bytes

import pytest

from app.agents.detail_recommendations import build_contract, candidate_schema, public_summary, validate_persisted
from app.agents.content_roles import validate_storyboard_v4

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def drafts():
    # 서버 CI에는 프론트 패키지(vite)가 없다. 프론트 CI가 같은 파일을
    # tests/frontend/woc3-mock-details-contract.test.mjs 로 돌려 검사를 이어 간다.
    if not (ROOT / "node_modules" / "vite").exists():
        pytest.skip("frontend dependencies are not installed")
    script = """import {createServer} from 'vite';
const vite=await createServer({configFile:false,resolve:{alias:{'@':process.cwd()+'/src'}},
 optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,watch:null,ws:false},appType:'custom',logLevel:'silent'});
try {
 const {buildDraft}=await vite.ssrLoadModule('/src/mock/db.js');
 const values=['emo','show'].map(template=>{
  globalThis.location={search:'?mockTemplate='+template+'&mockDetail=full'};
  const {product,analysis,storyboard}=buildDraft();return {template,product,analysis,storyboard};
 });
 process.stdout.write(JSON.stringify(values));
} finally {await vite.close();} """
    result = subprocess.run(["node", "--input-type=module", "-e", script], text=True,
                            capture_output=True, cwd=ROOT, timeout=30)
    assert result.returncode == 0, result.stderr
    return {row["template"]: row for row in json.loads(result.stdout)}


@pytest.mark.parametrize("template", ["emo", "show"])
def test_mock_full_subjects_normalize_against_real_mock_original_bytes(drafts, template):
    row = drafts[template]
    recommendations = row["analysis"]["detailRecommendations"]
    assert len(recommendations["candidates"]) == 4
    colors = row["product"]["colors"]
    base = next(color for color in colors if color.get("isBase"))
    images = [next(image for image in base["images"] if image["slot"] == slot)
              for slot in ["Front", "Back", "Detail", "BackDetail"]]
    source_bytes = [(unquote_to_bytes(image["src"].split(",", 1)[1]), "image/svg+xml") for image in images]
    fields = candidate_schema()["items"]["properties"].keys()
    raw = [{key: candidate[key] for key in fields} for candidate in recommendations["candidates"]]
    contract = build_contract(raw, source_bytes, [image["slot"] for image in images])
    assert validate_persisted(contract) == contract
    assert public_summary(contract) == recommendations
    assert {candidate["sourceSlot"] for candidate in contract["candidates"]} == {"Front", "Detail"}
    assert sum(candidate["kind"] == "fabric" for candidate in contract["candidates"]) == 1
    validate_storyboard_v4(row["storyboard"])
    details = [block for block in row["storyboard"] if block.get("source") == "ai" and block.get("shot") == "detail"]
    assert len(details) == 4
    assert sum(block["pageSectionKind"] == "fabric" for block in details) == 1
    assert sum(block["pageSectionKind"] == "points" for block in details) == 3
