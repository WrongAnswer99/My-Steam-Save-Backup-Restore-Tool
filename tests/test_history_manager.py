import tempfile
import unittest
import zipfile
import json
from pathlib import Path

from src.history_manager import (
    clear_history_point,
    list_history,
    import_history_package,
    package_history,
    package_history_points,
    restore_game_history_point,
)


class HistoryManagerTests(unittest.TestCase):
    def make_history(self, root: Path) -> Path:
        backup_root = root / "backup_result"
        game = backup_root / "1_Sample Game"
        (game / "local" / "001_save").mkdir(parents=True)
        (game / "local" / "001_save" / "current.txt").write_text("current local", encoding="utf-8")
        (game / "123").mkdir()
        (game / "123" / "current.txt").write_text("current remote", encoding="utf-8")

        (game / "local-2026-01-01-10-00-00" / "001_save").mkdir(parents=True)
        (game / "local-2026-01-01-10-00-00" / "001_save" / "old.txt").write_text("old local", encoding="utf-8")
        (game / "123-2026-01-01-10-00-00").mkdir()
        (game / "123-2026-01-01-10-00-00" / "old.txt").write_text("old remote", encoding="utf-8")
        (game / "local-2026-01-02-10-00-00" / "001_save").mkdir(parents=True)
        (game / "local-2026-01-02-10-00-00" / "001_save" / "mid.txt").write_text("mid local", encoding="utf-8")

        local_source = root / "original-local"
        remote_source = root / "original-remote"

        (game / "hashes.txt").write_text(
            "\n".join([
                "Steam Saves Backup hashes v2",
                "generated_at\t2026-01-03T10:00:00+08:00",
                "app_id\t1",
                "game\tSample Game",
                "algorithm\tSHA-256",
                "",
                "scope\tkind\tsha256\tfiles\tbytes\tbacked_up_at\tsource\tdestination",
                f"local\tdirectory\tdigest1\t1\t10\t2026-01-03T10:00:00+08:00\t{local_source}\tlocal/001_save",
                f"remote:123\tdirectory\tdigest2\t1\t11\t2026-01-03T10:00:00+08:00\t{remote_source}\t123",
                "",
            ]),
            encoding="utf-8",
        )
        return backup_root

    def test_lists_current_and_archived_time_points(self):
        with tempfile.TemporaryDirectory() as temp:
            backup_root = self.make_history(Path(temp))
            points = list_history(backup_root)
            self.assertEqual(
                [
                    "2026-01-03-10-00-00",
                    "2026-01-02-10-00-00",
                    "2026-01-01-10-00-00",
                ],
                [point.timestamp_key for point in points],
            )
            self.assertEqual([2, 1, 2], [len(point.snapshots) for point in points])

    def test_updated_only_zip_uses_scope_name_without_timestamp(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            backup_root = self.make_history(root)
            result = package_history(
                backup_root,
                "2026-01-02-10-00-00",
                root / "updated.zip",
                complete_state=False,
            )
            with zipfile.ZipFile(result.output) as archive:
                names = set(archive.namelist())
                manifest = json.loads(archive.read("steam-saves-backup.json"))
                names.remove("steam-saves-backup.json")
                self.assertEqual({"1_Sample Game/local/001_save/mid.txt"}, names)
                self.assertNotIn("local-2026-01-02-10-00-00", "\n".join(names))
                self.assertEqual(["2026-01-02-10-00-00"], manifest["time_points"])
                self.assertIn("exported_at", manifest)
            self.assertEqual(1, result.snapshot_count)

    def test_complete_state_uses_latest_scope_at_or_before_time(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            backup_root = self.make_history(root)
            result = package_history(
                backup_root,
                "2026-01-02-10-00-00",
                root / "state.zip",
                complete_state=True,
            )
            with zipfile.ZipFile(result.output) as archive:
                names = set(archive.namelist())
                names.remove("steam-saves-backup.json")
                self.assertEqual(
                    {
                        "1_Sample Game/local/001_save/mid.txt",
                        "1_Sample Game/123/old.txt",
                    },
                    names,
                )
                self.assertEqual("mid local", archive.read("1_Sample Game/local/001_save/mid.txt").decode())
                self.assertEqual("old remote", archive.read("1_Sample Game/123/old.txt").decode())
            self.assertEqual(2, result.snapshot_count)

    def test_selected_points_zip_groups_each_point_without_scope_suffixes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            backup_root = self.make_history(root)
            result = package_history_points(
                backup_root,
                ["2026-01-01-10-00-00", "2026-01-02-10-00-00"],
                root / "selected.zip",
                complete_state=False,
            )
            with zipfile.ZipFile(result.output) as archive:
                names = set(archive.namelist())
                names.remove("steam-saves-backup.json")
                self.assertEqual(
                    {
                        "2026-01-01-10-00-00/1_Sample Game/local/001_save/old.txt",
                        "2026-01-01-10-00-00/1_Sample Game/123/old.txt",
                        "2026-01-02-10-00-00/1_Sample Game/local/001_save/mid.txt",
                    },
                    names,
                )
                self.assertNotIn("local-2026", "\n".join(names))
            self.assertEqual(3, result.snapshot_count)

    def test_package_can_be_imported_and_restored_to_original_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_root = root / "source"
            backup_root = self.make_history(source_root)
            package = package_history(
                backup_root,
                "2026-01-02-10-00-00",
                root / "portable.zip",
                complete_state=True,
            )
            imported_root = root / "imported"
            imported = import_history_package(imported_root, package.output)
            self.assertEqual(2, len(imported.imported))
            game_directory = imported_root / "1_Sample Game"
            self.assertTrue((game_directory / "local-2026-01-02-10-00-00").is_dir())
            self.assertTrue((game_directory / "123-2026-01-02-10-00-00").is_dir())

            restored = restore_game_history_point(
                imported_root,
                game_directory,
                "2026-01-02-10-00-00",
            )
            self.assertEqual(2, restored.restored_sources)
            self.assertEqual(
                "mid local",
                (source_root / "original-local" / "mid.txt").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                "old remote",
                (source_root / "original-remote" / "old.txt").read_text(encoding="utf-8"),
            )

    def test_clear_removes_archives_and_untracks_deleted_current_scopes(self):
        with tempfile.TemporaryDirectory() as temp:
            backup_root = self.make_history(Path(temp))
            removed = clear_history_point(backup_root, "2026-01-02-10-00-00")
            self.assertEqual(1, len(removed.removed))
            self.assertFalse((backup_root / "1_Sample Game" / "local-2026-01-02-10-00-00").exists())
            self.assertTrue((backup_root / "1_Sample Game" / "local").is_dir())

            current = clear_history_point(backup_root, "2026-01-03-10-00-00")
            self.assertEqual(2, len(current.removed))
            self.assertFalse((backup_root / "1_Sample Game" / "local").exists())
            self.assertFalse((backup_root / "1_Sample Game" / "123").exists())
            self.assertFalse((backup_root / "1_Sample Game" / "hashes.txt").exists())

    def test_clear_current_time_preserves_other_current_scope_records(self):
        with tempfile.TemporaryDirectory() as temp:
            backup_root = self.make_history(Path(temp))
            hashes = backup_root / "1_Sample Game" / "hashes.txt"
            content = hashes.read_text(encoding="utf-8")
            content = content.replace(
                "remote:123\tdirectory\tdigest2\t1\t11\t2026-01-03T10:00:00+08:00",
                "remote:123\tdirectory\tdigest2\t1\t11\t2026-01-04T10:00:00+08:00",
            )
            hashes.write_text(content, encoding="utf-8")

            result = clear_history_point(backup_root, "2026-01-03-10-00-00")
            self.assertEqual(1, len(result.removed))
            self.assertFalse((backup_root / "1_Sample Game" / "local").exists())
            self.assertTrue((backup_root / "1_Sample Game" / "123").is_dir())
            remaining = hashes.read_text(encoding="utf-8")
            self.assertNotIn("local\tdirectory", remaining)
            self.assertIn("remote:123\tdirectory", remaining)


if __name__ == "__main__":
    unittest.main()
