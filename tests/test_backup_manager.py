import tempfile
import unittest
import json
from pathlib import Path
from unittest import mock

from src.backup_manager import (
    backup_report,
    detect_game_change,
    detect_report_changes,
    hash_tree,
    sanitize_component,
)
from src.history_manager import restore_game_history_point


class BackupManagerTests(unittest.TestCase):
    def test_hash_tree_changes_with_file_content(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "save.dat").write_text("first", encoding="utf-8")
            before = hash_tree(root)
            (root / "save.dat").write_text("second", encoding="utf-8")
            after = hash_tree(root)
        self.assertNotEqual(before.digest, after.digest)

    def test_backup_layout_hashes_and_versions_changed_scope(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            local = root / "LocalSave"
            local.mkdir()
            (local / "slot1.sav").write_text("save data", encoding="utf-8")
            userdata = root / "Steam" / "userdata" / "123456" / "42"
            (userdata / "remote").mkdir(parents=True)
            (userdata / "remote" / "cloud.sav").write_text("cloud data", encoding="utf-8")
            output = root / "backup_result"
            report = {
                "games": [{
                    "app_id": 42,
                    "name": "Example: Game",
                    "local": {"directories": [str(local)], "files": [], "registry_keys": []},
                    "steam_userdata": {
                        "app_directories": [str(userdata)],
                        "remote_directories": [str(userdata / "remote")],
                        "manifest_directories": [],
                        "files": [],
                    },
                }]
            }

            before_backup = detect_game_change(report["games"][0], output)
            self.assertTrue(before_backup["changed"])
            self.assertIsNone(before_backup["last_backup_at"])

            with mock.patch(
                "src.backup_manager._now_iso",
                return_value="2026-01-01T10:00:00+08:00",
            ):
                first = backup_report(report, output)
            game_root = output / "42_Example_ Game"
            self.assertEqual(1, len(first.completed))
            self.assertTrue((game_root / "local" / "001_LocalSave" / "slot1.sav").is_file())
            self.assertTrue((game_root / "123456" / "remote" / "cloud.sav").is_file())
            hashes = (game_root / "hashes.txt").read_text(encoding="utf-8")
            self.assertIn("SHA-256", hashes)
            self.assertIn("remote:123456", hashes)
            clean = detect_game_change(report["games"][0], output)
            self.assertFalse(clean["changed"])
            self.assertIsNotNone(clean["last_backup_at"])

            (local / "slot1.sav").write_text("new save data", encoding="utf-8")
            changed = detect_game_change(report["games"][0], output)
            self.assertTrue(changed["changed"])
            progress_events = []
            detected = detect_report_changes(
                report,
                output,
                progress=lambda current, total, message: progress_events.append((current, total, message)),
            )
            self.assertTrue(detected.completed[0]["changed"])
            self.assertTrue(any(str(local) in event[2] for event in progress_events))
            self.assertTrue(any("发现差异" in event[2] for event in progress_events))
            with mock.patch(
                "src.backup_manager._now_iso",
                return_value="2026-01-02T10:00:00+08:00",
            ):
                second = backup_report(report, output)
            self.assertEqual(1, len(second.completed))
            self.assertEqual("new save data", (game_root / "local" / "001_LocalSave" / "slot1.sav").read_text())
            versions = list(game_root.glob("local-20*"))
            self.assertEqual(1, len(versions))
            self.assertEqual("save data", (versions[0] / "001_LocalSave" / "slot1.sav").read_text())
            self.assertEqual([], list(game_root.glob("123456-20*")))
            history_index = json.loads((game_root / "history.json").read_text(encoding="utf-8"))
            archived_entry = history_index["archives"][versions[0].name]
            self.assertEqual("local", archived_entry["scope"])
            self.assertEqual(str(local), archived_entry["records"][0]["source"])

            with mock.patch(
                "src.backup_manager._copy_and_verify",
                side_effect=AssertionError("unchanged data must not be copied"),
            ):
                third = backup_report(report, output)
            self.assertEqual([], third.completed)
            self.assertEqual(1, len(third.unchanged))
            self.assertEqual(1, len(list(game_root.glob("local-20*"))))

            (local / "slot1.sav").write_text("after backup", encoding="utf-8")
            timestamp_key = versions[0].name.removeprefix("local-")
            restored = restore_game_history_point(output, game_root, timestamp_key)
            self.assertGreaterEqual(restored.restored_sources, 1)
            self.assertEqual("save data", (local / "slot1.sav").read_text(encoding="utf-8"))

    def test_game_without_existing_sources_is_skipped(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = {
                "games": [{
                    "app_id": 99,
                    "name": "Not Played",
                    "local": {"directories": [str(root / "missing")], "files": []},
                    "steam_userdata": {"app_directories": [], "remote_directories": [], "files": []},
                }]
            }
            result = backup_report(report, root / "backup_result")
            self.assertEqual([], result.completed)
            self.assertEqual(1, len(result.skipped))
            self.assertFalse((root / "backup_result" / "99_Not Played").exists())

    def test_one_report_uses_one_batch_timestamp_for_every_game(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            games = []
            for app_id in (10, 20):
                source = root / f"save-{app_id}"
                source.mkdir()
                (source / "slot.sav").write_text(str(app_id), encoding="utf-8")
                games.append({
                    "app_id": app_id,
                    "name": f"Game {app_id}",
                    "local": {"directories": [str(source)], "files": [], "registry_keys": []},
                    "steam_userdata": {"app_directories": [], "remote_directories": [], "files": []},
                })

            fixed_time = "2026-09-25T14:03:27+08:00"
            output = root / "backup_result"
            with mock.patch("src.backup_manager._now_iso", return_value=fixed_time) as clock:
                result = backup_report({"games": games}, output)

            self.assertEqual(2, len(result.completed))
            self.assertEqual(1, clock.call_count)
            for app_id in (10, 20):
                hashes = (output / f"{app_id}_Game {app_id}" / "hashes.txt").read_text(encoding="utf-8")
                self.assertIn(f"generated_at\t{fixed_time}", hashes)
                self.assertIn(f"\t{fixed_time}\t", hashes)

    def test_sanitize_windows_filename(self):
        self.assertEqual("Example_ Game", sanitize_component("Example: Game"))
        self.assertEqual("_CON", sanitize_component("CON"))


if __name__ == "__main__":
    unittest.main()
