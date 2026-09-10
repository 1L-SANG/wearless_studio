"""Approved length changes only its garment's authority; stale receipts cannot release."""
from dataclasses import replace
from hashlib import sha256
from copy import deepcopy
from base64 import b64decode

import pytest
from PIL import Image

from app.agents import wearshot_contract as c, wearshot_prompt as prompt, wearshot_qc as qc
from app.agents.gemini_image import InlineImage
from test_wearshot_contract_v2 import packet, png
from test_wearshot_qc_v2 import observed, mark


def directed(*, scoped='top', hidden=False):
    old = packet()
    target = replace(old.target, approved_length_key='approved-hem',
                     out_of_frame_axes=('length',) if hidden else ())
    return replace(old, target=target, references=(old.references[-1], *old.references[:-1],
        c.BoundReference('approved-hem', 'approvedLength', png('purple'), scoped)),
        directing_mode='source_locked_v1')


def length_observed(contract):
    raw = observed(contract)
    for row, garment in zip(raw['garments'], contract.garments):
        row['lengthReferenceKey'] = garment.approved_length_key or garment.mannequin_key
        if 'length' in garment.out_of_frame_axes:
            row['checks']['length'] = mark('NOT_APPLICABLE')
    return raw


def frozen_legacy_packet():
    # Original 4x6 RGB PNG bytes from the approved snapshot. Re-encoding equal
    # pixels can change their byte hashes across Pillow/zlib versions/platforms.
    refs = tuple(c.BoundReference(key, role, InlineImage('image/png', b64decode(encoded)), garment, ordinal)
                 for key, role, garment, ordinal, encoded in (
        ('t-anchor', 'approvedMannequin', 'top', None,
         'iVBORw0KGgoAAAANSUhEUgAAAAQAAAAGCAIAAABrW6giAAAAEUlEQVR4nGP8z4AATEhssjgATtUBC+MZq3UAAAAASUVORK5CYII='),
        ('t-seller', 'targetSeller', 'top', 2,
         'iVBORw0KGgoAAAANSUhEUgAAAAQAAAAGCAIAAABrW6giAAAAE0lEQVR4nGP8//8/AwwwwVlkcgDlPgMJNPmmKAAAAABJRU5ErkJggg=='),
        ('m-anchor', 'approvedMannequin', 'trousers', None,
         'iVBORw0KGgoAAAANSUhEUgAAAAQAAAAGCAIAAABrW6giAAAAE0lEQVR4nGNkYPjPAANMcBaZHABM1wELBoB5tQAAAABJRU5ErkJggg=='),
        ('m-seller', 'matchingSeller', 'trousers', 1,
         'iVBORw0KGgoAAAANSUhEUgAAAAQAAAAGCAIAAABrW6giAAAADElEQVR4nGNgoCYAAABOAAHWZzc3AAAAAElFTkSuQmCC'),
        ('face', 'modelFace', None, None,
         'iVBORw0KGgoAAAANSUhEUgAAAAQAAAAGCAIAAABrW6giAAAAEklEQVR4nGP8/58BDpgQTPI4AJqJAgoGCOJWAAAAAElFTkSuQmCC'),
        ('example', 'example', None, None,
         'iVBORw0KGgoAAAANSUhEUgAAAAQAAAAGCAIAAABrW6giAAAAE0lEQVR4nGNsaGhggAEmOItMDgB0IgGMBrf4BwAAAABJRU5ErkJggg=='),
    ))
    return c.bind_contract(
        target=c.GarmentBinding('top', 't-anchor', ('t-seller',),
            (c.EssentialDetail('hem', 'curved split hem', ('t-seller',)),)),
        matching=(c.GarmentBinding('trousers', 'm-anchor', ('m-seller',)),),
        expected_matching_ids=('trousers',), references=refs,
        example_key='example', model_face_key='face', model_body_key=None, capture_key=None,
        frame_lock=c.FrameLock('medium', 'partial', 'Knees crop, same subject scale'),
        variation_axis='pose', capture_profile='soft')


@pytest.mark.parametrize('png_encoder', ['default', 'uncompressed', 'unavailable'])
def test_absent_extensions_preserve_frozen_contract_and_prompt_bytes(monkeypatch, png_encoder):
    if png_encoder == 'uncompressed':
        save = Image.Image.save
        def uncompressed(image, *args, **kwargs):
            return save(image, *args, **(kwargs | {'compress_level': 0}))
        monkeypatch.setattr(Image.Image, 'save', uncompressed)
    elif png_encoder == 'unavailable':
        def unavailable(*args, **kwargs):
            raise AssertionError('Frozen snapshot must not depend on a PNG encoder')
        monkeypatch.setattr(Image.Image, 'save', unavailable)
    old = frozen_legacy_packet()
    assert old.fingerprint == 'c7f1732eceb7597cded4d8ae2ecd3cedd8e259eadb639fc21569ec9cd0ae7cc0'
    assert sha256(prompt.render_generation(old).prompt.encode()).hexdigest() == 'c63a90aa7adb9fc2787a5f6a9db30258e45776642cc15f8a813a489d3fd8c418'
    assert set(old.target.to_dict()) == {'garmentId', 'mannequinKey', 'sellerKeys', 'essentials', 'outOfFrameAxes'}
    assert 'directingMode' not in old.to_dict()


def test_length_owner_is_scoped_and_same_bytes_reach_generation_and_exact_base_repair():
    contract = directed()
    assert contract.target.mannequin_key == 't-anchor'
    assert contract.target.seller_keys == ('t-seller',)
    assert contract.model_face_key == 'face'
    assert contract.target.to_dict()['approvedLengthKey'] == 'approved-hem'
    assert contract.to_dict()['directingMode'] == 'source_locked_v1'
    assert contract.fingerprint != packet().fingerprint
    generation = prompt.render_generation(contract)
    repair = prompt.render_repair(contract, c.make_repair_plan(contract, png('orange'), ('garment:top:length',), ('identity',)))
    assert generation.reference_keys[0] == 'example'
    assert repair.reference_keys == ('repairBase', *generation.reference_keys)
    assert repair.images[1:] == generation.images
    for rendered in (generation, repair):
        assert 'same physical place' in rendered.prompt
        assert 'lengthReferenceKey' in rendered.prompt or 'approvedLengthKey' in rendered.prompt
        assert 'width/ease' in rendered.prompt
        assert 'primary canvas' in rendered.prompt


@pytest.mark.parametrize('fault', ['missing', 'wrong_scope', 'wrong_role', 'unbound', 'ordinal'])
def test_invalid_length_reference_binding_holds(fault):
    with pytest.raises(ValueError):
        contract = directed(scoped='trousers' if fault == 'wrong_scope' else 'top')
        if fault == 'missing': replace(contract, references=contract.references[:-1])
        if fault == 'wrong_role': replace(contract, references=(*contract.references[:-1], replace(contract.references[-1], role='approvedMannequin')))
        if fault == 'unbound': replace(contract, target=replace(contract.target, approved_length_key=None))
        if fault == 'ordinal': replace(contract.references[-1], evidence_ordinal=1)


@pytest.mark.parametrize('fault', ['not_first', 'capture', 'unknown'])
def test_source_lock_requires_original_first_and_rejects_capture(fault):
    with pytest.raises(ValueError):
        contract = directed()
        if fault == 'not_first': replace(contract, references=tuple(reversed(contract.references)))
        if fault == 'capture': replace(contract, capture_key='capture', references=(*contract.references, c.BoundReference('capture', 'capture', png())))
        if fault == 'unknown': replace(contract, directing_mode='other')


def test_qc_requires_length_owner_and_revalidates_release_without_reusing_old_verdict():
    contract, candidate = directed(), png('green')
    schema = qc.review_schema(contract)['properties']['garments']['items']
    assert 'lengthReferenceKey' in schema['required']
    assert 'lengthReferenceKey' not in qc.review_schema(packet())['properties']['garments']['items']['required']
    raw = length_observed(contract)
    result = qc.validate(raw, contract, candidate)
    assert qc.release_allowed(result, contract, candidate)
    assert not qc.release_allowed(qc.validate(observed(packet()), packet(), candidate), contract, candidate)
    for key in ('t-anchor', 'm-anchor', None):
        wrong = deepcopy(raw)
        if key is None: wrong['garments'][0].pop('lengthReferenceKey')
        else: wrong['garments'][0]['lengthReferenceKey'] = key
        assert not qc.validate(wrong, contract, candidate)['valid']
    result['observations']['garments'][0]['lengthReferenceKey'] = 't-anchor'
    assert not qc.release_allowed(result, contract, candidate)


def test_hidden_length_remains_na_and_different_place_fails_despite_variation():
    contract, candidate = directed(hidden=True), png()
    raw = length_observed(contract)
    assert qc.release_allowed(qc.validate(raw, contract, candidate), contract, candidate)
    raw['garments'][0]['checks']['length'] = mark('PASS')
    assert not qc.validate(raw, contract, candidate)['valid']
    raw = length_observed(contract)
    raw['globalChecks']['background'] = mark('FAIL')
    raw['variation']['backgroundChanged'] = True
    assert not qc.release_allowed(qc.validate(raw, contract, candidate), contract, candidate)
