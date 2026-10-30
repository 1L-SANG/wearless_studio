"""Connection-generation scoped, complete read-only registration facts."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
from time import monotonic

from .. import repo
from ..config import is_allowed_mall, registration_enabled
from .cafe24_client import Cafe24Client, classify_error
from .cafe24_tokens import call_with_token
from .mall_registration_contract import MallFacts, CategoryNode, CategorySuggestion

CATALOG_CONTRACT = 'multimall-a02-1'
REQUIRED_SCOPES = {'mall.read_product', 'mall.write_product', 'mall.read_store', 'mall.read_category'}


def connection_version(connection):
    return hashlib.sha256(connection['id'].encode()).hexdigest() if connection else None


def registration_capability(settings, mall, connection):
    if mall != 'cafe24' or not registration_enabled(settings):
        return dict(status='preparing')
    if not connection:
        return dict(status='reconnect_required', reasonCode='connection_required')
    if not connection.get('refresh_expires_at') or connection['refresh_expires_at'] <= datetime.now(timezone.utc):
        return dict(status='reconnect_required', reasonCode='reconnect_required')
    if not REQUIRED_SCOPES <= set(connection.get('scopes') or []):
        return dict(status='reconnect_required', reasonCode='missing_permission')
    if not is_allowed_mall(settings, connection['shop_id']):
        return dict(status='preparing')
    return dict(status='available')


def invalidate_catalog(app, user_id, mall):
    cache = getattr(app.state, 'mall_catalog_cache', {})
    for key in list(cache):
        if key[:2] == (user_id, mall):
            del cache[key]


async def load_mall_facts(app, *, user_id: str, mall: str) -> MallFacts:
    async with app.state.pool.connection() as conn:
        connection = await repo.get_mall_connection(conn, user_id=user_id, mall=mall)
    capability = registration_capability(app.state.settings, mall, connection)
    facts = dict(mall=mall, **capability, connectionId=connection['id'] if connection else None,
        connectionVersion=connection_version(connection), shopId=connection['shop_id'] if connection else None,
        complete=False, categories=[], capabilities={}, catalogVersion=None, imageRatio=None, priceBasis=None)
    if facts['status'] != 'available':
        return facts
    if not hasattr(app.state, 'mall_catalog_cache'):
        app.state.mall_catalog_cache = {}
    cache = app.state.mall_catalog_cache
    now = monotonic()
    for key, (expires, _) in list(cache.items()):
        if expires <= now:
            del cache[key]
    key = (user_id, mall, connection['id'], CATALOG_CONTRACT)
    if key in cache:
        return deepcopy(cache[key][1])
    settings = app.state.settings
    client = Cafe24Client(connection['shop_id'], client_id=settings.cafe24_client_id,
                          client_secret=settings.cafe24_client_secret, api_version=settings.cafe24_api_version)
    async def read(fn):
        return await call_with_token(app, user_id=user_id, connection_id=connection['id'], fn=fn)
    try:
        categories = {}
        complete = False
        for offset in range(0, 5000, 100):
            rows = await read(lambda token: client.categories(token, limit=100, offset=offset))
            if len(rows) > 100:
                raise ValueError('invalid page')
            for row in rows:
                category_id = row.get('category_no')
                if type(category_id) is not int or category_id <= 0 or not isinstance(row.get('category_name'), str):
                    raise ValueError('invalid category')
                if row.get('use_display') == 'F':
                    continue
                categories[str(category_id)] = dict(categoryId=str(category_id), name=row['category_name'],
                    parentCategoryId=str(row['parent_category_no']) if row.get('parent_category_no') else None,
                    depth=row.get('category_depth'))
            if len(rows) < 100:
                complete = True
                break
        facts.update(categories=list(categories.values()), complete=complete)
        facts['priceBasis'] = await read(client.product_setting)
        facts['imageRatio'] = await read(client.image_setting)
        facts['capabilities'] = dict(options=True, maxVariants=200, maxAdditionalImages=4, shippingSource='mall')
        facts['catalogVersion'] = hashlib.sha256(json.dumps({k: facts[k] for k in (
            'categories', 'complete', 'priceBasis', 'imageRatio', 'capabilities')}, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if complete:
            cache[key] = (monotonic() + 600, deepcopy(facts))
    except Exception as exc:
        code = classify_error(exc)
        facts.update(complete=False, issues=[dict(code=code, path='catalog', message='쇼핑몰 설정을 확인하지 못했어요. 다시 확인해 주세요', action='reconnect')])
    return facts


def recommend_categories(product: dict, categories: list[CategoryNode]) -> list[CategorySuggestion]:
    def normalize(text):
        return re.sub(r'[^\w가-힣]', '', str(text).casefold())
    aliases = {'knit': '니트', 'cardigan': '가디건', 'jeans': '청바지', 'shirt': '셔츠', 'pants': '바지', 'skirt': '스커트', 'dress': '원피스'}
    values = [product.get(k, '') for k in ('clothingType', 'subCategory', 'customCategory', 'name')]
    terms = {normalize(value) for value in values if value}
    translated = {normalize(aliases[value]) for value in values if value in aliases}
    nodes = {node['categoryId']: node for node in categories}
    scored = []
    for node in categories:
        names, seen, current = [], set(), node
        while current and current['categoryId'] not in seen:
            seen.add(current['categoryId'])
            names.append(normalize(current['name']))
            current = nodes.get(current.get('parentCategoryId'))
        name, path = normalize(node['name']), ''.join(reversed(names))
        score = (300 if name in terms else 200 if name in translated else
                 100 if any(term in path for term in terms | translated) else 0)
        if score:
            scored.append(dict(categoryId=node['categoryId'], name=node['name'], score=score))
    scored.sort(key=lambda item: (-item['score'], item['categoryId']))
    if not scored:
        return []
    best = [item for item in scored if item['score'] == scored[0]['score']]
    return [{**item, 'suggested': len(best) == 1} for item in best]
