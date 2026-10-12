import math

import pytest

from app import client_events as events
from test_client_events import ingestion, sample  # noqa: F401


def diagnostics():
    return {
        'step': 'generating', 'message_code': 'screen_load_failed',
        'browser': {'family': 'Mobile Safari', 'major': '18', 'user_agent': 'private full UA'},
        'os': {'family': 'iOS', 'major': '18', 'unknown': 'private'},
        'viewport': {'width': 390, 'height': 844, 'unknown': 'private'},
        'network': {'effective_type': '4g', 'rtt_ms': 25.5, 'downlink_mbps': 12.5,
                    'save_data': False, 'online': True, 'ip': 'private'},
        'page_age_ms': 1000, 'visibility': 'visible',
        'tech_message': 'TypeError: request failed', 'stack': 'at https://app.example/app.js:10:20',
    }


def test_accepts_context_and_persists_only_allowed_fields(ingestion):
    client, store, _ = ingestion
    raw = sample(kind='error_boundary', **diagnostics())
    response = client.post('/v1/client-events', json=raw)
    assert response.status_code == 202
    clean = store[raw['event_id']]
    assert clean['step'] == 'generating' and clean['message_code'] == 'screen_load_failed'
    assert clean['browser'] == {'family': 'Mobile Safari', 'major': '18'}
    assert clean['os'] == {'family': 'iOS', 'major': '18'}
    assert clean['viewport'] == {'width': 390, 'height': 844}
    assert clean['network'] == {'effective_type': '4g', 'rtt_ms': 25.5, 'downlink_mbps': 12.5,
                                'save_data': False, 'online': True}
    assert clean['page_age_ms'] == 1000 and clean['visibility'] == 'visible'
    assert clean['tech_message'] == 'TypeError: request failed'
    assert clean['stack'] == 'at https://app.example/app.js:10:20'


@pytest.mark.parametrize('step', ['input', 'storyboard', 'mannequin', 'generating', 'editor', 'library', 'other'])
def test_accepts_each_step(step):
    assert events.sanitize_event(sample(step=step))['step'] == step


def test_invalid_labels_and_unknown_objects_are_dropped():
    clean = events.sanitize_event(sample(step=[], message_code='x' * 33,
        browser={'family': 'a' * 33, 'major': 'a' * 9, 'private': 'ignored'},
        os={'family': 'email@example.com', 'major': 'url?private'},
        viewport=[], network={'effective_type': ['bad'], 'save_data': 1, 'online': 'true'},
        page_age_ms=True, visibility='private', tech_message={}, stack=[]))
    assert clean['step'] == 'other' and clean['message_code'] == 'other'
    assert clean['browser'] == {} and clean['os'] == {}
    assert 'viewport' not in clean and 'page_age_ms' not in clean and 'visibility' not in clean
    assert clean['network'] == {'effective_type': 'unknown'}
    assert 'tech_message' not in clean and 'stack' not in clean


@pytest.mark.parametrize('value', [-1, 16385, True, 1.5, '390', None])
def test_viewport_out_of_bounds_is_dropped(value):
    clean = events.sanitize_event(sample(viewport={'width': value, 'height': value}))
    assert clean['viewport'] == {}


@pytest.mark.parametrize('value', [0, 16384])
def test_viewport_bounds_are_inclusive(value):
    assert events.sanitize_event(sample(viewport={'width': value, 'height': value}))['viewport'] == {
        'width': value, 'height': value}


@pytest.mark.parametrize('name,maximum', [('rtt_ms', 60000), ('downlink_mbps', 10000)])
@pytest.mark.parametrize('invalid', [-1, True, '12', math.nan, math.inf, -math.inf, 10**1000])
def test_invalid_network_numbers_are_dropped(name, maximum, invalid):
    clean = events.sanitize_event(sample(network={name: invalid}))
    assert name not in clean['network']


@pytest.mark.parametrize('name,maximum', [('rtt_ms', 60000), ('downlink_mbps', 10000)])
def test_network_number_bounds(name, maximum):
    for value in (0, 0.5, maximum):
        assert events.sanitize_event(sample(network={name: value}))['network'][name] == value
    assert name not in events.sanitize_event(sample(network={name: maximum + 1}))['network']


@pytest.mark.parametrize('effective_type', ['slow-2g', '2g', '3g', '4g', 'unknown'])
def test_network_effective_types(effective_type):
    assert events.sanitize_event(sample(network={'effective_type': effective_type}))['network']['effective_type'] == effective_type


@pytest.mark.parametrize('value', [-1, 604800001, True, 1.5, '1000'])
def test_page_age_out_of_bounds_is_dropped(value):
    assert 'page_age_ms' not in events.sanitize_event(sample(page_age_ms=value))


@pytest.mark.parametrize('value', [0, 604800000])
def test_page_age_bounds(value):
    assert events.sanitize_event(sample(page_age_ms=value))['page_age_ms'] == value


@pytest.mark.parametrize('kind', ['ui_error', 'error_boundary', 'runtime_error', 'unhandled_rejection'])
def test_technical_text_is_resanitized_and_bounded(kind):
    text = ('TypeError https://user:password@app.example/app.js?token=private#fragment '
            '/v1/projects?secret=private#fragment user@example.com 한글@예시.한국 1234567890 '
            + 'x' * 2500)
    clean = events.sanitize_event(sample(kind=kind, tech_message=text, stack=text))
    for name, maximum in (('tech_message', 500), ('stack', 2000)):
        assert len(clean[name]) <= maximum
        assert 'TypeError' in clean[name] and 'https://app.example/app.js' in clean[name]
        for forbidden in ('password', 'token', 'private', '#fragment', 'user@example.com', '한글@예시.한국', '1234567890'):
            assert forbidden not in clean[name]


@pytest.mark.parametrize('kind', ['api_error', 'network_error', 'toast_error', 'save_error',
                                  'upload_error', 'generation_error', 'analysis_error'])
def test_stack_is_dropped_for_other_kinds(kind):
    clean = events.sanitize_event(sample(kind=kind, blocking=True, stack='private stack', tech_message='TypeError'))
    assert 'stack' not in clean and clean['tech_message'] == 'TypeError'


def test_sanitizing_precedes_truncation_and_preserves_firefox_frames():
    text = 'x' * 495 + ' user@example.com 1234567890123'
    clean = events.sanitize_event(sample(kind='runtime_error', tech_message=text,
        stack='load@https://app.example/app.js?token=private#fragment\n\x00surrogate\ud800'))
    assert 'user' not in clean['tech_message']
    assert 'load@https://app.example/app.js' in clean['stack']
    assert 'private' not in clean['stack'] and '\x00' not in clean['stack'] and '\ud800' not in clean['stack']


def test_diagnostic_context_does_not_change_signature_or_slack_payload():
    from app.workers.client_alerts import slack_payload

    raw = sample(kind='error_boundary', **diagnostics())
    clean = events.sanitize_event(raw)
    assert events.event_signature(clean) == events.event_signature(events.sanitize_event(sample(
        kind=raw['kind'], route=raw['route'])))
    payload = slack_payload('화면 오류 알림', [{'signature': 'sig', 'count': 1, 'users': 1, 'cases': [clean]}])
    for forbidden in ('TypeError', 'Mobile Safari', 'screen_load_failed', 'app.example', 'generating'):
        assert forbidden not in payload['text']


def test_maximum_body_size_plain_text_does_not_block_the_api():
    import time

    started = time.perf_counter()
    clean = events.sanitize_event(sample(tech_message='x' * 30000))
    elapsed = time.perf_counter() - started
    assert clean['tech_message'] == 'x' * 500
    assert elapsed < 1, f'bounded technical text took {elapsed:.2f}s'
