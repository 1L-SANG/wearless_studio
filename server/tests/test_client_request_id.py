import uuid

import pytest
from fastapi import Request, Response

from app.auth import require_user
from test_http_error_log import _capture_api_logs


@pytest.mark.parametrize('status', [200, 204, 401, 422, 500])
def test_request_id_on_every_response(client, status):
    @client.app.get('/_test/request-id')
    async def endpoint(number: int, request: Request):
        if status == 500:
            raise RuntimeError('boom')
        if status == 401:
            require_user(request, token=None)
        return Response(status_code=status)

    request_id = str(uuid.uuid4())
    params = {} if status == 422 else {'number': 1}
    with _capture_api_logs() as logs:
        response = client.get('/_test/request-id', params=params, headers={
            'Origin': 'http://localhost:5173', 'X-Client-Request-Id': request_id})
    assert response.status_code == status
    assert response.headers['X-Request-Id'] == request_id
    assert 'X-Request-Id' in response.headers['Access-Control-Expose-Headers']
    assert response.headers['Access-Control-Allow-Origin'] == 'http://localhost:5173'
    assert all(request_id in log.getMessage() for log in logs)
    if status == 500:
        assert response.json()['error']['code'] == 'internal_error'


@pytest.mark.parametrize('incoming', ['', 'not-a-uuid', 'a' * 1000])
def test_invalid_id_replaced(client, incoming):
    response = client.get('/healthz', headers={'X-Client-Request-Id': incoming})
    assert str(uuid.UUID(response.headers['X-Request-Id'])) == response.headers['X-Request-Id']
    assert response.headers['X-Request-Id'] != incoming


def test_preflight_allows_client_request_id(client):
    response = client.options('/v1/client-events', headers={
        'Origin': 'http://localhost:5173', 'Access-Control-Request-Method': 'POST',
        'Access-Control-Request-Headers': 'X-Client-Request-Id,Authorization,Content-Type'})
    assert response.status_code == 200
    assert 'x-client-request-id' in response.headers['Access-Control-Allow-Headers'].lower()
    uuid.UUID(response.headers['X-Request-Id'])
