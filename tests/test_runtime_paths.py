from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from meowkeeper.runtime_paths import (
    active_data_root_from_status,
    default_dashboard_config_path,
    default_data_root,
    default_install_root,
)


class RuntimeDataPathTests(unittest.TestCase):
    def test_new_system_install_defaults_to_external_data_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install_root = Path(temporary) / "MeowKeeperPlus"
            app = install_root / "App"
            app.mkdir(parents=True)
            (app / "build_manifest.json").write_text("{}", encoding="utf-8")

            self.assertEqual(default_data_root(app), install_root / "Data")
            self.assertEqual(default_install_root(app), install_root)
            self.assertEqual(default_dashboard_config_path(app), install_root / "Data" / "config.json")

    def test_existing_legacy_app_data_is_kept_until_migration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install_root = Path(temporary) / "MeowKeeperPlus"
            app = install_root / "App"
            (app / "data" / "pets" / "highball").mkdir(parents=True)
            (app / "build_manifest.json").write_text("{}", encoding="utf-8")
            (install_root / "Data").mkdir()
            (app / "data" / "pets" / "highball" / "profile.json").write_text("{}", encoding="utf-8")

            self.assertEqual(default_data_root(app), app / "data")

    def test_populated_canonical_data_takes_precedence_over_legacy_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install_root = Path(temporary) / "MeowKeeperPlus"
            app = install_root / "App"
            (app / "data").mkdir(parents=True)
            (install_root / "Data").mkdir(parents=True)
            (app / "build_manifest.json").write_text("{}", encoding="utf-8")
            (install_root / "Data" / "config.json").write_text("{}", encoding="utf-8")
            (app / "data" / "old.json").write_text("{}", encoding="utf-8")

            self.assertEqual(default_data_root(app), install_root / "Data")

    def test_installer_receipts_and_cache_do_not_hide_legacy_user_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install_root = Path(temporary) / "MeowKeeperPlus"
            app = install_root / "App"
            legacy = app / "data"
            canonical = install_root / "Data"
            (legacy / "pets" / "highball").mkdir(parents=True)
            (legacy / "pets" / "highball" / "profile.json").write_text("{}", encoding="utf-8")
            (canonical / "install-history").mkdir(parents=True)
            (canonical / "install-history" / "receipt.json").write_text("{}", encoding="utf-8")
            (canonical / "cache").mkdir()
            (canonical / "cache" / "temporary.bin").write_bytes(b"cache")
            (app / "build_manifest.json").write_text("{}", encoding="utf-8")

            self.assertEqual(default_data_root(app), legacy)

    def test_portable_keeps_package_local_data_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "MeowKeeperPlus-portable-r107"
            root.mkdir()
            (root / "build_manifest.json").write_text("{}", encoding="utf-8")
            self.assertEqual(default_data_root(root), root / "data")

    def test_tray_follows_live_host_legacy_root_until_verified_migration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install_root = Path(temporary) / "MeowKeeperPlus"
            app = install_root / "App"
            app.mkdir(parents=True)
            (app / "build_manifest.json").write_text("{}", encoding="utf-8")
            (install_root / "Data" / "pets" / "highball").mkdir(parents=True)
            (app / "data" / "pets" / "highball").mkdir(parents=True)

            status = {"metrics": {"disk": {"path": str(app / "data" / "events")}}}
            self.assertEqual(active_data_root_from_status(app, status), app / "data")

    def test_tray_rejects_unexpected_host_reported_data_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary) / "MeowKeeperPlus" / "App"
            app.mkdir(parents=True)
            (app / "build_manifest.json").write_text("{}", encoding="utf-8")
            status = {"metrics": {"disk": {"path": str(Path(temporary) / "outside" / "events")}}}

            self.assertIsNone(active_data_root_from_status(app, status))


if __name__ == "__main__":
    unittest.main()
