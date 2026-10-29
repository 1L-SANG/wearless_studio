"""The single, credential-free registration input, snapshot and public view contract."""
from copy import deepcopy
import hashlib
import json
import unicodedata
from typing import Literal, TypedDict
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from .mall_defaults import mall_default_fields, normalize_mall_defaults, mall_defaults_view

MAX_AMOUNT = 2_147_483_647
MALLS = ('cafe24', 'zigzag', 'ably')
NOTICE_FIELDS = ('name', 'materials', 'colors', 'sizes', 'maker', 'origin', 'care', 'madeAt', 'warranty', 'contact')
SETUP_STAGES = ('mapping_variants', 'setting_inventory', 'uploading_thumbnail', 'uploading_additional_images', 'activating')


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class PriceOverride(StrictModel):
    amount: StrictInt = Field(ge=1, le=MAX_AMOUNT)
    basis: Literal['selling_price', 'tax_excluded']


class VariantOverride(StrictModel):
    variantKey: str = Field(min_length=1, max_length=512)
    quantity: StrictInt | None = Field(default=None, ge=0, le=MAX_AMOUNT)
    additionalAmount: StrictInt | None = Field(default=None, ge=0, le=MAX_AMOUNT)

    @field_validator('quantity', 'additionalAmount', mode='before')
    @classmethod
    def nonnull(cls, value):
        if value is None:
            raise ValueError('null is not an amount')
        return value


class CategorySelection(StrictModel):
    categoryId: str = Field(min_length=1, max_length=256)


class SourceVersions(StrictModel):
    connectionVersion: str
    mallDefaultsRevision: StrictInt = Field(ge=0)
    catalogVersion: str
    editorRevision: StrictInt = Field(ge=0)


class RegisterDraftInput(StrictModel):
    projectId: UUID
    publicationId: UUID | None = None
    editorRevision: StrictInt = Field(ge=0)
    selectedMalls: list[Literal['cafe24', 'zigzag', 'ably']] = Field(min_length=1, max_length=3)
    productName: str = Field(min_length=1, max_length=100)
    basePrice: StrictInt = Field(ge=1, le=MAX_AMOUNT)
    defaultQuantity: StrictInt = Field(default=0, ge=0, le=MAX_AMOUNT)
    priceOverrides: dict[Literal['cafe24', 'zigzag', 'ably'], PriceOverride] = Field(default_factory=dict)
    sizes: list[str] = Field(min_length=1, max_length=200)
    variantOverrides: list[VariantOverride] = Field(default_factory=list, max_length=200)
    mainAssetId: UUID | None = None
    additionalAssetIds: list[UUID] = Field(default_factory=list, max_length=4)
    categories: dict[Literal['cafe24', 'zigzag', 'ably'], CategorySelection] = Field(default_factory=dict)
    confirmedSourcesByMall: dict[Literal['cafe24', 'zigzag', 'ably'], SourceVersions] = Field(default_factory=dict)
    salesOverrides: dict = Field(default_factory=dict)

    @field_validator('productName', mode='before')
    @classmethod
    def product_name(cls, value):
        if not isinstance(value, str):
            return value
        if any(unicodedata.category(ch) in ('Cc', 'Cs') for ch in value):
            raise ValueError('control characters are not allowed')
        value = ' '.join(value.split())
        if not value:
            raise ValueError('product name required')
        return value

    @field_validator('selectedMalls')
    @classmethod
    def selected(cls, value):
        if len(set(value)) != len(value):
            raise ValueError('duplicate mall')
        return sorted(value)

    @field_validator('sizes')
    @classmethod
    def size_labels(cls, value):
        if not isinstance(value, list) or any(not isinstance(label, str) for label in value):
            raise ValueError('size labels must be text')
        normalized = [' '.join(label.split()) for label in value]
        if any(not label or len(label) > 100 or any(unicodedata.category(c).startswith('C') for c in label)
               for label in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError('invalid or duplicate size')
        return normalized

    @field_validator('salesOverrides')
    @classmethod
    def sales(cls, value):
        if value.keys() - {'listingMode', 'shippingDays', 'shipping', 'returns', 'taxType', 'productOrigin'}:
            raise ValueError('unknown sales override')
        normalize_mall_defaults('zigzag', {}, value)
        return value


class RegisterDraft(TypedDict):
    projectId: str
    publicationId: str | None
    editorRevision: int
    selectedMalls: list[str]
    productName: str
    basePrice: int
    defaultQuantity: int
    priceOverrides: dict
    sizes: list[str]
    variantOverrides: list[dict]
    mainAssetId: str | None
    additionalAssetIds: list[str]
    categories: dict
    confirmedSourcesByMall: dict
    salesOverrides: dict


# Server DTOs contain only resolved, owned data. Each snapshot is a detached JSON value.
class RegistrationSource(TypedDict, total=False):
    project: dict
    product: dict
    editorBlocks: list[dict]
    publication: dict
    assets: list[dict]
    mallSpecificDefaults: dict[str, dict]
    recoveries: dict[str, list[dict]]


class MallFacts(TypedDict, total=False):
    mall: str
    status: str
    connectionId: str
    connectionVersion: str
    shopId: str
    catalogVersion: str
    complete: bool
    categories: list[dict]
    priceBasis: str
    imageRatio: dict
    capabilities: dict
    issues: list[dict]


class RegistrationSnapshot(TypedDict):
    productName: str
    price: dict
    variants: list[dict]
    images: list[dict]
    notice: dict
    category: dict | None
    sales: dict
    mallDefaults: dict
    publication: dict
    imageRatio: dict
    catalogVersion: str
    priceSetting: str


class LookupResult(TypedDict):
    kind: Literal['found', 'none', 'multiple', 'inconclusive']
    productIds: list[str]


class CreatedProduct(TypedDict):
    productId: str


NoticeResolution = dict
VariantSnapshot = dict
PreflightIssue = dict
PreflightResult = dict
CategoryNode = dict
CategorySuggestion = dict


class ContractError(ValueError):
    def __init__(self, code, path, message, action):
        self.issue = dict(code=code, path=path, message=message, action=action)
        super().__init__(code)


def normalize_register_draft(value: dict) -> RegisterDraft:
    data = RegisterDraftInput.model_validate(value).model_dump(mode='json', exclude_unset=False)
    data['variantOverrides'] = [VariantOverride.model_validate(v).model_dump(exclude_none=True) for v in value.get('variantOverrides', [])]
    data['additionalAssetIds'] = list(dict.fromkeys(a for a in data['additionalAssetIds'] if a != data['mainAssetId']))
    return data


def registration_request_hash(draft: RegisterDraft) -> str:
    return hashlib.sha256(json.dumps(draft, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _text(value):
    if not isinstance(value, str):
        return ''
    value = value.strip()
    return '' if value == '정보 입력 필요' else value


def resolve_notice(blocks: list[dict], product: dict, defaults: dict) -> NoticeResolution:
    values = {key: set() for key in NOTICE_FIELDS}
    for block in blocks:
        if block.get('infoType') == 'required_notice':
            for field in (block.get('info') or {}).get('fields', []):
                if field.get('key') in values and _text(field.get('value')):
                    values[field['key']].add(_text(field['value']))
    conflicts = [key for key, choices in values.items() if len(choices) > 1]
    fields = {key: next(iter(choices)) if len(choices) == 1 else '' for key, choices in values.items()}
    if fields['sizes'].upper() == 'FREE':
        fields['sizes'] = ''
    sources = {key: 'editor' for key, value in fields.items() if value}
    disclosure = defaults.get('disclosure') or {}
    materials = ', '.join(str(m.get('name', '')) + (f" {m['ratio']}%" if m.get('ratio') is not None else '')
                          for m in product.get('materials', []) if isinstance(m, dict))
    measurements = []
    care = ''
    for block in blocks:
        info = block.get('info') or {}
        if block.get('kind') == 'size':
            for row in info.get('rows', []):
                actual = ', '.join(f'{k} {v}' for k, v in (row.get('values') or {}).items() if str(v).strip() and v is not None)
                if actual:
                    measurements.append(f"{row.get('label', '')}: {actual} {info.get('unit', 'cm')}")
        if block.get('kind') == 'care':
            care = _text(info.get('text'))
    supplements = {
        'name': product.get('name'), 'materials': materials,
        'colors': ', '.join(c.get('name', '') for c in product.get('colors', []) if isinstance(c, dict)),
        'sizes': '; '.join(measurements), 'maker': disclosure.get('manufacturer'),
        'origin': disclosure.get('country'), 'care': care, 'warranty': disclosure.get('warrantyText'),
        'contact': f"{disclosure['asManager']} {disclosure['phone']}" if disclosure.get('asManager') and disclosure.get('phone') else '',
    }
    for key, value in supplements.items():
        if not fields[key] and key not in conflicts and _text(value):
            fields[key], sources[key] = _text(value), 'saved'
    return dict(fields=fields, sources=sources, conflicts=conflicts, missing=[k for k, v in fields.items() if not v])


def build_variants(product: dict, sizes: list[str], default_quantity: int, overrides: list[dict]) -> list[VariantSnapshot]:
    sizes = RegisterDraftInput.size_labels(sizes)
    if type(default_quantity) is not int or not 0 <= default_quantity <= MAX_AMOUNT:
        raise ValueError('invalid quantity')
    colors = product.get('colors') or []
    if not colors or any(not isinstance(c, dict) or not _text(c.get('id')) or not _text(c.get('name')) for c in colors):
        raise ValueError('saved color required')
    if len({c['id'] for c in colors}) != len(colors):
        raise ValueError('duplicate color')
    changes = {}
    for value in overrides:
        value = VariantOverride.model_validate(value).model_dump(exclude_none=True)
        if value['variantKey'] in changes:
            raise ValueError('duplicate override')
        changes[value['variantKey']] = value
    result = []
    for color in colors:
        for size in sizes:
            key = json.dumps([color['id'], size], ensure_ascii=False, separators=(',', ':'))
            change = changes.pop(key, {})
            result.append(dict(variantKey=key, colorId=color['id'], colorName=color['name'], size=size,
                               quantity=change.get('quantity', default_quantity), additionalAmount=change.get('additionalAmount', 0)))
    if changes:
        raise ValueError('unknown variant key')
    return result


def freeze_registration(draft: RegisterDraft, *, source: RegistrationSource, facts: MallFacts) -> RegistrationSnapshot:
    from ..detail_publications import publication_files
    project, publication = source['project'], source.get('publication')
    if not publication or publication.get('status') != 'ready':
        raise ContractError('publication_required', 'publicationId', '상세페이지를 먼저 게시해 주세요', 'choose_image')
    if publication['editor_revision'] != draft['editorRevision'] or project['editor_revision'] != draft['editorRevision']:
        raise ContractError('editor_revision_changed', 'editorRevision', '편집본이 바뀌었어요. 저장하고 다시 확인해 주세요', 'choose_image')
    try:
        files = publication_files(publication)
    except ValueError:
        raise ContractError('invalid_files', 'publicationId', '게시 이미지를 다시 확인해 주세요', 'choose_image') from None
    blocks = source.get('editorBlocks', project.get('editor_blocks') or [])
    try:
        labels = [row.get('label', '') for b in blocks if b.get('kind') == 'size'
                  for row in (b.get('info') or {}).get('rows', [])] or ['FREE']
        saved_sizes = RegisterDraftInput.size_labels(labels)
    except (ValueError, TypeError, AttributeError):
        raise ContractError('invalid_options', 'sizes', '저장된 사이즈표의 이름을 확인해 주세요', 'edit_options') from None
    if saved_sizes != draft['sizes']:
        raise ContractError('sizes_changed', 'sizes', '사이즈표를 저장한 뒤 다시 확인해 주세요', 'edit_options')
    try:
        variants = build_variants(source.get('product') or {}, draft['sizes'], draft['defaultQuantity'], draft['variantOverrides'])
    except ValueError:
        raise ContractError('invalid_options', 'variantOverrides', '색상과 사이즈, 수량을 확인해 주세요', 'edit_options') from None
    if len(variants) > 200:
        raise ContractError('too_many_variants', 'sizes', '옵션 조합은 200개까지 등록할 수 있어요', 'edit_options')
    basis = 'tax_excluded' if facts.get('priceBasis') == 'B' else 'selling_price'
    price = draft['priceOverrides'].get(facts['mall'])
    if (basis == 'tax_excluded' and not price) or (price and price['basis'] != basis):
        raise ContractError('price_basis_changed', 'priceOverrides.' + facts['mall'], '쇼핑몰의 가격 기준을 다시 확인해 주세요', 'edit_price')
    price = price or dict(amount=draft['basePrice'], basis=basis)
    if any(price['amount'] + v['additionalAmount'] > MAX_AMOUNT for v in variants):
        raise ContractError('price_overflow', 'variantOverrides', '옵션 추가금액을 확인해 주세요', 'edit_price')
    if facts['mall'] == 'zigzag' and any((price['amount'] + v['additionalAmount']) % 10 for v in variants):
        raise ContractError('price_unit', 'basePrice', '10원 단위로 가격을 입력해 주세요', 'edit_price')
    assets = {a['id']: a for a in source['assets']}
    ids = [draft['mainAssetId'], *draft['additionalAssetIds']]
    if not ids[0]:
        raise ContractError('main_image_required', 'mainAssetId', '대표 이미지를 골라 주세요', 'choose_image')
    if any(i not in assets for i in ids):
        raise ContractError('asset_not_found', 'mainAssetId', '이 프로젝트의 이미지를 골라 주세요', 'choose_image')
    if any(assets[i].get('mime_type') not in ('image/jpeg', 'image/png', 'image/webp') for i in ids):
        raise ContractError('image_format', 'mainAssetId', '정지 이미지를 준비해 주세요', 'choose_image')
    mall = facts['mall']
    defaults = (source.get('mallSpecificDefaults') or {}).get(mall) or {}
    overrides = {k: v for k, v in draft['salesOverrides'].items() if k in mall_default_fields(mall)}
    sales = mall_defaults_view(mall, {**normalize_mall_defaults(mall, defaults, overrides), 'revision': defaults.get('revision', 0)})
    product = {**(source.get('product') or {}), 'name': draft['productName'],
               'materials': (source.get('analysis') or {}).get('materials', [])}
    notice = resolve_notice(blocks, product, sales)
    category = draft['categories'].get(facts['mall'])
    if category and category['categoryId'] not in {c['categoryId'] for c in facts.get('categories', [])}:
        raise ContractError('category_changed', 'categories.' + facts['mall'], '카테고리를 다시 골라 주세요', 'choose_category')
    return deepcopy(dict(productName=draft['productName'], price=price, variants=variants,
        images=[dict(assetId=i, r2Key=assets[i]['r2_key'], mimeType=assets[i]['mime_type'],
                     width=assets[i].get('width'), height=assets[i].get('height')) for i in ids],
        notice=notice, category=category, sales=sales,
        mallDefaults=mall_defaults_view(mall, defaults),
        publication=dict(id=publication['id'], width=publication['width'], files=files),
        imageRatio=facts['imageRatio'], catalogVersion=facts['catalogVersion'], priceSetting=facts['priceBasis']))


def derive_registration_outcome(job: dict) -> str | None:
    if job['status'] in ('pending', 'running'):
        return None
    metadata, result = job.get('metadata') or {}, job.get('result') or {}
    if metadata.get('productId'):
        return 'created'
    if metadata.get('rejectedAt'):
        return 'failed'
    if (metadata.get('create') or {}).get('sentAt') or result.get('outcome') in ('unknown', 'duplicate_suspected'):
        return 'duplicate_suspected' if result.get('outcome') == 'duplicate_suspected' else 'unknown'
    return 'failed'


def can_resume_registration(job: dict) -> bool:
    if job['payload'].get('mall') != 'cafe24' or derive_registration_outcome(job) != 'created':
        return False
    if (job.get('metadata') or {}).get('resumeBlocked'):
        return False
    checkpoint = (job.get('metadata') or {}).get('checkpoints', {}).get('uploading_additional_images') or {}
    requests = checkpoint.get('requests') or []
    if checkpoint.get('status') == 'unknown' or (checkpoint.get('sentAt') and checkpoint.get('status') != 'complete') or any(
            row.get('status') == 'unknown' or (row.get('sentAt') and row.get('status') != 'complete') for row in requests):
        return False
    return True


def registration_view(job: dict) -> dict:
    metadata, result, payload = job.get('metadata') or {}, job.get('result') or {}, job.get('payload') or {}
    outcome = derive_registration_outcome(job)
    if outcome is None:
        setup, retry = 'pending', 'none'
    elif outcome == 'created':
        complete = result.get('setupStatus') == 'complete' or all((metadata.get('checkpoints', {}).get(s) or {}).get('status') == 'complete' for s in SETUP_STAGES)
        setup = 'complete' if complete else 'needs_attention'
        retry = 'none' if complete else ('resume' if can_resume_registration(job) else 'open_admin')
    elif outcome in ('unknown', 'duplicate_suspected'):
        setup, retry = 'pending', 'verify' if payload.get('mall') == 'cafe24' else 'open_admin'
    else:
        setup, retry = 'not_applicable', 'new_request'
    return dict(jobId=job['id'], mall=payload.get('mall'), status=job['status'], progress=job.get('progress', 0),
        stage=metadata.get('stage'), outcome=outcome, setupStatus=setup, productId=metadata.get('productId'),
        productUrl=result.get('productUrl'), adminUrl=result.get('adminUrl'), stages=deepcopy(metadata.get('checkpoints') or {}),
        retryAction=retry, code=result.get('code') if outcome is not None else None,
        warnings=deepcopy(metadata.get('warnings') or result.get('warnings') or []), retention=result.get('retention'),
        publicationId=payload.get('publicationId'), createdAt=job.get('created_at'), finishedAt=job.get('finished_at'))
