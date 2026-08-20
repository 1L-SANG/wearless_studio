#!/usr/bin/env python3
import importlib.util
import json
import subprocess
import unittest
from unittest.mock import patch
from pathlib import Path


SCRIPT = Path(__file__).with_name('verify-vcmeta.py')


def load_module():
    if not SCRIPT.is_file():
        raise AssertionError('verify-vcmeta.py is missing')
    spec = importlib.util.spec_from_file_location('verify_vcmeta', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VcMetaCodecTest(unittest.TestCase):
    def test_encodes_get_vcmetadata_string_call(self):
        module = load_module()
        expected = (
            '0x5213be26'
            '0000000000000000000000000000000000000000000000000000000000000020'
            '0000000000000000000000000000000000000000000000000000000000000002'
            '7663000000000000000000000000000000000000000000000000000000000000'
        )
        self.assertEqual(module.encode_vcmeta_call('vc'), expected)

    def test_decodes_status_from_vcmetadata_tuple(self):
        module = load_module()
        words = [32, 0, 0, 0, 0, 320, 0, 0, 0, 0, 0, 6]
        fixture = (
            b''.join(value.to_bytes(32, 'big') for value in words)
            + b'ACTIVE' + (b'\0' * 26)
        )
        self.assertEqual(module.decode_vcmeta_status('0x' + fixture.hex()), 'ACTIVE')

    def test_rejects_truncated_status(self):
        module = load_module()
        words = [32, 0, 0, 0, 0, 320, 0, 0, 0, 0, 0, 6]
        fixture = b''.join(value.to_bytes(32, 'big') for value in words)
        with self.assertRaisesRegex(ValueError, 'truncated'):
            module.decode_vcmeta_status('0x' + fixture.hex())


class IssuerStatusQueryTest(unittest.TestCase):
    def query_rows(self, db_rows):
        module = load_module()
        captured = {}

        def fake_run(args, check, capture_output, text):
            captured['query'] = args[-1]
            effective_rows = []
            for vc_id, vc_status, revoke_statuses in db_rows:
                if 'revoke_vc' not in captured['query']:
                    status = vc_status
                elif vc_status == 'REVOKED' or 'REVOKED' in revoke_statuses:
                    status = 'REVOKED'
                elif vc_status == 'ACTIVE' and all(status == 'ACTIVE' for status in revoke_statuses):
                    status = 'ACTIVE'
                else:
                    status = 'UNKNOWN'
                effective_rows.append({'vcId': vc_id, 'status': status})
            return subprocess.CompletedProcess(
                args,
                0,
                '\n'.join(json.dumps(row) for row in effective_rows) + '\n',
                '',
            )

        with patch('subprocess.run', fake_run):
            rows = module.query_rows('postgre-opendid', 'issuer', 'issuer_db')
        return rows, captured['query']

    def test_related_revoked_protocol_row_makes_active_vc_effectively_revoked(self):
        rows, _ = self.query_rows([('vc-1', 'ACTIVE', ['REVOKED'])])

        self.assertEqual(rows, [{'vcId': 'vc-1', 'status': 'REVOKED'}])

    def test_active_vc_without_revoke_row_stays_active(self):
        rows, _ = self.query_rows([('vc-1', 'ACTIVE', [])])

        self.assertEqual(rows, [{'vcId': 'vc-1', 'status': 'ACTIVE'}])

    def test_revoked_vc_stays_revoked(self):
        rows, _ = self.query_rows([('vc-1', 'REVOKED', [])])

        self.assertEqual(rows, [{'vcId': 'vc-1', 'status': 'REVOKED'}])

    def test_unknown_status_fails_closed(self):
        rows, _ = self.query_rows([
            ('vc-1', 'SUSPENDED', []),
            ('vc-2', 'ACTIVE', ['PENDING']),
        ])

        self.assertEqual(rows, [
            {'vcId': 'vc-1', 'status': 'UNKNOWN'},
            {'vcId': 'vc-2', 'status': 'UNKNOWN'},
        ])

    def test_query_groups_revoke_rows_by_vc_without_multiplying_rows(self):
        rows, query = self.query_rows([('vc-1', 'ACTIVE', ['ACTIVE', 'REVOKED'])])

        self.assertEqual(rows, [{'vcId': 'vc-1', 'status': 'REVOKED'}])
        self.assertIn('group by vc.id, vc.vc_id, vc.status', query.lower())


if __name__ == '__main__':
    unittest.main()
