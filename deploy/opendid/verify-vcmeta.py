#!/usr/bin/env python3
"""Compare Issuer VC status with OpenDID contract metadata without mutations."""

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path


SELECTOR = '5213be26'  # getVcmetaData(string), OpenDID V2 ABI
WORD_BYTES = 32
VCMETA_HEAD_WORDS = 10
STATUS_HEAD_INDEX = 4


def word(value):
    if value < 0:
        raise ValueError('negative ABI word')
    return value.to_bytes(WORD_BYTES, 'big')


def encode_vcmeta_call(vc_id):
    payload = vc_id.encode('utf-8')
    padding = (-len(payload)) % WORD_BYTES
    encoded = word(WORD_BYTES) + word(len(payload)) + payload + (b'\0' * padding)
    return '0x' + SELECTOR + encoded.hex()


def read_word(raw, offset):
    end = offset + WORD_BYTES
    if offset < 0 or offset % WORD_BYTES or end > len(raw):
        raise ValueError('invalid ABI word offset')
    return int.from_bytes(raw[offset:end], 'big')


def decode_vcmeta_status(result):
    if not isinstance(result, str) or not result.startswith('0x'):
        raise ValueError('invalid RPC result')
    try:
        raw = bytes.fromhex(result[2:])
    except ValueError as exc:
        raise ValueError('invalid RPC hex') from exc
    tuple_offset = read_word(raw, 0)
    head_end = tuple_offset + VCMETA_HEAD_WORDS * WORD_BYTES
    if tuple_offset < WORD_BYTES or head_end > len(raw):
        raise ValueError('invalid VC metadata tuple')
    status_relative = read_word(raw, tuple_offset + STATUS_HEAD_INDEX * WORD_BYTES)
    if status_relative < VCMETA_HEAD_WORDS * WORD_BYTES:
        raise ValueError('invalid VC metadata status offset')
    status_offset = tuple_offset + status_relative
    status_length = read_word(raw, status_offset)
    start = status_offset + WORD_BYTES
    end = start + status_length
    if end > len(raw):
        raise ValueError('truncated VC metadata status')
    return raw[start:end].decode('utf-8')


def self_check():
    expected_call = (
        '0x5213be26'
        '0000000000000000000000000000000000000000000000000000000000000020'
        '0000000000000000000000000000000000000000000000000000000000000002'
        '7663000000000000000000000000000000000000000000000000000000000000'
    )
    if encode_vcmeta_call('vc') != expected_call:
        raise SystemExit('vcmeta_codec=encoder_failed')
    fixture_words = [32, 0, 0, 0, 0, 320, 0, 0, 0, 0, 0, 6]
    fixture = (
        b''.join(value.to_bytes(32, 'big') for value in fixture_words)
        + b'ACTIVE' + (b'\0' * 26)
    )
    if decode_vcmeta_status('0x' + fixture.hex()) != 'ACTIVE':
        raise SystemExit('vcmeta_codec=decoder_failed')
    print('vcmeta_codec=ok')


def properties(path):
    values = {}
    for line in path.read_text().splitlines():
        if line and not line.lstrip().startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip()
    return values


def required_env(name):
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f'{name}=missing')
    return value


def query_rows(container, user, database):
    query = """
select json_build_object(
  'vcId', vc.vc_id,
  'status',
  case
    when vc.status = 'REVOKED' or coalesce(bool_or(rv.status = 'REVOKED'), false) then 'REVOKED'
    when vc.status = 'ACTIVE'
      and count(rv.vc_id) filter (where rv.status is distinct from 'ACTIVE') = 0 then 'ACTIVE'
    else 'UNKNOWN'
  end
)::text
from vc
left join revoke_vc rv on rv.vc_id = vc.vc_id
group by vc.id, vc.vc_id, vc.status
order by vc.vc_id nulls first, vc.id;
"""
    try:
        result = subprocess.run(
            [
                'docker', 'exec', container, 'psql', '-X', '-v', 'ON_ERROR_STOP=1',
                '-U', user, '-d', database, '-At', '-c', query,
            ],
            check=True, capture_output=True, text=True,
        )
        return [json.loads(line) for line in result.stdout.splitlines() if line]
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise SystemExit('issuer_vc_query=failed') from exc


def eth_call(rpc_url, contract, data):
    body = json.dumps({
        'jsonrpc': '2.0', 'method': 'eth_call',
        'params': [{'to': contract, 'data': data}, 'latest'], 'id': 1,
    }).encode()
    request = urllib.request.Request(
        rpc_url, body, {'Content-Type': 'application/json'}, method='POST',
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        result = json.load(response)
    if not isinstance(result, dict) or 'error' in result or not isinstance(result.get('result'), str):
        raise ValueError('eth_call failed')
    return result['result']


def verify():
    container = os.environ.get('OPENDID_POSTGRES_CONTAINER', 'postgre-opendid')
    user = required_env('OPENDID_POSTGRES_USER')
    database = required_env('OPENDID_ISSUER_DB')
    config_path = Path(os.environ.get(
        'OPENDID_BLOCKCHAIN_PROPERTIES',
        '/opt/opendid/secrets/CA/blockchain.properties',
    ))
    contract = properties(config_path).get('evm.contract.address', '')
    if len(contract) != 42 or not contract.startswith('0x'):
        raise SystemExit('onchain_contract=invalid_config')
    try:
        int(contract[2:], 16)
    except ValueError as exc:
        raise SystemExit('onchain_contract=invalid_config') from exc

    rpc_url = os.environ.get('OPENDID_BESU_RPC_URL', 'http://127.0.0.1:8545')
    rows = query_rows(container, user, database)
    counts = {'ACTIVE': 0, 'REVOKED': 0, 'query_error': 0}
    mismatches = 0
    for row in rows:
        db_status = row.get('status')
        vc_id = row.get('vcId')
        try:
            if not isinstance(vc_id, str) or not vc_id:
                raise ValueError('missing VC ID')
            chain_status = decode_vcmeta_status(
                eth_call(rpc_url, contract, encode_vcmeta_call(vc_id))
            )
        except Exception:
            counts['query_error'] += 1
            mismatches += 1
            continue
        if db_status in ('ACTIVE', 'REVOKED') and chain_status == db_status:
            counts[db_status] += 1
        else:
            mismatches += 1

    print(f'onchain_checked={len(rows)}')
    print(f'onchain_active={counts["ACTIVE"]}')
    print(f'onchain_revoked={counts["REVOKED"]}')
    print(f'onchain_query_error={counts["query_error"]}')
    print(f'onchain_mismatch={mismatches}')
    if mismatches:
        raise SystemExit('existing VC on-chain metadata mismatch')


def main():
    if sys.argv[1:] == ['--self-test']:
        self_check()
        return
    if sys.argv[1:]:
        raise SystemExit('usage: verify-vcmeta.py [--self-test]')
    self_check()
    verify()


if __name__ == '__main__':
    main()
