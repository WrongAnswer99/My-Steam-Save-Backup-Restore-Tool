import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLayout, QSizePolicy

from src.gui import (
    STYLE_SHEET,
    ChangeProgressDialog,
    GameCard,
    HistoryDialog,
    SteamBackupWindow,
    is_search_subsequence,
    normalized_search_text,
)
from src.backup_manager import backup_report, game_backup_directory
from src.history_manager import HistoryPoint


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")
        cls.app.setStyleSheet(STYLE_SHEET)
        cls.app.setFont(QFont("Microsoft YaHei UI", 9))

    @staticmethod
    def make_card() -> GameCard:
        return GameCard({
            "app_id": 1978000,
            "name": "Neutral Sample Title",
            "local": {
                "found": False,
                "directories": [],
                "files": [],
                "registry_keys": [],
            },
            "steam_userdata": {
                "found": False,
                "app_directories": [],
                "remote_directories": [],
                "manifest_directories": [],
                "files": [],
            },
        })

    @staticmethod
    def game(app_id: int, name: str) -> dict[str, object]:
        return {
            "app_id": app_id,
            "name": name,
            "local": {"found": False, "directories": [], "files": [], "registry_keys": []},
            "steam_userdata": {
                "found": False,
                "app_directories": [],
                "remote_directories": [],
                "manifest_directories": [],
                "files": [],
            },
        }

    def test_card_body_toggles_but_controls_do_not(self):
        card = self.make_card()
        card.resize(1000, 80)
        card.show()
        self.app.processEvents()

        self.assertTrue(card.details.isHidden())
        QTest.mouseClick(
            card,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(400, 20),
        )
        self.assertFalse(card.details.isHidden())
        QTest.mouseClick(
            card,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(400, 20),
        )
        self.assertTrue(card.details.isHidden())

        QTest.mouseClick(card.checkbox, Qt.MouseButton.LeftButton)
        self.assertTrue(card.checkbox.isChecked())
        self.assertTrue(card.details.isHidden())

        QTest.mouseClick(card.expand_button, Qt.MouseButton.LeftButton)
        self.assertFalse(card.details.isHidden())
        self.assertTrue(any(
            button.text() == "查看历史版本"
            for button in card.findChildren(type(card.backup_button))
        ))
        card.close()

    def test_search_normalization_and_id_substring(self):
        card = self.make_card()
        self.assertEqual("mixedname", normalized_search_text("  Mi XeD Name "))
        self.assertTrue(card.matches_search("neutral sample"))
        self.assertTrue(card.matches_search("NEUTRALSAMPLETITLE"))
        self.assertTrue(card.matches_search("19"))
        self.assertFalse(card.matches_search("999"))
        card.set_change_state("changed", "2026-09-24T12:37:55+08:00")
        self.assertEqual("有修改", card.change_badge.text())
        self.assertIn("2026-09-24 12:37", card.last_backup_label.text())

    def test_backup_directory_and_time_are_clickable_after_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            (source / "save.dat").write_text("save", encoding="utf-8")
            game = self.game(42, "Backup Link")
            game["local"] = {
                "found": True,
                "directories": [str(source)],
                "files": [],
                "registry_keys": [],
            }
            backup_root = root / "backup_result"
            backup_report({"games": [game]}, backup_root)
            expected = str(game_backup_directory(game, backup_root).resolve())

            with patch("src.gui.BACKUP_ROOT", backup_root):
                card = GameCard(game)

            self.assertEqual(expected, card.backup_path_link.text())
            self.assertEqual(expected, card.backup_path_link.path)
            self.assertEqual(expected, card.last_backup_label.path)
            self.assertNotIn("无", card.last_backup_label.text())
        card.close()

    def test_search_matches_non_contiguous_subsequence_and_ignores_punctuation(self):
        game = self.game(322330, "Don't Starve Together")
        card = GameCard(game)
        self.assertEqual("dontstarvetogether", normalized_search_text("Don't Starve Together"))
        self.assertTrue(card.matches_search("dont"))
        self.assertTrue(card.matches_search("st"))
        self.assertTrue(card.matches_search("dst"))
        self.assertTrue(card.matches_search("233"))
        self.assertFalse(card.matches_search("dtsz"))
        self.assertTrue(is_search_subsequence("dst", "dontstarvetogether"))
        card.close()

    def test_toolbar_scope_styles_and_history_button(self):
        window = SteamBackupWindow()
        window.set_report({"games": [self.game(1, "One")]})
        self.assertFalse(hasattr(window, "selected_scan_button"))
        self.assertEqual("检测所有游戏", window.scan_button.text())
        self.assertEqual("检测选中更改", window.selected_changes_button.text())
        self.assertEqual("检测所有更改", window.changes_button.text())
        self.assertEqual("查看历史版本", window.history_button.text())
        self.assertTrue(window.history_button.isEnabled())
        self.assertEqual("PrimaryButton", window.scan_button.objectName())
        self.assertEqual("PrimaryButton", window.changes_button.objectName())
        self.assertEqual("PrimaryButton", window.all_backup_button.objectName())
        self.assertNotEqual("PrimaryButton", window.selected_changes_button.objectName())
        self.assertNotEqual("PrimaryButton", window.selected_backup_button.objectName())
        window.close()

    def test_global_history_supports_select_all_and_bulk_actions(self):
        points = [
            HistoryPoint(datetime.fromisoformat("2026-01-02T10:00:00+08:00"), "2026-01-02-10-00-00", ()),
            HistoryPoint(datetime.fromisoformat("2026-01-01T10:00:00+08:00"), "2026-01-01-10-00-00", ()),
        ]
        with tempfile.TemporaryDirectory() as temp, patch("src.gui.list_history", return_value=points):
            dialog = HistoryDialog(Path(temp))
            self.assertFalse(dialog.delete_selected_button.isEnabled())
            self.assertFalse(dialog.package_selected_button.isEnabled())
            dialog.select_all.setChecked(True)
            self.app.processEvents()
            self.assertTrue(all(box.isChecked() for box in dialog.row_checkboxes))
            self.assertTrue(dialog.delete_selected_button.isEnabled())
            self.assertTrue(dialog.package_selected_button.isEnabled())
            dialog.close()

    def test_pinned_game_stays_first_but_still_obeys_filters_and_persists(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Path(temp) / "ui_settings.json"
            window = SteamBackupWindow(pinned_games_path=settings)
            window.set_report({"games": [self.game(1, "Zeta"), self.game(2, "Alpha")]})
            self.assertEqual(["Alpha", "Zeta"], [card.game_name for card in window.cards])

            zeta = next(card for card in window.cards if card.game_name == "Zeta")
            zeta.pin_button.click()
            self.app.processEvents()
            self.assertEqual(["Zeta", "Alpha"], [card.game_name for card in window.cards])
            self.assertTrue(settings.is_file())

            window.search_filter.setText("alpha")
            self.app.processEvents()
            self.assertEqual(["Alpha"], [card.game_name for card in window.visible_cards()])
            window.close()

            restored = SteamBackupWindow(pinned_games_path=settings)
            restored.set_report({"games": [self.game(2, "Alpha"), self.game(1, "Zeta")]})
            self.assertEqual("Zeta", restored.cards[0].game_name)
            self.assertTrue(restored.cards[0].pin_button.isChecked())
            restored.close()

    def test_game_history_has_clear_restore_and_no_package_actions(self):
        point = HistoryPoint(
            datetime.fromisoformat("2026-01-02T10:00:00+08:00"),
            "2026-01-02-10-00-00",
            (),
        )
        with tempfile.TemporaryDirectory() as temp, patch("src.gui.list_game_history", return_value=[point]):
            root = Path(temp)
            dialog = HistoryDialog(root, game_directory=root / "1_Game", game_name="Game")
            visible_texts = {
                button.text() for button in dialog.findChildren(type(dialog.delete_selected_button))
                if not button.isHidden()
            }
            self.assertIn("清除", visible_texts)
            self.assertIn("恢复", visible_texts)
            self.assertIn("删除选中", visible_texts)
            self.assertNotIn("打包", visible_texts)
            self.assertTrue(dialog.package_selected_button.isHidden())
            self.assertTrue(dialog.import_button.isHidden())
            dialog.close()

    def test_long_path_does_not_expand_card_width(self):
        game = self.game(10, "Long Path")
        game["local"] = {
            "found": True,
            "directories": ["C:\\" + "very-long-folder-name\\" * 30],
            "files": [],
            "registry_keys": [],
        }
        window = SteamBackupWindow()
        games = [game, *[self.game(11 + index, f"Other Game {index}") for index in range(6)]]
        window.set_report({"games": games})
        window.resize(1120, 780)
        window.show()
        self.app.processEvents()
        window.scroll.sync_content_width()
        self.app.processEvents()
        before = [card.width() for card in window.cards]
        collapsed_height = window.cards[0].height()
        before_other_heights = [card.height() for card in window.cards[1:]]
        window.cards[0].expand_button.click()
        self.app.processEvents()
        window.scroll.sync_content_width()
        self.app.processEvents()
        after = [card.width() for card in window.cards]
        expanded_height = window.cards[0].height()
        self.assertEqual(before, after)
        self.assertEqual(before_other_heights, [card.height() for card in window.cards[1:]])
        self.assertGreater(expanded_height, collapsed_height)

        window.cards[0].expand_button.click()
        self.app.processEvents()
        self.assertEqual(collapsed_height, window.cards[0].height())
        self.assertEqual(before_other_heights, [card.height() for card in window.cards[1:]])

        window.cards[0].expand_button.click()
        self.app.processEvents()
        self.assertEqual(expanded_height, window.cards[0].height())
        self.assertEqual(before_other_heights, [card.height() for card in window.cards[1:]])
        self.assertEqual(window.scroll.viewport().width(), window.cards_host.width())
        self.assertEqual(
            QSizePolicy.Policy.Ignored,
            window.cards[0].sizePolicy().horizontalPolicy(),
        )
        self.assertEqual(
            QLayout.SizeConstraint.SetNoConstraint,
            window.cards_layout.sizeConstraint(),
        )
        window.close()

    def test_change_progress_dialog_stays_until_confirmed(self):
        dialog = ChangeProgressDialog(2, None)
        dialog.update_progress(1, 2, "Sample：正在检测目录 C:\\Save")
        self.assertFalse(dialog.ok_button.isEnabled())
        self.assertIn("Sample", dialog.game_label.text())
        self.assertIn("C:\\Save", dialog.log.toPlainText())
        dialog.finish("检测完成：1 个有修改")
        self.assertTrue(dialog.ok_button.isEnabled())
        self.assertTrue(dialog.complete)
        dialog.close()

    def test_detection_sorts_and_selects_changed_games(self):
        window = SteamBackupWindow()
        window.set_report({"games": [
            self.game(1, "Beta"),
            self.game(2, "Alpha"),
            self.game(3, "Gamma"),
            self.game(4, "Delta"),
        ]})
        result = SimpleNamespace(
            completed=[
                {"app_id": 1, "changed": True, "last_backup_at": None},
                {"app_id": 2, "changed": True, "last_backup_at": None},
                {"app_id": 3, "changed": False, "last_backup_at": None},
            ],
            failed=[{"app_id": 4, "name": "Delta", "error": "test"}],
        )
        window.change_detection_finished(result)
        self.assertEqual(["Alpha", "Beta", "Delta", "Gamma"], [card.game_name for card in window.cards])
        self.assertEqual([True, True, False, False], [card.is_selected() for card in window.cards])
        window.close()


if __name__ == "__main__":
    unittest.main()
