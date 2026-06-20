#!/usr/bin/env python
"""Phase 4 인프라 셋업 (일회용·멱등). 사용자 승인 하에 실제 R2/DB/.env에 기록.
 1) spike/.env 의 GEMINI_API_KEY → server/.env 복사
 2) spike/base 의 남/여 베이스 마네킹 → R2 업로드 + assets(source=seed) 행 (멱등)
 3) MANNEQUIN_BASE_WOMEN/MEN_ASSET_ID → server/.env 기록
 4) credit_accounts.balance >= 100 grant (테스트용)
"""
import io
import os
import re
import uuid

import psycopg
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_ENV = os.path.join(HERE, ".env")
SPIKE_ENV = os.path.join(HERE, "..", "spike", ".env")
BASE_DIR = os.path.join(HERE, "..", "spike", "base")
BASES = {"women": "base-female-2K.png", "men": "base-male-2K.png"}


def parse_env(path):
    out = {}
    if not os.path.exists(path):
        return out
    for line in open(path, encoding="utf-8"):
        if line.strip().startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def append_env(path, key, value):
    env = parse_env(path)
    if env.get(key):
        print(f"  .env: {key} 이미 있음 (유지)")
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"\n{key}={value}")
    print(f"  .env: {key} 추가")


def main():
    senv = parse_env(SERVER_ENV)
    spike = parse_env(SPIKE_ENV)

    # 1) GEMINI_API_KEY 복사
    if not senv.get("GEMINI_API_KEY") and spike.get("GEMINI_API_KEY"):
        append_env(SERVER_ENV, "GEMINI_API_KEY", spike["GEMINI_API_KEY"])
    else:
        print("  GEMINI_API_KEY:", "이미 있음" if senv.get("GEMINI_API_KEY") else "spike에도 없음 ⚠️")

    # R2 (boto3)
    import boto3
    from botocore.config import Config
    endpoint = senv.get("R2_ENDPOINT") or f"https://{senv['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com"
    s3 = boto3.client("s3", endpoint_url=endpoint, aws_access_key_id=senv["R2_ACCESS_KEY_ID"],
                      aws_secret_access_key=senv["R2_SECRET_ACCESS_KEY"], region_name="auto",
                      config=Config(signature_version="s3v4"))
    bucket = senv["R2_BUCKET"]
    conn = psycopg.connect(senv["DATABASE_URL"])

    # 2)+3) 베이스 마네킹 seed (멱등: r2_key 기준)
    ids = {}
    for gender, fname in BASES.items():
        data = open(os.path.join(BASE_DIR, fname), "rb").read()
        im = Image.open(io.BytesIO(data))
        key = f"seed/mannequin/base-{gender}-2K.png"
        s3.put_object(Bucket=bucket, Key=key, Body=data, ContentType="image/png")
        with conn.cursor() as cur:
            cur.execute("select id::text from assets where r2_key = %s", (key,))
            row = cur.fetchone()
            if row:
                aid = row[0]
            else:
                aid = str(uuid.uuid4())
                cur.execute(
                    "insert into assets (id, user_id, project_id, source, visibility, r2_bucket, "
                    "r2_key, mime_type, byte_size, width, height) "
                    "values (%s, null, null, 'seed', 'private', %s, %s, 'image/png', %s, %s, %s)",
                    (aid, bucket, key, len(data), im.width, im.height))
        ids[gender] = aid
        print(f"  base {gender}: asset {aid} ({im.width}x{im.height}) key={key}")
    conn.commit()
    append_env(SERVER_ENV, "MANNEQUIN_BASE_WOMEN_ASSET_ID", ids["women"])
    append_env(SERVER_ENV, "MANNEQUIN_BASE_MEN_ASSET_ID", ids["men"])

    # 4) 테스트 크레딧 grant — 단일 테스트 계정만 (전체 mass-update 금지)
    with conn.cursor() as cur:
        cur.execute("select user_id::text from credit_accounts")
        accounts = [r[0] for r in cur.fetchall()]
        if len(accounts) != 1:
            print(f"  ⚠️ credit_accounts {len(accounts)}행 — 대상 모호, 크레딧 grant 건너뜀 (수동 지정 필요)")
        else:
            cur.execute("update credit_accounts set balance = greatest(balance, 100) "
                        "where user_id = %s returning user_id::text, balance, reserved",
                        (accounts[0],))
            uid, bal, res = cur.fetchone()
            print(f"  credit grant → {uid}: balance={bal} reserved={res}")
    conn.commit()
    conn.close()
    print("DONE")


if __name__ == "__main__":
    main()
