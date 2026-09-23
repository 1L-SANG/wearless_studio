"""모델 리스트 열람 자격 (2026-09-23 오너 결정: 등록된 셀러와 모델만 본다).

화면(/models)과 목록 API(GET /v1/facemarket/public/models)가 같은 판정을 쓴다.

- 관리자: profiles.role = 'admin'.
- 셀러: 셀러 약관 동의 기록(seller_consents)이 있는 계정. 셀러 앱은 기록이 없으면 먼저 동의를
  받으므로, 셀러 앱을 쓰는 셀러에게는 기록이 있다. 협찬 상세를 셀러에게만 보이는 기준과 같다.
- 모델: 모델 기록(fm_models)이 있거나, 심사 중이거나 승인된 지원서가 있는 계정.
  반려·취소된 지원서만 있으면 모델이 아니다.

로그인만 한 계정(구글로 가입만 한 사람)은 자격이 없다. 판정은 한 번의 조회로 끝낸다.
"""

from typing import Literal

CatalogRole = Literal["admin", "seller", "model"]

CATALOG_ACCESS_SQL = """
select exists(select 1 from profiles where user_id = %s and role = 'admin') as catalog_is_admin,
       exists(select 1 from seller_consents where user_id = %s) as catalog_is_seller,
       (exists(select 1 from fm_models where user_id = %s)
        or exists(select 1 from fm_model_applications
                   where user_id = %s and status in ('under_review', 'approved'))) as catalog_is_model
"""


async def catalog_access(conn, user_id: str) -> dict:
    """{'allowed': bool, 'role': 'admin'|'seller'|'model'|None, 'seller': bool}.

    seller 는 역할과 따로 돌려준다. 관리자이면서 셀러 약관에도 동의한 계정은 역할이 'admin' 이지만
    협찬 상세(셀러 전용)는 받아야 하기 때문이다.
    """
    async with conn.cursor() as cur:
        await cur.execute(CATALOG_ACCESS_SQL, (user_id,) * 4)
        row = await cur.fetchone() or {}
    seller = bool(row.get("catalog_is_seller"))
    if row.get("catalog_is_admin"):
        role = "admin"
    elif seller:
        role = "seller"
    elif row.get("catalog_is_model"):
        role = "model"
    else:
        role = None
    return {"allowed": role is not None, "role": role, "seller": seller}
