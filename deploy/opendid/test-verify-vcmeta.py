#!/usr/bin/env python3
import importlib.util
import unittest
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


if __name__ == '__main__':
    unittest.main()
