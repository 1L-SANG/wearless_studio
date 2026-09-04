"""C2PA 서명용 인증서 생성(운영 스크립트).

  uv run python scripts/gen_c2pa_cert.py --out-dir ./c2pa

만든 PEM 두 개를 FM_C2PA_CERT_PEM / FM_C2PA_KEY_PEM 으로 넣는다(prod = SSM SecureString).
유효기간을 길게 잡는 이유: 파일이 오래 돌아다닌다. 만료돼도 기존 파일은 동봉된 체인으로
계속 검증되고, 만료 후 새 서명만 skipped 로 떨어진다(만료 알림은 CloudWatch 경보로).

🔴 경험적으로 확인한 사실(2026-09-04, c2pa-python 0.37.8) — 최초 설계는 리프
인증서를 **자체서명(issuer == subject)** 으로 만들었는데, 실제 라이브러리는 서명
시점에 "the certificate is invalid" 로 거부했다. 원인은 EKU 가 아니라 self-signed
그 자체였다: CAI 공식 문서("Using test certificates")가 명시하길 SDK 는 서명용
리프의 self-signed 인증서를 아예 허용하지 않는다(c2pa-rs 의 테스트 픽스처도 항상
leaf+intermediate 체인이다). 그래서 이 스크립트는 로컬 루트 CA 를 하나 만들고
리프를 그 CA 로 **발급**한다 — 루트 개인키는 리프 발급 후 버려도 무방하다(폐기해도
이미 발급된 리프 서명은 계속 유효). 이렇게 만든 리프만으로 서명은 성공하고,
읽어보면 매니페스트도 정상 로드된다 — 다만 검증 결과에 여전히
`signingCredential.untrusted`("발급자 미확인")가 뜨는데, 이는 우리 루트가 공개
신뢰 anchor 목록에 없기 때문으로 알려진 한계다(설계 §6.5). EKU OID
1.3.6.1.5.5.7.3.36(documentSigning)은 라이브러리가 그대로 받아들였다 — 바꿀 필요
없었다.

🔑 운영자 책임: 이 스크립트가 쓰는 두 개인키 파일(`c2pa_key.pem`·`c2pa_root_key.pem`)은
0600으로 저장된다 — 커밋 금지, 로컬에 오래 두지 말 것. 리프 키는 SSM SecureString
으로 옮긴 뒤, 루트 키는 재발급 용도가 끝나면 삭제해도 된다(이미 발급된 리프 서명은
루트 키 없이도 계속 유효하다).
"""

import argparse
import datetime as dt
import os
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

VALID_DAYS = 3650


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="./c2pa")
    ap.add_argument("--common-name", default="Wearless FaceMarket")
    args = ap.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    now = dt.datetime.now(dt.timezone.utc)
    not_before = now - dt.timedelta(minutes=5)
    not_after = now + dt.timedelta(days=VALID_DAYS)

    # ---- 로컬 루트 CA(self-signed, CA:true) — 리프 발급 전용, 발급 후 폐기 가능 ----
    root_key = ec.generate_private_key(ec.SECP256R1())
    root_name = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "KR"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Wearless"),
        x509.NameAttribute(NameOID.COMMON_NAME, f"{args.common_name} Root CA"),
    ])
    root_cert = (
        x509.CertificateBuilder()
        .subject_name(root_name)
        .issuer_name(root_name)
        .public_key(root_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True,
                crl_sign=True, encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(root_key.public_key()),
            critical=False,
        )
        .sign(root_key, hashes.SHA256())
    )

    # ---- 리프(서명용) — 루트가 발급, self-signed 아님(issuer=root_name != subject) ----
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf_name = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "KR"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Wearless"),
        x509.NameAttribute(NameOID.COMMON_NAME, args.common_name),
    ])
    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(leaf_name)
        .issuer_name(root_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=False,
                crl_sign=False, encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        # C2PA 는 documentSigning EKU(1.3.6.1.5.5.7.3.36)를 요구한다. c2pa-python
        # 0.37.8 로 실측 확인(2026-09-04) — 라이브러리가 이 OID 를 그대로 받아들였다.
        # 만약 향후 버전에서 거부하면 c2pa-python signing 문서가 명시한 OID 로 교체하고
        # 이 주석을 갱신한다.
        .add_extension(
            x509.ExtendedKeyUsage([x509.ObjectIdentifier("1.3.6.1.5.5.7.3.36")]),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(root_key.public_key()),
            critical=False,
        )
        .sign(root_key, hashes.SHA256())
    )

    # FM_C2PA_CERT_PEM / FM_C2PA_KEY_PEM 으로 들어가는 두 파일. sign_cert 로는 리프
    # 단독으로 충분하다(중간 CA 없이 루트가 바로 리프를 발급했으므로 체인에 얹을
    # 중간 인증서가 없다 — 루트 자체는 트러스트 anchor 가 아니라서 넣지 않는다).
    cert_path = out / "c2pa_cert.pem"
    key_path = out / "c2pa_key.pem"
    cert_path.write_bytes(leaf_cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        leaf_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    # 이 파일이 곧 FM_C2PA_KEY_PEM(운영 서명 키)이 된다 — umask 022 기본값이면
    # write_bytes 만으로는 0644(world-readable)로 나간다. 개인키라 반드시 0600.
    os.chmod(key_path, 0o600)

    # 루트는 참고용으로만 남긴다 — 재발급에 필요할 수 있으나 서명 경로는 위 두 파일만
    # 쓴다. 개인키를 넓히는 선택이라 마찬가지로 0600(root 는 이 스크립트를 다시 돌려
    # 재발급을 마친 뒤 삭제해도 무방 — 이미 발급된 리프 서명에는 영향 없음).
    root_cert_path = out / "c2pa_root_cert.pem"
    root_key_path = out / "c2pa_root_key.pem"
    root_cert_path.write_bytes(root_cert.public_bytes(serialization.Encoding.PEM))
    root_key_path.write_bytes(
        root_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    os.chmod(root_key_path, 0o600)

    print(f"wrote {cert_path} and {key_path} (0600, valid {VALID_DAYS}d)")
    print(f"wrote {root_cert_path} and {root_key_path} (0600, issuer — keep offline, delete when done reissuing)")


if __name__ == "__main__":
    main()
