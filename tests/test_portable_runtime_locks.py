import json
import tempfile
import unittest
from pathlib import Path

from tools.verify_portable import _runtime_lock


class RuntimeLockValidationTests(unittest.TestCase):
    def test_known_runtime_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'MeowKeeperPlusHost.json'
            payload = {'lock_name': 'MeowKeeperPlusHost', 'role': 'host', 'pid': 1,
                       'executable': 'python.exe', 'command_line': ['run_host.py'], 'created_at': 1.0}
            path.write_text(json.dumps(payload), encoding='utf-8')
            self.assertTrue(_runtime_lock(path, 'data/locks/MeowKeeperPlusHost.json'))
            self.assertFalse(_runtime_lock(path, 'data/private.json'))
            payload['role'] = 'other'
            path.write_text(json.dumps(payload), encoding='utf-8')
            with self.assertRaises(ValueError):
                _runtime_lock(path, 'data/locks/MeowKeeperPlusHost.json')

    def test_oversize_sidecar_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'MeowKeeperPlusHost.json'
            path.write_text('x' * 16385, encoding='utf-8')
            with self.assertRaises(ValueError):
                _runtime_lock(path, 'data/locks/MeowKeeperPlusHost.json')
