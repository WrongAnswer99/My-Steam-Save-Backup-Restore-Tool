import tempfile
import unittest
from pathlib import Path

from src.steam_save_scanner import (
    PathRule,
    _normalized_game_name,
    iter_manifest_sections,
    parse_manifest_entry,
    parse_vdf,
)


class VdfTests(unittest.TestCase):
    def test_nested_keyvalues(self):
        parsed = parse_vdf('"AppState" { "appid" "42" "name" "Test" }')
        self.assertEqual("42", parsed["AppState"]["appid"])
        self.assertEqual("Test", parsed["AppState"]["name"])


class ManifestTests(unittest.TestCase):
    def test_extracts_steam_id_paths_and_conditions(self):
        content = '''---
"Example: Game":
  files:
    "<winAppData>/Example":
      tags:
        - save
      when:
        - os: windows
          store: steam
    "<home>/.config/example":
      when:
        - os: linux
  registry:
    HKEY_CURRENT_USER/SOFTWARE/Example:
      when:
        - os: windows
  steam:
    id: 42
Next Game:
  id:
    steamExtra:
      - 99
'''
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "manifest.yaml"
            path.write_text(content, encoding="utf-8")
            sections = list(iter_manifest_sections(path))

        first = parse_manifest_entry(*sections[0])
        second = parse_manifest_entry(*sections[1])
        self.assertEqual({42}, first.steam_ids)
        self.assertEqual({99}, second.steam_ids)
        self.assertEqual(2, len(first.files))
        self.assertTrue(first.files[0].applies_to_windows_steam())
        self.assertFalse(first.files[1].applies_to_windows_steam())
        self.assertEqual(1, len(first.registry))

    def test_condition_without_filters_applies(self):
        self.assertTrue(PathRule("x").applies_to_windows_steam())

    def test_normalized_name_matches_digit_and_word(self):
        self.assertEqual(
            _normalized_game_name("7 Sample Title"),
            _normalized_game_name("Seven Sample Title"),
        )

    def test_condition_with_unrelated_first_field_still_honors_os(self):
        content = '''Game:
  files:
    "<home>/linux-only":
      when:
        - bit: 64
          os: linux
  steam:
    id: 1
'''
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "manifest.yaml"
            path.write_text(content, encoding="utf-8")
            entry = parse_manifest_entry(*next(iter_manifest_sections(path)))
        self.assertFalse(entry.files[0].applies_to_windows_steam())



if __name__ == "__main__":
    unittest.main()
