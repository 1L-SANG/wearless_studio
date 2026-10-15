"""Remove payment credentials from the server's formatted log output."""

import logging
import re
from copy import copy


_PAYMENT_PATH = re.compile(
    # This is a fixed operation path, not a billing key.
    r"/v1/billing/authorizations/issue(?=$|[/\s?#'\"<>:\x1b])"
    r"|(?P<prefix>/v1/(?:webhooks/toss|billing)/)[^/\s?#'\"<>\x1b]+"
)


def redact_payment_paths(text: str) -> str:
    return _PAYMENT_PATH.sub(
        lambda match: f"{match['prefix']}[redacted]" if match['prefix'] else match[0],
        text,
    )


class _PaymentSafeFormatter(logging.Formatter):
    def __init__(self, formatter: logging.Formatter | None):
        super().__init__()
        self.formatter = formatter or logging.Formatter()

    def format(self, record: logging.LogRecord) -> str:
        # Render first: Uvicorn needs its original argument tuple, while extras
        # and exception URLs only appear in the final formatted string.
        return redact_payment_paths(self.formatter.format(copy(record)))


def protect_log_handler(handler: logging.Handler) -> None:
    if not isinstance(handler.formatter, _PaymentSafeFormatter):
        handler.setFormatter(_PaymentSafeFormatter(handler.formatter))
