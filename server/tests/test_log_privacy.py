"""Payment credentials must be absent from rendered server logs.

Use the real app/Uvicorn formatters: flattening LogRecord.args breaks
Uvicorn access logs, and sanitizing only getMessage() misses extras/traces.
"""

import asyncio
import io
import logging

import httpx
import pytest
from fastapi.testclient import TestClient
from uvicorn.logging import AccessFormatter, DefaultFormatter

from app import main, toss_billing
from conftest import make_settings


WEBHOOK_SECRET = "synthetic-webhook-secret"
BILLING_KEY = "synthetic-billing-key"
WEBHOOK_PATH = f"/v1/webhooks/toss/{WEBHOOK_SECRET}"
BILLING_PATH = f"/v1/billing/{BILLING_KEY}"


@pytest.fixture()
def app_logs(monkeypatch):
    app = main.create_app(make_settings(
        subscription_billing_enabled=True,
        toss_webhook_path_secret=WEBHOOK_SECRET,
        toss_billing_kek="synthetic-kek",
    ))
    stream = io.StringIO()
    handler = logging.getLogger().handlers[0]
    monkeypatch.setattr(handler, "stream", stream)
    return TestClient(app), stream


@pytest.mark.parametrize("method, path, status", [
    ("GET", WEBHOOK_PATH, 405),
    ("POST", WEBHOOK_PATH + "/missing", 404),
    ("POST", WEBHOOK_PATH, 500),
])
def test_app_error_output_hides_webhook_path_secret(app_logs, method, path, status):
    client, stream = app_logs
    # A JSON list reaches the real webhook handler and raises before any DB work.
    response = client.request(method, path, json=[])

    assert response.status_code == status
    output = stream.getvalue()
    assert WEBHOOK_SECRET not in output
    assert "/v1/webhooks/toss/" in output
    assert f"status={status}" in output
    assert response.headers["x-request-id"] in output
    if status == 500:
        assert "Traceback" in output
        assert "AttributeError" in output
        assert "code=internal_error" in output


def test_success_request_log_keeps_status_and_request_id(app_logs):
    client, stream = app_logs
    response = client.post(WEBHOOK_PATH, json={"eventType": "IGNORED"})

    assert response.status_code == 200
    output = stream.getvalue()
    assert WEBHOOK_SECRET not in output
    assert "status=200 method=POST" in output
    assert response.headers["x-request-id"] in output


def test_worker_exception_and_extra_urls_are_redacted_in_output(app_logs):
    _, stream = app_logs
    logger = logging.getLogger("wearless.subscription_biller")
    try:
        raise RuntimeError(f"provider failed at https://toss.test{BILLING_PATH}")
    except RuntimeError:
        logger.exception("billing failed", extra={
            "callback": f"https://api.test{WEBHOOK_PATH}",
            "request_id": "request-safe-42", "code": "provider_unavailable",
        })

    output = stream.getvalue()
    assert BILLING_KEY not in output
    assert WEBHOOK_SECRET not in output
    assert "Traceback" in output and "RuntimeError" in output
    assert "request_id='request-safe-42'" in output
    assert "code='provider_unavailable'" in output


def test_uvicorn_access_output_retains_its_argument_format(monkeypatch):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(AccessFormatter(
        '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
        use_colors=False,
    ))
    logger = logging.getLogger("uvicorn.access")
    monkeypatch.setattr(logger, "handlers", [handler])
    monkeypatch.setattr(logger, "propagate", False)
    monkeypatch.setattr(logger, "level", logging.INFO)
    main._configure_logging()
    formatter = handler.formatter
    main._configure_logging()
    assert handler.formatter is formatter  # Reconfiguration must not stack wrappers.

    logger.info('%s - "%s %s HTTP/%s" %d',
                "127.0.0.1:1234", "POST", WEBHOOK_PATH + "?attempt=1", "1.1", 200)

    output = stream.getvalue()
    assert WEBHOOK_SECRET not in output
    assert "127.0.0.1:1234" in output
    assert "POST /v1/webhooks/toss/" in output
    assert "?attempt=1 HTTP/1.1" in output
    assert "200 OK" in output


@pytest.mark.parametrize("name", ["uvicorn", "uvicorn.error"])
def test_uvicorn_error_output_redacts_exception_urls(monkeypatch, name):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(DefaultFormatter("%(levelprefix)s %(message)s", use_colors=False))
    logger = logging.getLogger(name)
    monkeypatch.setattr(logger, "handlers", [handler])
    monkeypatch.setattr(logger, "propagate", False)
    monkeypatch.setattr(logger, "level", logging.INFO)
    main._configure_logging()

    try:
        raise RuntimeError(f"upstream failed at https://toss.test{BILLING_PATH}")
    except RuntimeError:
        logger.exception("request failed path=%s request_id=request-uvicorn-42", WEBHOOK_PATH)

    output = stream.getvalue()
    assert WEBHOOK_SECRET not in output
    assert BILLING_KEY not in output
    assert "ERROR:" in output and "Traceback" in output
    assert "RuntimeError" in output and "request-uvicorn-42" in output


@pytest.mark.parametrize("transport_failure", [False, True])
def test_billing_failure_logs_hide_key_without_changing_outbound_request(
    app_logs, monkeypatch, transport_failure,
):
    _, stream = app_logs
    seen = []

    def respond(request):
        seen.append(request.url.path)
        if transport_failure:
            raise httpx.ConnectError("synthetic outage", request=request)
        return httpx.Response(403, json={"code": "REJECT_CARD_COMPANY", "message": "declined"})

    monkeypatch.setattr(toss_billing, "_transport_for", lambda _settings: httpx.MockTransport(respond))
    with pytest.raises(toss_billing.TossBillingError):
        asyncio.run(toss_billing.charge(
            make_settings(toss_api_base="https://toss.test", toss_billing_secret_key="synthetic-key"),
            billing_key=BILLING_KEY, customer_key="synthetic-customer", order_id="synthetic-order",
            order_name="test", amount=100,
        ))

    assert seen == [BILLING_PATH]
    output = stream.getvalue()
    assert BILLING_KEY not in output
    assert "/v1/billing/" in output
    assert ("ConnectError" if transport_failure else "code=REJECT_CARD_COMPANY") in output


def test_noncredential_billing_operation_keeps_diagnostic_path(app_logs):
    _, stream = app_logs
    logging.getLogger("wearless.toss_billing").warning(
        "billing failed path=%s status=403 code=REJECT_CARD_COMPANY",
        "/v1/billing/authorizations/issue",
    )

    output = stream.getvalue()
    assert "/v1/billing/authorizations/issue" in output
    assert "status=403 code=REJECT_CARD_COMPANY" in output
