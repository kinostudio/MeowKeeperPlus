import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.build_portable import _launcher_text
from tools.portable_start import dashboard_port, main


class PortableStartTests(unittest.TestCase):
    def test_one_entry_uses_embedded_runtime_and_tray(self):
        self.assertIn("portable_start.py", _launcher_text())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime/python").mkdir(parents=True)
            (root / "runtime/python/pythonw.exe").touch()
            (root / "config.json").write_text(json.dumps({"dashboard": {"port": 18911}}), encoding="utf-8")
            with patch("tools.portable_start.__file__", str(root / "tools/portable_start.py")), \
                    patch("tools.portable_start.subprocess.Popen") as launch:
                self.assertEqual(main([]), 0)
            command = launch.call_args.args[0]
            self.assertEqual(command[0], str(root / "runtime/python/pythonw.exe"))
            self.assertIn("--open-dashboard", command)
            self.assertIn("http://127.0.0.1:18911", command)
            self.assertFalse(launch.call_args.kwargs["shell"])

    def test_invalid_port_never_launches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text('{"dashboard":{"port":true}}', encoding="utf-8")
            with self.assertRaises(ValueError):
                dashboard_port(root)

    def test_installed_entry_uses_legacy_data_config_until_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            install_root = Path(directory) / "MeowKeeperPlus"
            app = install_root / "App"
            (app / "build_manifest.json").parent.mkdir(parents=True)
            (app / "build_manifest.json").write_text("{}", encoding="utf-8")
            (install_root / "Data" / "install-history").mkdir(parents=True)
            (app / "data").mkdir()
            (app / "data" / "config.json").write_text(
                json.dumps({"dashboard": {"port": 18912}}), encoding="utf-8"
            )

            self.assertEqual(dashboard_port(app), 18912)
