from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

from meowkeeper.core.analytics.cpu_temperature import BrokerCpuProvider, CpuTemperatureMonitor

ROOT = Path(__file__).resolve().parents[1]


class CpuSensorBrokerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        (self.folder / 'request.txt').write_text('', encoding='ascii')
        self.provider = BrokerCpuProvider(self.folder)

    def payload(self, *, seconds=0):
        return {'schema_version': 1, 'broker_schema': 1, 'provider': 'libre_hardware_monitor',
                'status': 'available', 'observed_at': (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat(),
                'sensors': [{'hardware_name': 'Intel CPU', 'sensor_name': 'CPU Package',
                             'sensor_id': '/intelcpu/0/temperature/0', 'temperature_c': 57.0}]}

    def test_fresh_cache_and_bounded_timestamp_request(self):
        (self.folder / 'temperature.json').write_text(json.dumps(self.payload()), encoding='utf-8')
        result = self.provider.read()
        self.assertEqual(result['temperature_c'], 57)
        self.assertEqual(result['transport'], 'protected_local_cache')
        self.assertTrue(self.provider.alive)
        self.assertLess((self.folder / 'request.txt').stat().st_size, 128)
        self.provider.close()
        self.assertEqual((self.folder / 'request.txt').read_bytes(), b'')

    def test_missing_cache_keeps_lease_until_next_task(self):
        monitor = CpuTemperatureMonitor([self.provider])
        monitor.sample()
        self.assertEqual(monitor.snapshot()['error_reason'], 'broker_starting')
        self.assertEqual(monitor.retry_at, 0)
        self.assertTrue((self.folder / 'request.txt').read_text())
        monitor.stop()
        self.assertEqual((self.folder / 'request.txt').read_bytes(), b'')

    def test_expired_cache_never_returns_old_temperature(self):
        (self.folder / 'temperature.json').write_text(json.dumps(self.payload(seconds=60)), encoding='utf-8')
        value = self.provider.read()
        self.assertIsNone(value['temperature_c'])
        self.assertEqual(value['error_reason'], 'broker_starting')

    def test_oversize_or_wrong_contract_cache_is_rejected(self):
        cache = self.folder / 'temperature.json'
        for content in ['x' * 65537, '{}', json.dumps({**self.payload(), 'broker_schema': 9})]:
            cache.write_text(content, encoding='utf-8')
            value = self.provider.read()
            self.assertIsNone(value['temperature_c'])
            self.assertEqual(value['error_reason'], 'broker_invalid')

    def test_absent_install_does_not_create_request_or_directories(self):
        other = self.folder / 'absent'
        provider = BrokerCpuProvider(other)
        self.assertFalse(provider.installed)
        provider.read()
        provider.close()
        self.assertFalse(other.exists())

    def test_disabled_monitor_releases_request(self):
        monitor = CpuTemperatureMonitor([self.provider], active=lambda: False)
        (self.folder / 'request.txt').write_text('request', encoding='ascii')
        monitor.sample()
        self.assertEqual(monitor.snapshot()['error_reason'], 'monitoring_paused')
        self.assertEqual((self.folder / 'request.txt').read_bytes(), b'')

    def test_privileged_worker_has_fixed_paths_lifetime_and_no_network(self):
        source = (ROOT / 'tools/cpu_sensor_helper/Program.cs').read_text(encoding='utf-8')
        broker = source.split('static int RunBroker()', 1)[1].split('static int Main', 1)[0]
        for evidence in ['IsSystem', 'SpecialFolder.ProgramFiles', 'SpecialFolder.CommonApplicationData',
                         'TotalSeconds < 55', 'age > 20', 'ReadBlock', 'PlainPath(lease)']:
            self.assertIn(evidence, broker)
        for forbidden in ['Process.Start', 'TcpListener', 'HttpListener', 'args[', 'Assembly.Load']:
            self.assertNotIn(forbidden, broker)
        script = (ROOT / 'scripts/Manage-CpuSensorTask.ps1').read_text(encoding='utf-8-sig')
        for evidence in ['Read,Write', 'ExecutionTimeLimit', 'IgnoreNew', 'Get-FileHash',
                         'AccessControlType', 'ReparsePoint', 'Description -ne $marker',
                         "[Environment]::GetFolderPath('System')", 'Import-Module -Name $taskModule']:
            self.assertIn(evidence, script)

    def test_user_host_and_broker_are_explicit_separate_install_choices(self):
        source = (ROOT / 'scripts/Install-MeowKeeperPlus.ps1').read_text(encoding='utf-8-sig')
        self.assertIn('[switch]$UserHost', source)
        self.assertIn("@('-UserHost','-HostUserSid',$HostUserSid)", source)
        self.assertEqual(source.count("if ($UserHost) { $coreXmlArgs += @('--user-id', $interactiveUserSid) }"), 2)
        self.assertIn('受限 SYSTEM 測溫工作', source)
        self.assertIn('Manage-CpuSensorTask.ps1', (ROOT / 'scripts/Uninstall-MeowKeeperPlus.ps1').read_text(encoding='utf-8-sig'))

    @unittest.skipUnless(os.name == 'nt', '需要 Windows PowerShell')
    def test_windows_parser_and_unprivileged_binary_refuses_broker(self):
        executable = str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
        script = str(ROOT / 'scripts/Manage-CpuSensorTask.ps1').replace("'", "''")
        command = "$e=$null;$t=$null;[void][System.Management.Automation.Language.Parser]::ParseFile('" + script + "',[ref]$t,[ref]$e);if($e.Count){exit 1}"
        result = subprocess.run([executable, '-NoProfile', '-NonInteractive', '-Command', command],
                                capture_output=True, shell=False, timeout=30, check=False)
        self.assertEqual(result.returncode, 0)
        helper = ROOT / 'vendor/cpu-sensor/MeowKeeperCpuSensor.exe'
        self.assertTrue(helper.is_file())
        result = subprocess.run([str(helper), '--broker'], capture_output=True, timeout=10, shell=False, check=False)
        self.assertEqual(result.returncode, 5)

    def test_pending_broker_does_not_fall_through_to_unprivileged_helper(self):
        from meowkeeper.core.analytics.cpu_temperature import InstalledCpuProvider
        provider = InstalledCpuProvider(self.folder)
        provider.broker = self.provider
        provider.helper = Mock()
        self.assertEqual(provider.read()['error_reason'], 'broker_starting')
        provider.helper.read.assert_not_called()
