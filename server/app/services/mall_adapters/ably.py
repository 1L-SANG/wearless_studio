"""Phase A placeholders, without a dependency on Phase B modules."""
from ..cafe24_client import Cafe24ApiError
from ..mall_registration_contract import CreatedProduct, LookupResult, MallFacts, PreflightIssue, RegistrationSnapshot
from .base import JobContext


class PreparingAdapter:
    def evaluate(self, snapshot: RegistrationSnapshot, facts: MallFacts) -> list[PreflightIssue]:
        return [dict(code='registration_preparing', path='mall', message='이 쇼핑몰 등록은 준비 중이에요', action='open_defaults')]

    async def prepare(self, ctx: JobContext) -> None:
        raise Cafe24ApiError('registration_preparing')

    async def create(self, ctx: JobContext) -> CreatedProduct:
        raise Cafe24ApiError('registration_preparing')

    async def lookup_created(self, ctx: JobContext) -> LookupResult:
        return dict(kind='inconclusive', productIds=[])

    async def complete_setup(self, ctx: JobContext) -> None:
        raise Cafe24ApiError('registration_preparing')
