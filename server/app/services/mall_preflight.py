"""Pure per-mall evaluation. No jobs, quotas, remote writes or source mutations."""
from .mall_registration_contract import (
    RegisterDraft, RegistrationSource, MallFacts, PreflightResult, ContractError,
    freeze_registration, registration_request_hash, registration_view,
)


def issue(code, path, message, action):
    return dict(code=code, path=path, message=message, action=action)


def source_versions(draft, source, facts):
    return dict(connectionVersion=facts['connectionVersion'],
        salesDefaultsRevision=(source.get('salesDefaults') or {}).get('revision', 0),
        mallDefaultsRevision=((source.get('mallSpecificDefaults') or {}).get(facts['mall']) or {}).get('revision', 0),
        catalogVersion=facts['catalogVersion'], editorRevision=draft['editorRevision'])


def evaluate_preflight(draft: RegisterDraft, *, source: RegistrationSource, facts_by_mall: dict[str, MallFacts]) -> PreflightResult:
    items = []
    for mall in draft['selectedMalls']:
        facts = facts_by_mall[mall]
        item = dict(mall=mall, status='blocked', issues=[], warnings=[])
        if facts['status'] == 'preparing':
            item.update(status='preparing', issues=[issue('registration_preparing', 'mall', '이 쇼핑몰 등록은 준비 중이에요', 'open_defaults')])
        elif facts['status'] != 'available':
            item['issues'].append(issue(facts.get('reasonCode', 'reconnect_required'), 'connection', '쇼핑몰을 다시 연결해 주세요', 'reconnect'))
        elif not facts.get('complete'):
            item['issues'] = facts.get('issues') or [issue('catalog_incomplete', 'catalog', '쇼핑몰 설정을 끝까지 확인하지 못했어요', 'choose_category')]
        elif (source.get('recoveries') or {}).get(mall):
            existing = source['recoveries'][mall][0]
            view = registration_view(existing)
            action = 'resume_existing' if view['retryAction'] == 'resume' else 'verify_existing'
            item.update(existingJobId=existing['id'], issues=[issue('registration_unresolved', 'registration', '이전 등록 상태를 먼저 확인해 주세요', action)])
        else:
            try:
                snapshot = freeze_registration(draft, source=source, facts=facts)
            except ContractError as exc:
                item['issues'].append(exc.issue)
            else:
                notice = snapshot['notice']
                if mall == 'zigzag':
                    for field in notice['conflicts']:
                        item['issues'].append(issue('notice_conflict', 'notice.' + field, '고시정보가 서로 달라요. 사용할 값을 골라 주세요', 'fill_notice'))
                    for field in notice['missing']:
                        item['issues'].append(issue('notice_missing', 'notice.' + field, '고시정보를 채워 주세요', 'fill_notice'))
                else:
                    for field in ('materials', 'origin'):
                        if not notice['fields'][field]:
                            item['warnings'].append(issue('notice_missing', 'notice.' + field, '고시정보를 확인해 주세요', 'fill_notice'))
                if not snapshot['category']:
                    item['warnings'].append(issue('category_recommended', 'categories.' + mall, '카테고리를 고르면 상품을 찾기 쉬워요', 'choose_category'))
                if all(v['quantity'] == 0 for v in snapshot['variants']):
                    item['warnings'].append(issue('all_sold_out', 'defaultQuantity', '모든 옵션이 품절로 등록돼요', 'edit_options'))
                if notice['fields']['name'] != draft['productName']:
                    item['warnings'].append(issue('notice_name_differs', 'notice.name', '고시 품명과 등록 상품명이 달라요', 'fill_notice'))
                if not item['issues']:
                    item.update(status='ready', sourceVersions=source_versions(draft, source, facts),
                        summary=dict(variantCount=len(snapshot['variants']), price=snapshot['price']['amount'], shippingSource='mall'))
        items.append(item)
    return dict(editorRevision=draft['editorRevision'], inputHash=registration_request_hash(draft),
        readyMalls=[i['mall'] for i in items if i['status'] == 'ready'],
        blockedMalls=[i['mall'] for i in items if i['status'] != 'ready'], items=items)
