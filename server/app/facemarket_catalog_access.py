"""모델 리스트 열람 자격.

오너 결정(2026-09-23): 모델 리스트는 **등록된 셀러와, 2차 등록까지 마친 모델**만 본다.
화면(/models)과 목록 API(GET /v1/facemarket/public/models)가 이 판정 하나를 같이 쓴다.
판정은 조회 한 번으로 끝난다(CATALOG_ACCESS_SQL).

누가 보는가
- 관리자: profiles.role = 'admin'.
- 셀러: 셀러 약관 동의 기록(seller_consents)이 있는 계정. 셀러 앱은 기록이 없으면 먼저 동의를
  받으므로, 셀러 앱을 쓰는 셀러에게는 기록이 있다. 협찬 상세를 셀러에게만 보이는 기준과 같다.
- 모델: **2차 등록을 마친 모델**. 데이터로는 "본인 모델(fm_models.user_id)에 발급된 라이선스가
  있다"이다. 2차 등록 위저드의 마지막 단계('라이선스 증서 발급하기')가 끝나면 한 트랜잭션에서
  fm_licenses.status 가 'active' 가 되고 fm_biometric_enrollments.status 가 'passed' 가 된다
  (facemarket.py 의 라이선스 발급 처리). 그래서 active 라이선스가 있으면 2차 등록 완료다.
  재등록 중인 모델은 이전 라이선스가 'reverification_required' 로 내려가 있는데, 이미 2차를
  마친 모델이므로 함께 본다.

누가 못 보는가
- 비로그인, 로그인만 한 계정(구글로 가입만 한 사람).
- 1차 지원서만 낸 사람, 1차 승인 뒤 2차 등록을 진행 중인 사람. 라이선스가 아직 없거나
  'pending' 이다.
- 철회했거나 라이선스가 취소된 모델('revoked'), 만료된 라이선스('expired').

판정을 바꿀 때는 이 설명, CATALOG_ACCESS_SQL, server/tests/test_facemarket_catalog_access.py 를
같이 고친다.
"""

from typing import Literal

CatalogRole = Literal["admin", "seller", "model"]

# 2차 등록을 마쳤다고 보는 라이선스 상태. 위 설명의 '모델' 항목 참고.
REGISTERED_MODEL_LICENSE_STATUSES = ("active", "reverification_required")

CATALOG_ACCESS_SQL = """
select exists(select 1 from profiles where user_id = %s and role = 'admin') as catalog_is_admin,
       exists(select 1 from seller_consents where user_id = %s) as catalog_is_seller,
       exists(select 1 from fm_licenses l
                join fm_models m on m.id = l.model_id
               where m.user_id = %s
                 and l.status in ('active', 'reverification_required')) as catalog_is_model
"""


async def catalog_access(conn, user_id: str) -> dict:
    """{'allowed': bool, 'role': 'admin'|'seller'|'model'|None, 'seller': bool}.

    seller 는 역할과 따로 돌려준다. 관리자이면서 셀러 약관에도 동의한 계정은 역할이 'admin' 이지만
    협찬 상세(셀러 전용)는 받아야 하기 때문이다.
    """
    async with conn.cursor() as cur:
        await cur.execute(CATALOG_ACCESS_SQL, (user_id,) * 3)
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
