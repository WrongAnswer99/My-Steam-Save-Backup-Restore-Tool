#!/usr/bin/env python3
"""Discover installed Steam apps and existing save/config paths on Windows.

The scanner reads Steam's own VDF files and the vendored Ludusavi manifest.
It never modifies game data. Only paths that currently exist are emitted.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

try:
    import winreg
except ImportError:  # pragma: no cover - non-Windows development host
    winreg = None


STEAM64_OFFSET = 76561197960265728
DEFAULT_MANIFEST = Path("third_party/ludusavi-manifest/data/manifest.yaml")
DEFAULT_OUTPUT = Path("scan_result.generated.json")


@dataclass(frozen=True)
class PathRule:
    template: str
    conditions: tuple[dict[str, tuple[str, ...]], ...] = ()

    def applies_to_windows_steam(self) -> bool:
        if not self.conditions:
            return True
        for condition in self.conditions:
            os_values = condition.get("os", ())
            store_values = condition.get("store", ())
            if os_values and "windows" not in os_values:
                continue
            if store_values and "steam" not in store_values:
                continue
            return True
        return False


@dataclass
class ManifestEntry:
    name: str
    steam_ids: set[int] = field(default_factory=set)
    files: list[PathRule] = field(default_factory=list)
    registry: list[PathRule] = field(default_factory=list)


@dataclass(frozen=True)
class InstalledApp:
    app_id: int
    name: str
    install_dir: Path
    library_root: Path
    manifest_path: Path


def _decode_vdf_string(value: str) -> str:
    return value.replace(r"\"", '"').replace(r"\\", "\\")


def parse_vdf(text: str) -> dict[str, object]:
    """Parse the KeyValues subset used by Steam library/app manifests."""
    tokens: list[str] = []
    token_re = re.compile(r'"((?:\\.|[^"\\])*)"|([{}])')
    for match in token_re.finditer(text):
        tokens.append(_decode_vdf_string(match.group(1)) if match.group(1) is not None else match.group(2))

    index = 0

    def parse_object(expect_close: bool = False) -> dict[str, object]:
        nonlocal index
        result: dict[str, object] = {}
        while index < len(tokens):
            token = tokens[index]
            if token == "}":
                if not expect_close:
                    raise ValueError("Unexpected closing brace in VDF")
                index += 1
                return result
            if token == "{":
                raise ValueError("Unexpected opening brace in VDF")
            key = token
            index += 1
            if index >= len(tokens):
                raise ValueError(f"Missing value for VDF key: {key}")
            if tokens[index] == "{":
                index += 1
                result[key] = parse_object(expect_close=True)
            else:
                result[key] = tokens[index]
                index += 1
        if expect_close:
            raise ValueError("Unclosed VDF object")
        return result

    return parse_object()


def _yaml_scalar(value: str) -> str:
    value = value.strip()
    if value.startswith('"') and value.endswith('"'):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value[1:-1].replace(r'\"', '"').replace(r"\\", "\\")
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")
    return value


def _yaml_values(value: str) -> tuple[str, ...]:
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        return tuple(_yaml_scalar(item).lower() for item in value[1:-1].split(",") if item.strip())
    return (_yaml_scalar(value).lower(),)


def _top_level_name(line: str) -> str | None:
    if not line or line[0].isspace() or not line.endswith(":") or line == "---":
        return None
    return _yaml_scalar(line[:-1])


def iter_manifest_sections(path: Path) -> Iterator[tuple[str, list[str]]]:
    current_name: str | None = None
    current_lines: list[str] = []
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\r\n")
            next_name = _top_level_name(line)
            if next_name is not None:
                if current_name is not None:
                    yield current_name, current_lines
                current_name = next_name
                current_lines = []
            elif current_name is not None:
                current_lines.append(line)
    if current_name is not None:
        yield current_name, current_lines


def _parse_conditions(block: list[str]) -> tuple[dict[str, tuple[str, ...]], ...]:
    conditions: list[dict[str, tuple[str, ...]]] = []
    in_when = False
    current: dict[str, tuple[str, ...]] | None = None
    for line in block:
        if re.match(r"^      when:\s*$", line):
            in_when = True
            continue
        if not in_when:
            continue
        if line and len(line) - len(line.lstrip(" ")) <= 6:
            break
        match = re.match(r"^        -\s+([A-Za-z][A-Za-z0-9_-]*):\s*(.+?)\s*$", line)
        if match:
            current = {}
            if match.group(1) in {"os", "store"}:
                current[match.group(1)] = _yaml_values(match.group(2))
            conditions.append(current)
            continue
        match = re.match(r"^          (os|store):\s*(.+?)\s*$", line)
        if match and current is not None:
            current[match.group(1)] = _yaml_values(match.group(2))
    return tuple(conditions)


def _parse_rules(lines: list[str], section_name: str) -> list[PathRule]:
    start = next((i for i, line in enumerate(lines) if line == f"  {section_name}:"), None)
    if start is None:
        return []
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r"^  \S", lines[i]):
            end = i
            break

    rules: list[PathRule] = []
    i = start + 1
    while i < end:
        match = re.match(r"^    (.+?):(?:\s*\{\})?\s*$", lines[i])
        if not match:
            i += 1
            continue
        template = _yaml_scalar(match.group(1))
        block_end = i + 1
        while block_end < end and not re.match(r"^    \S.*:(?:\s*\{\})?\s*$", lines[block_end]):
            block_end += 1
        rules.append(PathRule(template=template, conditions=_parse_conditions(lines[i + 1:block_end])))
        i = block_end
    return rules


def parse_manifest_entry(name: str, lines: list[str]) -> ManifestEntry:
    entry = ManifestEntry(name=name)
    section: str | None = None
    in_steam_extra = False
    for line in lines:
        section_match = re.match(r"^  ([A-Za-z][A-Za-z0-9_-]*):", line)
        if section_match:
            section = section_match.group(1)
            in_steam_extra = False
            continue
        if section == "steam":
            match = re.match(r"^    id:\s*(\d+)\s*$", line)
            if match:
                entry.steam_ids.add(int(match.group(1)))
        elif section == "id":
            if re.match(r"^    steamExtra:\s*$", line):
                in_steam_extra = True
                continue
            if re.match(r"^    \S", line):
                in_steam_extra = False
            if in_steam_extra:
                match = re.match(r"^      -\s*(\d+)\s*$", line)
                if match:
                    entry.steam_ids.add(int(match.group(1)))
    entry.files = _parse_rules(lines, "files")
    entry.registry = _parse_rules(lines, "registry")
    return entry


def load_manifest_by_steam_id(path: Path) -> tuple[dict[int, list[ManifestEntry]], int]:
    by_id: dict[int, list[ManifestEntry]] = {}
    entry_count = 0
    for name, lines in iter_manifest_sections(path):
        entry_count += 1
        entry = parse_manifest_entry(name, lines)
        for app_id in entry.steam_ids:
            by_id.setdefault(app_id, []).append(entry)
    return by_id, entry_count


def _registry_value(root: object, subkey: str, name: str) -> str | None:
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(root, subkey) as key:
            value, _ = winreg.QueryValueEx(key, name)
            return str(value)
    except OSError:
        return None


def discover_steam_root(explicit: Path | None = None) -> Path:
    if explicit is not None:
        root = explicit.expanduser().resolve()
        if not (root / "steamapps").is_dir():
            raise FileNotFoundError(f"Steam root has no steamapps directory: {root}")
        return root

    candidates: list[str] = []
    if winreg is not None:
        checks = [
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
        ]
        for root_key, subkey, value_name in checks:
            value = _registry_value(root_key, subkey, value_name)
            if value:
                candidates.append(value)
    candidates.extend([r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"])
    for candidate in candidates:
        path = Path(candidate)
        if (path / "steamapps").is_dir():
            return path.resolve()
    raise FileNotFoundError("Unable to locate Steam. Pass --steam-root explicitly.")


def discover_libraries(steam_root: Path) -> list[Path]:
    libraries = [steam_root]
    config = steam_root / "steamapps" / "libraryfolders.vdf"
    if config.is_file():
        parsed = parse_vdf(config.read_text(encoding="utf-8-sig", errors="replace"))
        folders = parsed.get("libraryfolders", {})
        if isinstance(folders, dict):
            for value in folders.values():
                if isinstance(value, dict) and isinstance(value.get("path"), str):
                    libraries.append(Path(value["path"]))
    unique: list[Path] = []
    seen: set[str] = set()
    for library in libraries:
        normalized = library.resolve()
        key = os.path.normcase(str(normalized))
        if key not in seen and (normalized / "steamapps").is_dir():
            seen.add(key)
            unique.append(normalized)
    return unique


def discover_installed_apps(libraries: Iterable[Path]) -> list[InstalledApp]:
    apps: dict[int, InstalledApp] = {}
    for library in libraries:
        steamapps = library / "steamapps"
        for manifest_path in sorted(steamapps.glob("appmanifest_*.acf")):
            try:
                parsed = parse_vdf(manifest_path.read_text(encoding="utf-8-sig", errors="replace"))
                state = parsed.get("AppState", {})
                if not isinstance(state, dict):
                    continue
                app_id = int(str(state["appid"]))
                name = str(state.get("name", f"App {app_id}"))
                install_name = str(state.get("installdir", ""))
                install_dir = (steamapps / "common" / install_name).resolve()
                apps[app_id] = InstalledApp(app_id, name, install_dir, library, manifest_path.resolve())
            except (KeyError, ValueError, OSError):
                continue
    return sorted(apps.values(), key=lambda app: (app.name.casefold(), app.app_id))


def steam_user_ids(steam_root: Path) -> list[str]:
    result: set[str] = set()
    userdata = steam_root / "userdata"
    if userdata.is_dir():
        for child in userdata.iterdir():
            if child.is_dir() and child.name.isdigit():
                account_id = int(child.name)
                result.add(str(account_id))
                result.add(str(account_id + STEAM64_OFFSET))
    return sorted(result, key=lambda value: int(value))


def _known_folders() -> dict[str, str]:
    home = Path.home().resolve()
    appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")).resolve()
    local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local")).resolve()
    documents = Path(os.environ.get("USERPROFILE", home)) / "Documents"
    return {
        "<home>": str(home),
        "<osUserName>": os.environ.get("USERNAME", home.name),
        "<winAppData>": str(appdata),
        "<winLocalAppData>": str(local),
        "<winDocuments>": str(documents.resolve()),
        "<winProgramData>": str(Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")).resolve()),
        "<winPublic>": str(Path(os.environ.get("PUBLIC", r"C:\Users\Public")).resolve()),
        "<winDir>": str(Path(os.environ.get("WINDIR", r"C:\Windows")).resolve()),
    }


def expand_template(template: str, app: InstalledApp, steam_root: Path, user_ids: list[str]) -> list[str]:
    replacements = _known_folders()
    replacements["<base>"] = str(app.install_dir)
    replacements["<root>"] = str(steam_root)
    values = [template]
    for token, replacement in replacements.items():
        values = [value.replace(token, replacement) for value in values]
    if "<storeUserId>" in template:
        values = [value.replace("<storeUserId>", user_id) for value in values for user_id in user_ids]
    return [os.path.normpath(value.replace("/", os.sep)) for value in values]


def existing_paths(patterns: Iterable[str]) -> tuple[list[str], list[str]]:
    directories: set[str] = set()
    files: set[str] = set()
    for pattern in patterns:
        if glob.has_magic(pattern):
            matches = glob.glob(pattern, recursive=True)
        else:
            matches = [pattern] if os.path.exists(pattern) else []
        for match in matches:
            absolute = str(Path(match).resolve())
            if os.path.isdir(absolute):
                directories.add(absolute)
            elif os.path.isfile(absolute):
                files.add(absolute)
    return sorted(directories, key=str.casefold), sorted(files, key=str.casefold)


def _is_under(path: str, parent: Path) -> bool:
    try:
        Path(path).resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _existing_registry_keys(rules: Iterable[PathRule]) -> list[str]:
    if winreg is None:
        return []
    roots = {
        "HKEY_CURRENT_USER": winreg.HKEY_CURRENT_USER,
        "HKEY_LOCAL_MACHINE": winreg.HKEY_LOCAL_MACHINE,
        "HKEY_CLASSES_ROOT": winreg.HKEY_CLASSES_ROOT,
        "HKEY_USERS": winreg.HKEY_USERS,
    }
    found: set[str] = set()
    for rule in rules:
        if not rule.applies_to_windows_steam():
            continue
        normalized = rule.template.replace("\\", "/")
        root_name, separator, subkey = normalized.partition("/")
        if not separator or root_name not in roots or "<" in subkey:
            continue
        try:
            with winreg.OpenKey(roots[root_name], subkey.replace("/", "\\")):
                found.add(normalized)
        except OSError:
            pass
    return sorted(found, key=str.casefold)


def _normalized_game_name(value: str) -> str:
    number_words = {
        "0": "zero",
        "1": "one",
        "2": "two",
        "3": "three",
        "4": "four",
        "5": "five",
        "6": "six",
        "7": "seven",
        "8": "eight",
        "9": "nine",
    }
    value = value.casefold().replace("™", "").replace("®", "")
    value = re.sub(r"\d", lambda match: number_words[match.group(0)], value)
    return "".join(character for character in value if character.isalnum())


def _directory_contains_file(path: Path) -> bool:
    try:
        for _, _, files in os.walk(path):
            if files:
                return True
    except OSError:
        return False
    return False


def discover_named_user_directories(
    apps: Iterable[InstalledApp],
    manifest: dict[int, list[ManifestEntry]],
    max_depth: int = 3,
) -> dict[int, list[str]]:
    folders = _known_folders()
    roots = [
        Path(folders["<winAppData>"]),
        Path(folders["<winLocalAppData>"]),
        Path(folders["<home>"]) / "AppData" / "LocalLow",
        Path(folders["<winDocuments>"]),
        Path(folders["<home>"]) / "Saved Games",
    ]
    index: dict[str, list[Path]] = {}
    seen: set[str] = set()
    queue: list[tuple[Path, int]] = [(root, 0) for root in roots if root.is_dir()]
    while queue:
        parent, depth = queue.pop(0)
        try:
            children = [child for child in parent.iterdir() if child.is_dir()]
        except OSError:
            continue
        for child in children:
            key = os.path.normcase(str(child.resolve()))
            if key in seen:
                continue
            seen.add(key)
            normalized = _normalized_game_name(child.name)
            if normalized:
                index.setdefault(normalized, []).append(child.resolve())
            if depth + 1 < max_depth:
                try:
                    is_link = child.is_symlink() or child.is_junction()
                except OSError:
                    is_link = True
                if not is_link:
                    queue.append((child, depth + 1))

    matches: dict[int, list[str]] = {}
    for app in apps:
        aliases = {app.name, app.install_dir.name}
        aliases.update(entry.name for entry in manifest.get(app.app_id, []))
        candidate_paths: set[Path] = set()
        for alias in aliases:
            normalized = _normalized_game_name(alias)
            candidate_paths.update(index.get(normalized, []))
        existing = sorted(
            {str(path) for path in candidate_paths if _directory_contains_file(path)},
            key=str.casefold,
        )
        if existing:
            matches[app.app_id] = existing
    return matches


def scan_app(
    app: InstalledApp,
    entries: list[ManifestEntry],
    steam_root: Path,
    user_ids: list[str],
    heuristic_directories: list[str] | None = None,
) -> dict[str, object]:
    local_patterns: list[str] = []
    manifest_remote_patterns: list[str] = []
    registry_rules: list[PathRule] = []
    userdata_root = (steam_root / "userdata").resolve()

    for entry in entries:
        for rule in entry.registry:
            if not rule.applies_to_windows_steam():
                continue
            registry_rules.extend(
                PathRule(template=expanded)
                for expanded in expand_template(rule.template, app, steam_root, user_ids)
            )
        for rule in entry.files:
            if not rule.applies_to_windows_steam():
                continue
            for expanded in expand_template(rule.template, app, steam_root, user_ids):
                if _is_under(expanded, userdata_root):
                    manifest_remote_patterns.append(expanded)
                else:
                    local_patterns.append(expanded)

    local_dirs, local_files = existing_paths(local_patterns)
    heuristic_directories = heuristic_directories or []
    local_dirs = sorted(set(local_dirs) | set(heuristic_directories), key=str.casefold)
    manifest_remote_dirs, manifest_remote_files = existing_paths(manifest_remote_patterns)

    app_directories: set[str] = set()
    manifest_remote_directories: set[str] = set(manifest_remote_dirs)
    remote_directories: set[str] = set()
    remote_files: set[str] = set(manifest_remote_files)
    userdata = steam_root / "userdata"
    if userdata.is_dir():
        for account_dir in userdata.iterdir():
            app_dir = account_dir / str(app.app_id)
            if not app_dir.is_dir():
                continue
            app_directories.add(str(app_dir.resolve()))
            remote_dir = app_dir / "remote"
            if remote_dir.is_dir():
                remote_directories.add(str(remote_dir.resolve()))
            cache = app_dir / "remotecache.vdf"
            if cache.is_file():
                remote_files.add(str(cache.resolve()))

    remote_content_files: set[str] = set()
    for remote_directory in remote_directories:
        for root, _, names in os.walk(remote_directory):
            remote_content_files.update(str((Path(root) / name).resolve()) for name in names)

    registry_keys = _existing_registry_keys(registry_rules)
    local_found = bool(local_dirs or local_files or registry_keys)
    remote_found = bool(app_directories or remote_directories or manifest_remote_directories or remote_files)
    return {
        "app_id": app.app_id,
        "name": app.name,
        "install_directory": str(app.install_dir),
        "library_root": str(app.library_root),
        "manifest_entries": sorted({entry.name for entry in entries}, key=str.casefold),
        "local": {
            "found": local_found,
            "heuristic_directories": heuristic_directories,
            "directories": local_dirs,
            "files": local_files,
            "registry_keys": registry_keys,
        },
        "steam_userdata": {
            "found": remote_found,
            "remote_directory_found": bool(remote_directories),
            "remote_files_found": bool(remote_content_files),
            "remote_file_count": len(remote_content_files),
            "app_directories": sorted(app_directories, key=str.casefold),
            "remote_directories": sorted(remote_directories, key=str.casefold),
            "manifest_directories": sorted(manifest_remote_directories, key=str.casefold),
            "files": sorted(remote_files, key=str.casefold),
        },
    }


def refresh_report_indexes(report: dict[str, object]) -> dict[str, object]:
    """Rebuild summary and missing-data indexes from the report's games."""
    result = dict(report)
    games = list(result.get("games", []))
    result["summary"] = {
        "installed_apps": len(games),
        "matched_manifest": sum(bool(game.get("manifest_entries", [])) for game in games),
        "with_local_data": sum(bool(game.get("local", {}).get("found")) for game in games),
        "without_local_data": sum(not game.get("local", {}).get("found") for game in games),
        "with_steam_userdata": sum(bool(game.get("steam_userdata", {}).get("found")) for game in games),
        "without_steam_userdata": sum(not game.get("steam_userdata", {}).get("found") for game in games),
        "with_remote_directory": sum(
            bool(game.get("steam_userdata", {}).get("remote_directory_found")) for game in games
        ),
        "with_remote_files": sum(
            bool(game.get("steam_userdata", {}).get("remote_files_found")) for game in games
        ),
    }
    result["missing_local"] = [
        {"app_id": game["app_id"], "name": game["name"]}
        for game in games
        if not game.get("local", {}).get("found")
    ]
    result["missing_steam_userdata"] = [
        {"app_id": game["app_id"], "name": game["name"]}
        for game in games
        if not game.get("steam_userdata", {}).get("found")
    ]
    result["missing_remote_directory"] = [
        {"app_id": game["app_id"], "name": game["name"]}
        for game in games
        if not game.get("steam_userdata", {}).get("remote_directory_found")
    ]
    result["missing_remote_files"] = [
        {"app_id": game["app_id"], "name": game["name"]}
        for game in games
        if not game.get("steam_userdata", {}).get("remote_files_found")
    ]
    return result


def build_report(
    steam_root: Path,
    manifest_path: Path,
) -> dict[str, object]:
    libraries = discover_libraries(steam_root)
    apps = discover_installed_apps(libraries)
    manifest, manifest_entry_count = load_manifest_by_steam_id(manifest_path)
    user_ids = steam_user_ids(steam_root)
    named_user_directories = discover_named_user_directories(apps, manifest)
    games = [
        scan_app(app, manifest.get(app.app_id, []), steam_root, user_ids, named_user_directories.get(app.app_id))
        for app in apps
    ]
    return refresh_report_indexes({
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "steam_root": str(steam_root),
        "library_roots": [str(path) for path in libraries],
        "manifest": {
            "path": str(manifest_path.resolve()),
            "entries": manifest_entry_count,
        },
        "games": games,
    })


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan installed Steam apps and existing save/config paths.")
    parser.add_argument("--steam-root", type=Path, help="Steam installation root; auto-detected by default")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST, help="Ludusavi manifest.yaml path")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Generated JSON report path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        manifest_path = args.manifest.resolve()
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Ludusavi manifest not found: {manifest_path}")
        steam_root = discover_steam_root(args.steam_root)
        report = build_report(steam_root, manifest_path)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary = report["summary"]
        print(f"Steam root: {steam_root}")
        print(f"Installed apps: {summary['installed_apps']}")
        print(f"Manifest matched: {summary['matched_manifest']}")
        print(f"Local data found: {summary['with_local_data']}")
        print(f"Steam userdata found: {summary['with_steam_userdata']}")
        print(f"Remote directories with files: {summary['with_remote_files']}")
        print(f"Report: {args.output.resolve()}")
        return 0
    except (FileNotFoundError, ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
