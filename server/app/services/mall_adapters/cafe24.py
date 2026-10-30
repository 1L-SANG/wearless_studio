"""Cafe24's initial setup, using only the injected context and client."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json

import httpx

from ..cafe24_client import Cafe24ApiError, Cafe24Variant, build_description_html, price_fields
from ..mall_images import prepare_mall_jpeg
from ..mall_registration_contract import (
    CreatedProduct, LookupResult, MallFacts, PreflightIssue, RegistrationSnapshot, VariantSnapshot,
)
from .base import JobContext


def _hash(value):
    return hashlib.sha256(value if isinstance(value, bytes) else json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def _country(value):
    # A finite supported table, never infer an unknown country's origin.
    countries = {'대한민국': 'KR', '한국': 'KR', 'KR': 'KR', '중국': 'CN', 'CN': 'CN',
                 '일본': 'JP', 'JP': 'JP', '미국': 'US', 'US': 'US', '베트남': 'VN', 'VN': 'VN',
                 '이탈리아': 'IT', 'IT': 'IT', '프랑스': 'FR', 'FR': 'FR', '인도': 'IN', 'IN': 'IN',
                 '방글라데시': 'BD', 'BD': 'BD', '인도네시아': 'ID', 'ID': 'ID', '대만': 'TW', 'TW': 'TW'}
    return countries.get(''.join(str(value).split()).upper())


def build_cafe24_product_request(snapshot: RegistrationSnapshot, *, description: str,
                                 price_basis: str, external_code: str) -> dict:
    basis = 'tax_excluded' if price_basis == 'B' else 'selling_price'
    if snapshot['priceSetting'] != price_basis or snapshot['price']['basis'] != basis:
        raise Cafe24ApiError('price_basis_changed')
    variants = snapshot['variants']
    colors = list(dict.fromkeys(row['colorName'] for row in variants))
    sizes = list(dict.fromkeys(row['size'] for row in variants))
    if not variants or len(colors) * len(sizes) != len(variants):
        raise Cafe24ApiError('variant_mapping_mismatch')
    request = dict(product_name=snapshot['productName'], supply_price=snapshot['price']['amount'],
        display='F', selling='F', tax_type='B' if snapshot['sales']['taxType'] == 'FREE' else 'A',
        product_condition='N', has_option='T', option_type='S',
        options=[dict(name='색상', value=colors), dict(name='사이즈', value=sizes)],
        shipping_fee_by_product='F', custom_product_code=external_code,
        description=description, mobile_description=description, **price_fields(price_basis, snapshot['price']['amount']))
    notice = snapshot['notice']['fields']
    if notice.get('materials'):
        request['product_material'] = notice['materials']
    if origin := _country(notice.get('origin', '')):
        request['made_in_code'] = origin
    if snapshot.get('category'):
        request['add_category_no'] = [dict(category_no=int(snapshot['category']['categoryId']), recommend='F', new='F')]
    return dict(shop_no=1, request=request)


def match_cafe24_variants(expected: list[VariantSnapshot], actual: list[Cafe24Variant]) -> dict[str, str]:
    def fail():
        raise Cafe24ApiError('variant_mapping_mismatch')
    wanted = {(row['colorName'], row['size']): row['variantKey'] for row in expected}
    if len(wanted) != len(expected) or len(actual) != len(expected):
        fail()
    result, codes = {}, set()
    for row in actual:
        options = row.get('options')
        if not isinstance(options, list) or len(options) != 2 or any(not isinstance(v, dict) for v in options):
            fail()
        names = {option.get('name'): option.get('value') for option in options}
        if set(names) != {'색상', '사이즈'} or any(not isinstance(v, str) for v in names.values()):
            fail()
        key, code = wanted.get((names['색상'], names['사이즈'])), row.get('variantCode')
        if key is None or key in result or not isinstance(code, str) or not code or code in codes:
            fail()
        result[key] = code
        codes.add(code)
    return result


def _integer(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value))
        return int(number) if number.is_finite() and number == number.to_integral_value() else None
    except (InvalidOperation, ValueError, OverflowError):
        return None


def _inventory(row):
    def flag(key):
        return row.get(key) if row.get(key) in ('T', 'F') else None
    return dict(quantity=_integer(row.get('quantity')), additionalAmount=_integer(row.get('additionalAmount')),
        useInventory=flag('useInventory'), display=flag('display'), selling=flag('selling'),
        displaySoldout=flag('displaySoldout'), safetyInventory=_integer(row.get('safetyInventory')))


def _target(row):
    return dict(quantity=row['quantity'], additionalAmount=row['additionalAmount'], useInventory='T', display='T',
        selling='T' if row['quantity'] else 'F', displaySoldout='T', safetyInventory=0)


async def _stage(ctx, stage, **values):
    checkpoints = deepcopy(ctx.job['metadata'].get('checkpoints') or {})
    checkpoints[stage] = {**checkpoints.get(stage, {}), **values}
    await ctx.checkpoint(dict(stage=stage, checkpoints=checkpoints))


class Cafe24Adapter:
    def __init__(self):
        self._prepared = {}

    def evaluate(self, snapshot: RegistrationSnapshot, facts: MallFacts) -> list[PreflightIssue]:
        issues = []
        if not facts.get('complete') or not facts.get('imageRatio'):
            issues.append(dict(code='image_setting_unavailable', path='catalog', message='쇼핑몰 이미지 설정을 확인해 주세요', action='choose_image'))
        if snapshot['priceSetting'] != facts.get('priceBasis'):
            issues.append(dict(code='price_basis_changed', path='price', message='가격 기준을 다시 확인해 주세요', action='edit_price'))
        return issues

    async def _image(self, ctx, image):
        asset_id = image['assetId']
        if asset_id in self._prepared:
            return self._prepared[asset_id]
        await ctx.recheck()
        raw = await asyncio.to_thread(ctx.storage.get_bytes, image['r2Key'])
        ratio = ctx.snapshot['imageRatio']
        jpeg = await asyncio.to_thread(prepare_mall_jpeg, raw, ratio=(ratio['width'], ratio['height']))
        value = dict(assetId=asset_id, sourceHash=_hash(raw), imageHash=_hash(jpeg), ratio=ratio)
        saved = (ctx.job['metadata'].get('checkpoints', {}).get('preparing_images') or {}).get('images') or []
        previous = next((v for v in saved if v['assetId'] == asset_id), None)
        if previous is not None and previous != value:
            raise Cafe24ApiError('image_upload_mismatch')
        self._prepared[asset_id] = (jpeg, value)
        return jpeg, value

    async def prepare(self, ctx: JobContext) -> None:
        files = ctx.snapshot['publication']['files']
        checkpoint = ctx.job['metadata'].get('checkpoints', {}).get('uploading_images') or {}
        files_hash = _hash(files)
        if checkpoint and checkpoint.get('filesHash') != files_hash:
            raise Cafe24ApiError('image_upload_mismatch')
        urls = list(checkpoint.get('urls') or [])
        if len(urls) > len(files):
            raise Cafe24ApiError('image_upload_mismatch')
        await _stage(ctx, 'uploading_images', filesHash=files_hash, urls=urls, status='pending')
        pending = None
        while len(urls) < len(files):
            batch, size = [], 0
            for file in files[len(urls):len(urls) + 8]:
                await ctx.recheck()
                raw = pending if pending is not None else await asyncio.to_thread(ctx.storage.get_bytes, file['r2_key'])
                pending = None
                encoded_size = 4 * ((len(raw) + 2) // 3)
                if encoded_size > 20 * 1024 * 1024:
                    raise Cafe24ApiError('image_upload_mismatch')
                if size + encoded_size > 20 * 1024 * 1024:
                    pending = raw
                    break
                batch.append(raw)
                size += encoded_size
            uploaded = await ctx.call(lambda token: ctx.client.upload_description_images(token, batch), effect='image_copy')
            if len(uploaded) != len(batch):
                raise Cafe24ApiError('image_upload_mismatch')
            urls.extend(uploaded)
            await _stage(ctx, 'uploading_images', filesHash=files_hash, urls=urls, status='pending')
        await _stage(ctx, 'uploading_images', status='complete')
        await _stage(ctx, 'preparing_images', status='pending')
        prepared = []
        for image in ctx.snapshot['images']:
            _, value = await self._image(ctx, image)
            prepared.append(value)
        await _stage(ctx, 'preparing_images', status='complete', images=prepared)
        await _stage(ctx, 'price_setting', status='pending')
        try:
            basis = await ctx.call(ctx.client.product_setting, effect='read')
        except (Cafe24ApiError, httpx.HTTPError, TimeoutError):
            raise Cafe24ApiError('price_setting_unavailable') from None
        description = build_description_html(urls, ctx.snapshot['productName'], ctx.snapshot['publication']['width'])
        body = build_cafe24_product_request(ctx.snapshot, description=description, price_basis=basis,
                                            external_code=ctx.job['metadata']['externalCode'])
        await _stage(ctx, 'price_setting', status='complete', basis=basis, requestHash=_hash(body))

    async def create(self, ctx: JobContext) -> CreatedProduct:
        checkpoints = ctx.job['metadata']['checkpoints']
        body = build_cafe24_product_request(ctx.snapshot,
            description=build_description_html(checkpoints['uploading_images']['urls'], ctx.snapshot['productName'], ctx.snapshot['publication']['width']),
            price_basis=checkpoints['price_setting']['basis'], external_code=ctx.job['metadata']['externalCode'])
        if _hash(body) != checkpoints['price_setting']['requestHash']:
            raise Cafe24ApiError('price_basis_changed')
        no = await ctx.call(lambda token: ctx.client.create_product(token, body), effect='create')
        # Commit the creation fact before any setup or new remote call.
        await ctx.checkpoint(dict(productId=str(no)))
        return dict(productId=str(no))

    async def lookup_created(self, ctx: JobContext) -> LookupResult:
        try:
            ids = await ctx.call(lambda token: ctx.client.find_products_by_custom_code(token, ctx.job['metadata']['externalCode']), effect='read')
        except (Cafe24ApiError, httpx.HTTPError, TimeoutError):
            return dict(kind='inconclusive', productIds=[])
        ids = list(dict.fromkeys(str(no) for no in ids))
        return dict(kind='found' if len(ids) == 1 else 'multiple' if ids else 'none', productIds=ids)

    async def _state(self, ctx):
        return await ctx.call(lambda token: ctx.client.product_state(token, int(ctx.job['metadata']['productId'])), effect='read')

    async def _hidden(self, ctx):
        if (ctx.job['metadata'].get('checkpoints', {}).get('activating') or {}).get('sentAt'):
            raise Cafe24ApiError('remote_product_changed')
        state = await self._state(ctx)
        if state['display'] != 'F' or state['selling'] != 'F':
            await ctx.checkpoint(dict(resumeBlocked=True))
            raise Cafe24ApiError('remote_product_changed')

    async def _variants(self, ctx):
        rows = await ctx.call(lambda token: ctx.client.variants(token, int(ctx.job['metadata']['productId'])), effect='read')
        try:
            mapping = match_cafe24_variants(ctx.snapshot['variants'], rows)
        except Cafe24ApiError:
            if ctx.job['metadata'].get('checkpoints', {}).get('mapping_variants'):
                await ctx.checkpoint(dict(resumeBlocked=True))
                raise Cafe24ApiError('remote_product_changed') from None
            raise
        saved = ctx.job['metadata'].get('checkpoints', {}).get('mapping_variants') or {}
        if saved and mapping != saved.get('mapping'):
            await ctx.checkpoint(dict(resumeBlocked=True))
            raise Cafe24ApiError('remote_product_changed')
        return rows, mapping

    async def _check_inventory(self, ctx, *, complete):
        rows, mapping = await self._variants(ctx)
        before = ctx.job['metadata']['checkpoints']['mapping_variants']['before']
        actual = {row['variantCode']: _inventory(row) for row in rows}
        for variant in ctx.snapshot['variants']:
            code = mapping[variant['variantKey']]
            if actual[code] != _target(variant) and (complete or actual[code] != before[code]):
                await ctx.checkpoint(dict(resumeBlocked=True))
                raise Cafe24ApiError('remote_product_changed')
        return actual

    async def complete_setup(self, ctx: JobContext) -> None:
        metadata = ctx.job['metadata']
        if metadata.get('resumeBlocked'):
            raise Cafe24ApiError('remote_product_changed')
        activation = metadata.get('checkpoints', {}).get('activating') or {}
        desired = dict(display='F', selling='F')
        if ctx.snapshot['sales']['listingMode'] == 'ON_SALE':
            desired = dict(display='T', selling='T' if any(v['quantity'] > 0 for v in ctx.snapshot['variants']) else 'F')
        if activation.get('sentAt'):
            # An order may already have reduced stock. Never re-run setup or write
            # inventory after this fence, including when activation's response was lost.
            state = await self._state(ctx)
            if any(state[key] != value for key, value in desired.items()):
                await _stage(ctx, 'activating', status='unknown')
                raise Cafe24ApiError('activation_unconfirmed')
            await _stage(ctx, 'activating', status='complete', **desired)
            await ctx.checkpoint(dict(stage='complete'))
            return
        if not metadata.get('checkpoints', {}).get('mapping_variants'):
            await ctx.checkpoint(dict(stage='mapping_variants'))
        await self._hidden(ctx)
        rows, mapping = await self._variants(ctx)
        if not metadata.get('checkpoints', {}).get('mapping_variants'):
            await _stage(ctx, 'mapping_variants', status='complete', mapping=mapping,
                         before={row['variantCode']: _inventory(row) for row in rows})
        inventory = metadata['checkpoints'].get('setting_inventory') or {}
        await self._check_inventory(ctx, complete=inventory.get('status') == 'complete')
        if inventory.get('status') != 'complete':
            await _stage(ctx, 'setting_inventory', status='pending')
            changes = [dict(variant_code=mapping[row['variantKey']], quantity=row['quantity'], additional_amount=row['additionalAmount'],
                display='T', selling='T' if row['quantity'] else 'F', use_inventory='T', display_soldout='T', safety_inventory=0)
                for row in ctx.snapshot['variants']]

            async def update(token):
                # Also check after token wait and before each absolute PUT retry.
                await self._hidden(ctx)
                await self._check_inventory(ctx, complete=False)
                await _stage(ctx, 'setting_inventory', sentAt=datetime.now(timezone.utc).isoformat())
                await ctx.recheck()
                return await ctx.client.update_variants(token, int(metadata['productId']), changes)

            await ctx.call(update, effect='absolute_setup')
            rows, _ = await self._variants(ctx)
            actual = {row['variantCode']: _inventory(row) for row in rows}
            if any(actual[mapping[row['variantKey']]] != _target(row) for row in ctx.snapshot['variants']):
                raise Cafe24ApiError('inventory_setup_incomplete')
            await _stage(ctx, 'setting_inventory', status='complete')
        images = ctx.snapshot['images']
        thumbnail = metadata['checkpoints'].get('uploading_thumbnail') or {}
        if thumbnail.get('status') != 'complete':
            await _stage(ctx, 'uploading_thumbnail', status='pending')
            jpeg, value = await self._image(ctx, images[0])

            async def upload(token):
                await self._hidden(ctx)
                await ctx.recheck()
                return await ctx.client.upload_product_image(token, int(metadata['productId']), jpeg)

            await ctx.call(upload, effect='image_copy')
            await _stage(ctx, 'uploading_thumbnail', status='complete', **value)
        additional = metadata['checkpoints'].get('uploading_additional_images') or {}
        if additional.get('status') != 'complete':
            await ctx.checkpoint(dict(stage='uploading_additional_images'))
            if additional.get('sentAt'):
                await _stage(ctx, 'uploading_additional_images', status='unknown')
                raise Cafe24ApiError('additional_images_unknown')
            prepared = [await self._image(ctx, image) for image in images[1:]]
            hashes = [value['imageHash'] for _, value in prepared]
            await _stage(ctx, 'uploading_additional_images', status='pending', imageHashes=hashes)
            if prepared:
                async def append(token):
                    await self._hidden(ctx)
                    await _stage(ctx, 'uploading_additional_images', sentAt=datetime.now(timezone.utc).isoformat())
                    await ctx.recheck()
                    try:
                        return await ctx.client.upload_additional_images(token, int(metadata['productId']), [jpeg for jpeg, _ in prepared])
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code == 401:
                            raise
                        raise Cafe24ApiError('additional_images_unknown') from None
                    except (Cafe24ApiError, httpx.RequestError, TimeoutError):
                        # No confirmed image identity/list contract exists. Turn transport
                        # failures into a non-retryable error before the image-copy wrapper.
                        raise Cafe24ApiError('additional_images_unknown') from None
                try:
                    await ctx.call(append, effect='image_copy')
                except (Cafe24ApiError, httpx.HTTPError, TimeoutError):
                    if metadata['checkpoints']['uploading_additional_images'].get('sentAt'):
                        await _stage(ctx, 'uploading_additional_images', status='unknown')
                    raise
            await _stage(ctx, 'uploading_additional_images', status='complete')
        await self._hidden(ctx)
        await self._check_inventory(ctx, complete=True)
        await _stage(ctx, 'activating', status='pending')

        async def activate(token):
            await self._hidden(ctx)
            await self._check_inventory(ctx, complete=True)
            await _stage(ctx, 'activating', sentAt=datetime.now(timezone.utc).isoformat(), **desired)
            await ctx.recheck()
            return await ctx.client.set_product_state(token, int(metadata['productId']), **desired)

        try:
            await ctx.call(activate, effect='activate')
        except (Cafe24ApiError, httpx.HTTPError, TimeoutError):
            if not (metadata['checkpoints'].get('activating') or {}).get('sentAt'):
                raise
            # Only a GET may resolve an ambiguous activation, never another PUT.
        state = await self._state(ctx)
        if any(state[key] != value for key, value in desired.items()):
            await _stage(ctx, 'activating', status='unknown')
            raise Cafe24ApiError('activation_unconfirmed')
        await _stage(ctx, 'activating', status='complete', **desired)
        await ctx.checkpoint(dict(stage='complete'))
