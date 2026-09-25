import hashlib
from io import BytesIO
from types import SimpleNamespace
import asyncio

from PIL import Image

from app.agents import detail_recommendations
from app.agents.gemini_image import InlineImage
from app.workers import detail_shot_runtime as runtime


def picture():
    im = Image.new('RGB', (100, 100), 'red')
    for x in range(50, 100):
        for y in range(100):
            im.putpixel((x, y), (0, 0, 255))
    out = BytesIO()
    im.save(out, format='PNG')
    return InlineImage('image/png', out.getvalue())


def test_mirrored_source_and_target_region_are_corrected_together():
    source = picture()
    original_hash = hashlib.sha256(source.data).hexdigest()
    target = {'sourceSha256':original_hash,'region':{'x':.1,'y':.2,'w':.3,'h':.4}}
    images, resolved = runtime.normalize_mirrored_sources([source],target)
    im = Image.open(BytesIO(images[0].data))
    assert im.getpixel((10,10))[:3] == (0,0,255)
    assert im.getpixel((90,10))[:3] == (255,0,0)
    assert resolved['sourceSha256'] == hashlib.sha256(images[0].data).hexdigest()
    assert abs(resolved['region']['x'] - .6) < 1e-9
    assert resolved['originalSourceSha256'] == original_hash
    assert target['region']['x'] == .1  # persisted source contract wasn't mutated


def test_selected_target_excludes_unsigned_extra_photos(monkeypatch):
    source, extra = picture(), InlineImage('image/png', b'unanalyzed-extra')
    contract = detail_recommendations.build_contract([{
        'kind':'closure','sourceIndex':0,'region':{'x':0,'y':0,'w':1,'h':.5},
        'photoUse':'standalone','standaloneValue':'construction','visibility':'clear',
        'rank':1,'label':'단추 여밈','reason':'구조 확인','informationGroup':'closure','featurePoints':[],
    }],[(source.data,source.mime)],['Front'])
    async def load(*args):
        return [source,extra], ['Front','Detail']
    monkeypatch.setattr(runtime,'_load',load)
    result, target, direction = asyncio.run(runtime.resolve_sources(
        SimpleNamespace(), 'u', {'colors':[{'id':'base','isBase':True,'images':[]}]},
        {'detailRecommendations':contract}, {'detailTargetId':contract['candidates'][0]['id'],'direction':'front'}))
    assert result == [source] and direction == 'front'
    assert target['sourceSha256'] == hashlib.sha256(source.data).hexdigest()


def test_legacy_targetless_front_never_attaches_back(monkeypatch):
    seen = []
    async def load(app,user,refs):
        seen.extend(refs)
        return [picture()],['Front']
    monkeypatch.setattr(runtime,'_load',load)
    monkeypatch.setattr(runtime.cut_generator,'detail_reference_images',
                        lambda *args,**kw: ([('Front','front'),('Back','back'),('Detail','detail')],None))
    asyncio.run(runtime.resolve_sources(SimpleNamespace(),'u',{}, {}, {'direction':'front'}))
    assert seen == [('Front','front'),('Detail','detail')]
