#!/usr/bin/env python3
"""Copy discovered Steam data into deterministic per-game backup folders."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable


DEFAULT_REPORT = Path("scan_result.generated.json")
DEFAULT_BACKUP_ROOT = Path("backup_result")
HISTORY_INDEX_FILENAME = "history.json"
INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


@dataclass(frozen=True)
class HashInfo:
    digest: str
    files: int
    bytes: int


@dataclass(frozen=True)
class BackupRecord:
    scope: str
    source: str
    destination: str
    kind: str
    hash_info: HashInfo
    backed_up_at: str


@dataclass
class BackupRunResult:
    completed: list[dict[str, object]]
    unchanged: list[dict[str, object]]
    skipped: list[dict[str, object]]
    failed: list[dict[str, object]]


@dataclass
class ChangeDetectionResult:
    completed: list[dict[str, object]]
    failed: list[dict[str, object]]


def sanitize_component(value: str, max_length: int = 100) -> str:
    value = INVALID_FILENAME.sub("_", value).strip().rstrip(".")
    value = re.sub(r"\s+", " ", value)
    if not value:
        value = "unnamed"
    if value.upper() in WINDOWS_RESERVED:
        value = f"_{value}"
    return value[:max_length].rstrip(" .") or "unnamed"


def hash_file(path: Path, chunk_size: int = 1024 * 1024) -> HashInfo:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
            size += len(chunk)
    return HashInfo(digest.hexdigest(), 1, size)


def hash_tree(path: Path) -> HashInfo:
    if path.is_file():
        return hash_file(path)
    digest = hashlib.sha256()
    file_count = 0
    total_bytes = 0
    for root, directories, files in os.walk(path, followlinks=False):
        directories.sort(key=str.casefold)
        files.sort(key=str.casefold)
        root_path = Path(root)
        for directory in directories:
            item = root_path / directory
            relative = item.relative_to(path).as_posix()
            if item.is_symlink():
                target = os.readlink(item)
                digest.update(f"L\0{relative}\0{target}\n".encode("utf-8", errors="surrogatepass"))
            else:
                digest.update(f"D\0{relative}\n".encode("utf-8", errors="surrogatepass"))
        for filename in files:
            item = root_path / filename
            relative = item.relative_to(path).as_posix()
            if item.is_symlink():
                target = os.readlink(item)
                digest.update(f"L\0{relative}\0{target}\n".encode("utf-8", errors="surrogatepass"))
                continue
            info = hash_file(item)
            digest.update(
                f"F\0{relative}\0{info.bytes}\0{info.digest}\n".encode("utf-8", errors="surrogatepass")
            )
            file_count += 1
            total_bytes += info.bytes
    return HashInfo(digest.hexdigest(), file_count, total_bytes)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def collapse_sources(directories: Iterable[str], files: Iterable[str]) -> tuple[list[Path], list[Path]]:
    existing_directories = sorted(
        {Path(value).resolve() for value in directories if value and Path(value).is_dir()},
        key=lambda path: (len(path.parts), os.path.normcase(str(path))),
    )
    kept_directories: list[Path] = []
    for directory in existing_directories:
        if not any(_is_relative_to(directory, parent) for parent in kept_directories):
            kept_directories.append(directory)

    existing_files = sorted(
        {Path(value).resolve() for value in files if value and Path(value).is_file()},
        key=lambda path: os.path.normcase(str(path)),
    )
    kept_files = [
        file_path for file_path in existing_files
        if not any(_is_relative_to(file_path, directory) for directory in kept_directories)
    ]
    return kept_directories, kept_files


def _copy_and_verify(source: Path, destination: Path, expected: HashInfo | None = None) -> HashInfo:
    source_hash = expected or hash_tree(source)
    if source.is_dir():
        shutil.copytree(source, destination, symlinks=True)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination, follow_symlinks=False)
    destination_hash = hash_tree(destination)
    if source_hash != destination_hash:
        raise OSError(f"Hash verification failed: {source} -> {destination}")
    return source_hash


def _steam_account_and_app(path: Path) -> tuple[str, str] | None:
    parts = path.resolve().parts
    for index, part in enumerate(parts):
        if part.casefold() == "userdata" and index + 2 < len(parts):
            return parts[index + 1], parts[index + 2]
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _write_hashes(
    path: Path,
    app_id: int,
    name: str,
    records: list[BackupRecord],
    registry: list[str],
    generated_at: str | None = None,
) -> None:
    lines = [
        "Steam Saves Backup hashes v2",
        f"generated_at\t{generated_at or _now_iso()}",
        f"app_id\t{app_id}",
        f"game\t{name}",
        "algorithm\tSHA-256",
        "",
        "scope\tkind\tsha256\tfiles\tbytes\tbacked_up_at\tsource\tdestination",
    ]
    for record in records:
        info = record.hash_info
        lines.append("\t".join([
            record.scope,
            record.kind,
            info.digest,
            str(info.files),
            str(info.bytes),
            record.backed_up_at,
            record.source,
            record.destination,
        ]))
    if registry:
        lines.extend(["", "registry_keys_not_copied"])
        lines.extend(registry)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_hashes(path: Path) -> tuple[str | None, list[BackupRecord]]:
    if not path.is_file():
        return None, []
    generated_at: str | None = None
    records: list[BackupRecord] = []
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        if line.startswith("generated_at\t"):
            generated_at = line.split("\t", 1)[1]
            continue
        fields = line.split("\t")
        if len(fields) == 8 and fields[0] not in {"scope", "generated_at"}:
            scope, kind, digest, files, size, backed_up_at, source, destination = fields
        elif len(fields) == 7 and fields[0] not in {"scope", "generated_at"}:
            scope, kind, digest, files, size, source, destination = fields
            backed_up_at = generated_at or ""
        else:
            continue
        try:
            records.append(BackupRecord(
                scope=scope,
                source=source,
                destination=destination,
                kind=kind,
                hash_info=HashInfo(digest, int(files), int(size)),
                backed_up_at=backed_up_at,
            ))
        except ValueError:
            continue
    return generated_at, records


def _records_by_scope(records: Iterable[BackupRecord]) -> dict[str, list[BackupRecord]]:
    grouped: dict[str, list[BackupRecord]] = {}
    for record in records:
        grouped.setdefault(record.scope, []).append(record)
    return grouped


def _record_to_json(record: BackupRecord) -> dict[str, object]:
    return {
        "scope": record.scope,
        "source": record.source,
        "destination": record.destination,
        "kind": record.kind,
        "sha256": record.hash_info.digest,
        "files": record.hash_info.files,
        "bytes": record.hash_info.bytes,
        "backed_up_at": record.backed_up_at,
    }


def _record_from_json(value: object) -> BackupRecord | None:
    if not isinstance(value, dict):
        return None
    try:
        return BackupRecord(
            scope=str(value["scope"]),
            source=str(value["source"]),
            destination=str(value["destination"]),
            kind=str(value["kind"]),
            hash_info=HashInfo(
                str(value.get("sha256", "")),
                int(value.get("files", 0)),
                int(value.get("bytes", 0)),
            ),
            backed_up_at=str(value.get("backed_up_at", "")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def read_archive_history(game_directory: Path) -> dict[str, tuple[str, list[BackupRecord]]]:
    """Read restore metadata keyed by archived directory name."""
    path = game_directory / HISTORY_INDEX_FILENAME
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        return {}
    result: dict[str, tuple[str, list[BackupRecord]]] = {}
    archives = data.get("archives", {}) if isinstance(data, dict) else {}
    if not isinstance(archives, dict):
        return result
    for directory_name, entry in archives.items():
        if not isinstance(entry, dict):
            continue
        scope = str(entry.get("scope", ""))
        raw_records = entry.get("records", [])
        if not isinstance(raw_records, list):
            raw_records = []
        records = [
            record for value in raw_records
            if (record := _record_from_json(value)) is not None
        ]
        result[str(directory_name)] = (scope, records)
    return result


def write_archive_history_entry(
    game_directory: Path,
    directory_name: str,
    scope: str,
    records: Iterable[BackupRecord],
) -> None:
    """Persist source mappings for one archived or imported snapshot."""
    path = game_directory / HISTORY_INDEX_FILENAME
    data: dict[str, object] = {"format": "steam-saves-backup-history", "version": 1, "archives": {}}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, dict):
                data.update(loaded)
        except (OSError, ValueError, TypeError):
            pass
    archives = data.get("archives")
    if not isinstance(archives, dict):
        archives = {}
        data["archives"] = archives
    archives[directory_name] = {
        "scope": scope,
        "records": [_record_to_json(record) for record in records],
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def remove_archive_history_entries(game_directory: Path, directory_names: set[str]) -> None:
    path = game_directory / HISTORY_INDEX_FILENAME
    if not path.is_file() or not directory_names:
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        return
    archives = data.get("archives") if isinstance(data, dict) else None
    if not isinstance(archives, dict):
        return
    for name in directory_names:
        archives.pop(name, None)
    if not archives:
        path.unlink(missing_ok=True)
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _record_signature(records: Iterable[BackupRecord]) -> list[tuple[str, str, str, str]]:
    return sorted(
        (record.kind, record.source, record.destination, record.hash_info.digest)
        for record in records
    )


def _scope_directory(scope: str) -> str:
    if scope == "local":
        return "local"
    if scope.startswith("remote:"):
        return sanitize_component(scope.split(":", 1)[1])
    raise ValueError(f"Unknown backup scope: {scope}")


def game_backup_directory(game: dict[str, object], backup_root: Path = DEFAULT_BACKUP_ROOT) -> Path:
    app_id = int(game["app_id"])
    name = str(game["name"])
    return backup_root.resolve() / f"{app_id}_{sanitize_component(name)}"


def collect_game_records(
    game: dict[str, object],
    backed_up_at: str | None = None,
    item_progress: Callable[[str, Path], None] | None = None,
) -> list[BackupRecord]:
    """Hash every currently existing source using the same layout as the backup operation."""
    backup_time = backed_up_at or _now_iso()
    records: list[BackupRecord] = []
    local = game.get("local", {})
    local_directories, local_files = collapse_sources(local.get("directories", []), local.get("files", []))
    for index, source in enumerate([*local_directories, *local_files], start=1):
        if item_progress:
            item_progress("目录" if source.is_dir() else "文件", source)
        target = Path("local") / f"{index:03d}_{sanitize_component(source.name)}"
        records.append(BackupRecord(
            scope="local",
            source=str(source),
            destination=str(target),
            kind="directory" if source.is_dir() else "file",
            hash_info=hash_tree(source),
            backed_up_at=backup_time,
        ))

    userdata = game.get("steam_userdata", {})
    app_directories, _ = collapse_sources(userdata.get("app_directories", []), [])
    covered_remote_roots: list[Path] = []
    for source in app_directories:
        if item_progress:
            item_progress("目录", source)
        identity = _steam_account_and_app(source)
        account = identity[0] if identity else "unknown_account"
        target = Path(sanitize_component(account))
        covered_remote_roots.append(source)
        records.append(BackupRecord(
            scope=f"remote:{account}",
            source=str(source),
            destination=str(target),
            kind="directory",
            hash_info=hash_tree(source),
            backed_up_at=backup_time,
        ))

    fallback_directories, fallback_files = collapse_sources(
        [*userdata.get("remote_directories", []), *userdata.get("manifest_directories", [])],
        userdata.get("files", []),
    )
    fallback_sources = [
        source for source in [*fallback_directories, *fallback_files]
        if not any(_is_relative_to(source, parent) for parent in covered_remote_roots)
    ]
    for index, source in enumerate(fallback_sources, start=1):
        if item_progress:
            item_progress("目录" if source.is_dir() else "文件", source)
        identity = _steam_account_and_app(source)
        account = identity[0] if identity else "unknown_account"
        target = Path(sanitize_component(account)) / f"extra_{index:03d}_{sanitize_component(source.name)}"
        records.append(BackupRecord(
            scope=f"remote:{account}",
            source=str(source),
            destination=str(target),
            kind="directory" if source.is_dir() else "file",
            hash_info=hash_tree(source),
            backed_up_at=backup_time,
        ))
    return records


def _latest_backup_at(generated_at: str | None, records: Iterable[BackupRecord]) -> str | None:
    values = [record.backed_up_at for record in records if record.backed_up_at]
    if generated_at:
        values.append(generated_at)
    if not values:
        return None

    def sort_key(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)

    return max(values, key=sort_key)


def backup_metadata(
    game: dict[str, object],
    backup_root: Path = DEFAULT_BACKUP_ROOT,
) -> dict[str, object]:
    destination = game_backup_directory(game, backup_root)
    generated_at, records = _read_hashes(destination / "hashes.txt")
    return {
        "directory": str(destination),
        "tracked": bool(records),
        "last_backup_at": _latest_backup_at(generated_at, records),
    }


def detect_game_change(
    game: dict[str, object],
    backup_root: Path = DEFAULT_BACKUP_ROOT,
    item_progress: Callable[[str, Path], None] | None = None,
) -> dict[str, object]:
    destination = game_backup_directory(game, backup_root)
    generated_at, previous_records = _read_hashes(destination / "hashes.txt")
    current_records = collect_game_records(game, item_progress=item_progress)
    changed = _record_signature(current_records) != _record_signature(previous_records)
    if not changed:
        for scope in _records_by_scope(current_records):
            if not (destination / _scope_directory(scope)).exists():
                changed = True
                break
    return {
        "app_id": int(game["app_id"]),
        "name": str(game["name"]),
        "changed": changed,
        "current_sources": len(current_records),
        "tracked_sources": len(previous_records),
        "last_backup_at": _latest_backup_at(generated_at, previous_records),
    }


def detect_report_changes(
    report: dict[str, object],
    backup_root: Path = DEFAULT_BACKUP_ROOT,
    progress: Callable[[int, int, str], None] | None = None,
) -> ChangeDetectionResult:
    games = list(report.get("games", []))
    completed: list[dict[str, object]] = []
    failed: list[dict[str, object]] = []
    total = len(games)
    for index, game in enumerate(games, start=1):
        name = str(game.get("name", "Unknown"))
        if progress:
            progress(index, total, f"{name}：开始检测")
        try:
            result = detect_game_change(
                game,
                backup_root,
                item_progress=lambda kind, path: progress(
                    index,
                    total,
                    f"{name}：正在检测{kind} {path}",
                ) if progress else None,
            )
            completed.append(result)
            if progress:
                verdict = "发现差异，需要备份" if result["changed"] else "未发现差异"
                progress(index, total, f"{name}：检测完成，{verdict}")
        except Exception as error:
            failed.append({"app_id": game.get("app_id"), "name": name, "error": str(error)})
            if progress:
                progress(index, total, f"{name}：检测失败，{error}")
    if progress:
        progress(total, total, "全部游戏：更改检测完成")
    return ChangeDetectionResult(completed=completed, failed=failed)


def _version_label(value: str | None, fallback: Path) -> str:
    if value:
        try:
            parsed = datetime.fromisoformat(value)
            return parsed.astimezone().strftime("%Y-%m-%d-%H-%M-%S")
        except ValueError:
            pass
    timestamp = fallback.stat().st_mtime if fallback.exists() else datetime.now().timestamp()
    return datetime.fromtimestamp(timestamp).astimezone().strftime("%Y-%m-%d-%H-%M-%S")


def _unique_version_path(parent: Path, base_name: str, timestamp: str) -> Path:
    candidate = parent / f"{base_name}-{timestamp}"
    suffix = 2
    while candidate.exists():
        candidate = parent / f"{base_name}-{timestamp}-{suffix}"
        suffix += 1
    return candidate


def _install_scope(
    staged: Path,
    current: Path,
    previous_date: str | None,
) -> Path | None:
    archived: Path | None = None
    if current.exists():
        archived = _unique_version_path(
            current.parent,
            current.name,
            _version_label(previous_date, current),
        )
        current.rename(archived)
    try:
        staged.rename(current)
    except Exception:
        if archived is not None and archived.exists() and not current.exists():
            archived.rename(current)
        raise
    return archived


def backup_game(
    game: dict[str, object],
    backup_root: Path,
    backed_up_at: str | None = None,
) -> dict[str, object] | None:
    app_id = int(game["app_id"])
    name = str(game["name"])
    backup_time = backed_up_at or _now_iso()
    records = collect_game_records(game, backup_time)
    if not records:
        return None
    destination = game_backup_directory(game, backup_root)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=backup_root))

    try:
        local = game.get("local", {})
        registry_keys = [str(value) for value in local.get("registry_keys", [])]
        old_generated_at, old_records = _read_hashes(destination / "hashes.txt")
        old_by_scope = _records_by_scope(old_records)
        new_by_scope = _records_by_scope(records)
        final_records: list[BackupRecord] = []
        changed_scopes: list[str] = []
        unchanged_scopes: list[str] = []
        archived_directories: list[str] = []
        destination.mkdir(parents=True, exist_ok=True)

        for scope, scope_records in new_by_scope.items():
            directory_name = _scope_directory(scope)
            staged_scope = staging / directory_name
            current_scope = destination / directory_name
            previous_records = old_by_scope.get(scope, [])
            unchanged = (
                current_scope.exists()
                and _record_signature(scope_records) == _record_signature(previous_records)
            )
            if unchanged:
                final_records.extend(previous_records)
                unchanged_scopes.append(scope)
                continue

            for record in scope_records:
                _copy_and_verify(
                    Path(record.source),
                    staging / record.destination,
                    expected=record.hash_info,
                )
            previous_date = next(
                (record.backed_up_at for record in previous_records if record.backed_up_at),
                old_generated_at,
            )
            archived = _install_scope(staged_scope, current_scope, previous_date)
            if archived is not None:
                archived_directories.append(archived.name)
                write_archive_history_entry(
                    destination,
                    archived.name,
                    scope,
                    previous_records,
                )
            final_records.extend(scope_records)
            changed_scopes.append(scope)

        for scope, previous_records in old_by_scope.items():
            if scope not in new_by_scope:
                final_records.extend(previous_records)

        if changed_scopes or not (destination / "hashes.txt").is_file():
            _write_hashes(
                destination / "hashes.txt",
                app_id,
                name,
                final_records,
                registry_keys,
                generated_at=backup_time,
            )
        shutil.rmtree(staging, ignore_errors=True)
        return {
            "app_id": app_id,
            "name": name,
            "directory": str(destination.resolve()),
            "sources": len(records),
            "files": sum(record.hash_info.files for record in records),
            "bytes": sum(record.hash_info.bytes for record in records),
            "changed_scopes": changed_scopes,
            "unchanged_scopes": unchanged_scopes,
            "archived_directories": archived_directories,
        }
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise


def backup_report(
    report: dict[str, object],
    backup_root: Path = DEFAULT_BACKUP_ROOT,
    progress: Callable[[int, int, str], None] | None = None,
    backed_up_at: str | None = None,
) -> BackupRunResult:
    backup_root = backup_root.resolve()
    backup_root.mkdir(parents=True, exist_ok=True)
    batch_time = backed_up_at or _now_iso()
    games = list(report.get("games", []))
    completed: list[dict[str, object]] = []
    unchanged: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    failed: list[dict[str, object]] = []
    total = len(games)
    for index, game in enumerate(games, start=1):
        name = str(game.get("name", "Unknown"))
        if progress:
            progress(index - 1, total, f"正在备份：{name}")
        try:
            result = backup_game(game, backup_root, batch_time)
            if result is None:
                skipped.append({"app_id": game.get("app_id"), "name": name})
            elif result["changed_scopes"]:
                completed.append(result)
            else:
                unchanged.append(result)
        except Exception as error:
            failed.append({"app_id": game.get("app_id"), "name": name, "error": str(error)})
    if progress:
        progress(total, total, "备份完成")
    return BackupRunResult(completed=completed, unchanged=unchanged, skipped=skipped, failed=failed)


def load_report(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Back up paths from a Steam save scan report.")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--output", type=Path, default=DEFAULT_BACKUP_ROOT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.report.is_file():
        print(f"error: scan report not found: {args.report}")
        return 1
    result = backup_report(
        load_report(args.report),
        args.output,
        progress=lambda current, total, message: print(f"[{current}/{total}] {message}"),
    )
    total_bytes = sum(int(item["bytes"]) for item in result.completed)
    print(f"Completed games: {len(result.completed)}")
    print(f"Unchanged games: {len(result.unchanged)}")
    print(f"Skipped games without existing data: {len(result.skipped)}")
    print(f"Failed games: {len(result.failed)}")
    print(f"Copied bytes: {total_bytes}")
    print(f"Backup root: {args.output.resolve()}")
    for failure in result.failed:
        print(f"FAILED {failure['app_id']} {failure['name']}: {failure['error']}")
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
