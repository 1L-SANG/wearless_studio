"""C2PA Content Credential 서명기 (층②).

배포본에만 박는다. AI 컷 자산에 박아도 editorExport.js 의 캔버스 재렌더가 지운다.

🔴 하드룰 — 이 파일이 조립하는 매니페스트는 무제한 배포되는 파일 안에 들어간다.
   한 번 박으면 회수 불가다. build_manifest 는 **명시 키워드만 받는** 화이트리스트
   조립기다. DB 행을 **row 로 전개해 넘기지 말 것 — 시그니처가 TypeError 로 막는다.

동기 함수만 노출한다. 호출부가 asyncio.to_thread 로 감싼다(2026-08-26 이벤트루프 동결).
"""

from __future__ import annotations

import logging

logger = logging.getLogger("facemarket.c2pa")

#: 매니페스트에 절대 들어가면 안 되는 키·값 조각. 회귀 테스트가 이 집합을 순회한다.
#: camelCase·snake_case 양쪽 다 있어야 한다 — DB row 는 snake_case(Postgres 관례)라
#: 실제 유출 사고는 그쪽에서 난다. bare "ci" 는 일부러 뺐다: 부분 문자열 매치라
#: precision·specification 같은 무관한 값도 걸려 위양성만 만든다 — ciHash/ci_hash 가
#: 개념 자체는 이미 정확히 커버한다.
FORBIDDEN_MANIFEST_KEYS = frozenset({
    "faceImage", "face_image",
    "faceImageKey", "face_image_key",
    "faceImageUri", "face_image_uri",
    "faceImageDigest", "face_image_digest",
    "ciHash", "ci_hash",
    "birthDate", "birth_date",
    "birthYear", "birth_year",
    "displayName", "display_name",
    "realName", "real_name",
    "userId", "user_id",
    "r2Key", "r2_key",
    "presignedUrl", "presigned_url",
})

_AI_SOURCE_TYPE = (
    "http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"
)
_CUSTOM_LABEL = "kr.wearless.facemarket"


def build_manifest(
    *,
    model_id: str,
    license_id: str,
    vc_id: str | None,
    publication_id: str,
    verify_url: str,
    allowed_use: list[str],
    forbidden_use: list[str],
    license_valid_until: str,
    source_asset_ids: list[str],
    app_version: str,
) -> dict:
    """C2PA 매니페스트 조립. 키워드 화이트리스트 = 방어 ①.

    `claim_generator`(레거시 문자열)와 `claim_generator_info`(리스트) 를 둘 다 넣는다 —
    실측 확인(2026-09-04, c2pa-python 0.37.8): 라이브러리는 `claim_generator` 문자열을
    **조용히 버리고** 서명된 파일의 producer 신원은 `claim_generator_info` 만 읽는다.
    이걸 빼먹으면 파일에 "produced by c2pa-rs"만 남고 우리 이름은 어디에도 안 남는다
    (verifier 가 "produced by" 로 보여주는 바로 그 필드라 무해한 누락이 아니다).
    `claim_generator` 문자열은 하위호환·우리 쪽 로깅용으로 남겨둔다(라이브러리는 무시).
    """
    return {
        "claim_generator": f"wearless-facemarket/{app_version}",
        "claim_generator_info": [
            {"name": "wearless-facemarket", "version": app_version}
        ],
        "title": "FaceMarket generated image",
        "assertions": [
            {
                "label": "c2pa.actions",
                "data": {
                    "actions": [
                        {
                            "action": "c2pa.created",
                            "digitalSourceType": _AI_SOURCE_TYPE,
                        }
                    ]
                },
            },
            {
                "label": _CUSTOM_LABEL,
                "data": {
                    "modelId": str(model_id),
                    "licenseId": str(license_id),
                    "vcId": vc_id,
                    "publicationId": str(publication_id),
                    "verifyUrl": verify_url,
                    "allowedUse": list(allowed_use),
                    "forbiddenUse": list(forbidden_use),
                    "licenseValidUntil": license_valid_until,
                    "sourceAssetIds": [str(a) for a in source_asset_ids],
                },
            },
        ],
    }


class C2paSigner:
    """자체 발급 X.509(ES256) 리프 인증서로 매니페스트를 임베드한다.

    ⚠️ c2pa-python 0.37.8 은 **리프 인증서 자체가 self-signed(issuer==subject) 인 경우
    서명을 거부한다**("the certificate is invalid"). `gen_c2pa_cert.py` 가 로컬 루트
    CA 를 하나 만들어 리프를 그 CA 로 발급하는 이유다(루트는 폐기 가능한 임시키).

    검증기에서 '발급자 미확인'(signingCredential.untrusted)으로 뜨는 것은 알려진
    한계다(설계 §6.5) — 우리 루트가 공개 신뢰 목록에 없기 때문. 매니페스트 내용은
    읽히고 변조 감지도 동작한다. 신뢰의 무게는 verifyUrl → 공개 검증 페이지 → 체인이 진다.
    """

    def __init__(self, cert_pem: str, key_pem: str, app_version: str):
        self._cert = cert_pem.encode()
        self._key = key_pem.encode()
        self.app_version = app_version

    @classmethod
    def from_settings(cls, settings) -> "C2paSigner | None":
        """cert/key 둘 다 있어야 활성. FaceMarketChain.from_settings 와 같은 관례."""
        if not (settings.fm_c2pa_cert_pem and settings.fm_c2pa_key_pem):
            return None
        try:
            import c2pa  # noqa: F401 — 부재 시 즉시 비활성

            return cls(
                settings.fm_c2pa_cert_pem,
                settings.fm_c2pa_key_pem,
                # Settings 에 app_version 필드가 아직 없다(config.py 는 이번 라운드
                # 다른 태스크 소유라 여기서 추가 못 함 — TODO: 실제 배포 버전이
                # 배선되면 이 getattr 은 그 필드를 그냥 읽으면 된다). 필드가 생기기
                # 전까지 "0"처럼 진짜 버전으로 착각할 값 대신, 매니페스트에 박혀도
                # "안 배선됐다"는 게 그 자체로 드러나는 값을 쓴다.
                getattr(settings, "app_version", "unset"),
            )
        except Exception:
            logger.exception("c2pa_signer_init_failed")
            return None

    def sign(self, data: bytes, mime: str, manifest: dict) -> bytes:
        """서명된 바이트 반환. **동기** — 호출부가 to_thread 로 감쌀 것.

        실제 c2pa-python 0.37.8 API 는 브리핑의 가정(`c2pa.create_signer`/
        `c2pa.SigningAlg`)과 다르다: 서명자는 `C2paSignerInfo` 구조체를
        `Signer.from_info()` 에 넘겨 만든다. `Builder.sign()` 은 **매니페스트
        바이트만** 반환하고, 실제로 서명이 박힌 자산은 우리가 넘긴 `dst` 스트림에
        쓰인다 — `dst` 를 안 넘기면 내부에서 버려지는 버퍼로 가버려 자산이 유실된다.
        """
        import io
        import json

        import c2pa

        signer_info = c2pa.C2paSignerInfo(
            alg=c2pa.C2paSigningAlg.ES256,
            sign_cert=self._cert,
            private_key=self._key,
            # TSA(RFC-3161 트러스티드 타임스탬프) 없음 — 의도적. 있으면 인증서가
            # 만료된 뒤에도 "서명 당시엔 유효했다"를 증명할 수 있지만(읽어보면
            # signature_info.time 이 항상 None 인 이유), 그 대가로 서명 시점에
            # 외부 TSA 서비스에 매 요청 의존이 생긴다 — 이 경로는 다운로드를
            # 막으면 안 되는 경로라 그 의존을 의도적으로 피했다. 인증서 유효기간을
            # 10년으로 길게 잡아 당장 급하지 않다. 재검토 트리거: 인증서 로테이션
            # 주기가 짧아지거나(예: 1년 이하), "서명 당시 유효성" 증명이 실제
            # 분쟁·감사 요건으로 필요해지면 그때 tsa_url 을 채운다.
            ta_url=None,
        )
        with (
            c2pa.Signer.from_info(signer_info) as signer,
            c2pa.Builder(json.dumps(manifest)) as builder,
        ):
            src = io.BytesIO(data)
            dst = io.BytesIO()
            builder.sign(signer, mime, src, dst)
            return dst.getvalue()
