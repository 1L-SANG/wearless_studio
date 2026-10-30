"""The explicit registration adapter boundary. DTOs live in the shared contract."""
from typing import Any, Awaitable, Callable, Protocol

from ..mall_registration_contract import (
    CreatedProduct, LookupResult, MallFacts, PreflightIssue, RegistrationSnapshot,
)


class JobContext(Protocol):
    job: dict
    snapshot: RegistrationSnapshot
    client: Any
    storage: Any

    async def recheck(self) -> None: ...
    async def checkpoint(self, patch: dict) -> None: ...
    async def call(self, operation: Callable[..., Awaitable[Any]], *, effect: str) -> Any: ...
    async def mark_create_sent(self) -> None: ...


class MallAdapter(Protocol):
    def evaluate(self, snapshot: RegistrationSnapshot, facts: MallFacts) -> list[PreflightIssue]: ...
    async def prepare(self, ctx: JobContext) -> None: ...
    async def create(self, ctx: JobContext) -> CreatedProduct: ...
    async def lookup_created(self, ctx: JobContext) -> LookupResult: ...
    async def complete_setup(self, ctx: JobContext) -> None: ...


def get_adapter(mall: str) -> MallAdapter:
    from .ably import PreparingAdapter
    from .cafe24 import Cafe24Adapter

    registry = {'cafe24': Cafe24Adapter, 'zigzag': PreparingAdapter, 'ably': PreparingAdapter}
    return registry[mall]()
