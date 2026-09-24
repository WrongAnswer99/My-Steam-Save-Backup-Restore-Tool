#!/usr/bin/env python3
"""Discover, package, and remove versioned Steam backup snapshots."""

from __future__ import annotations

import os
import json
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Iterable

try:
    from src.backup_manager import (
        BackupRecord,
        HashInfo,
        _read_hashes,
        _records_by_scope,
        _scope_directory,
        read_archive_history,
        remove_archive_history_entries,
        write_archive_history_entry,
    )
except ModuleNotFoundError:  # Allows importing when ``src`` is the working directory.
    from backup_manager import (
        BackupRecord,
        HashInfo,
        _read_hashes,
        _records_by_scope,
        _scope_directory,
        read_archive_history,
        remove_archive_history_entries,
        write_archive_history_entry,
    )


TIMESTAMP_FORMAT = "%Y-%m-%d-%H-%M-%S"
PACKAGE_MANIFEST = "steam-saves-backup.json"
PACKAGE_FORMAT = "steam-saves-backup-package"
ARCHIVE_DIRECTORY = re.compile(
    r"^(?P<scope>.+)-(?P<timestamp>\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})"
    r"(?:-(?P<ordinal>(?:[2-9]|[1-9]\d+)))?$"
)


@dataclass(frozen=True)
class BackupSnapshot:
    """One local/account directory as it existed at a particular time."""

    timestamp: datetime
    timestamp_key: str
    game_directory: Path
    scope_name: str
    source_directory: Path
    current: bool
    ordinal: int = 1
    record_scope: str | None = None
    records: tuple[BackupRecord, ...] = ()


@dataclass(frozen=True)
class HistoryPoint:
    """All snapshots whose backup timestamp falls in the same local second."""

    timestamp: datetime
    timestamp_key: str
    snapshots: tuple[BackupSnapshot, ...]

    @property
    def game_count(self) -> int:
        return len({snapshot.game_directory for snapshot in self.snapshots})

    @property
    def archived_count(self) -> int:
        return sum(not snapshot.current for snapshot in self.snapshots)


@dataclass(frozen=True)
class PackageResult:
    output: Path
    snapshot_count: int
    game_count: int
    file_count: int


@dataclass(frozen=True)
class ClearResult:
    removed: tuple[Path, ...]


@dataclass(frozen=True)
class ImportResult:
    imported: tuple[Path, ...]
    time_points: tuple[str, ...]


@dataclass(frozen=True)
class RestoreResult:
    restored_sources: int
    restored_files: int
    skipped_scopes: tuple[str, ...]


def _local_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed.astimezone()


def _timestamp_key(value: datetime) -> str:
    return value.astimezone().strftime(TIMESTAMP_FORMAT)


def _directory_timestamp(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, TIMESTAMP_FORMAT).astimezone()
    except ValueError:
        return None


def _snapshot_rank(snapshot: BackupSnapshot) -> tuple[datetime, int, int]:
    # A current directory is the final result if an archive and current version
    # happen to share the same second. Higher collision suffixes are newer.
    return snapshot.timestamp, int(snapshot.current), snapshot.ordinal


def discover_snapshots(backup_root: Path) -> list[BackupSnapshot]:
    """Return snapshots created by this application under ``backup_root``."""
    backup_root = backup_root.resolve()
    if not backup_root.is_dir():
        return []

    snapshots: list[BackupSnapshot] = []
    for game_directory in sorted(
        (path for path in backup_root.iterdir() if path.is_dir() and not path.name.startswith(".")),
        key=lambda path: path.name.casefold(),
    ):
        generated_at, records = _read_hashes(game_directory / "hashes.txt")
        current_by_scope = _records_by_scope(records)
        archive_history = read_archive_history(game_directory)
        for scope, scope_records in current_by_scope.items():
            try:
                scope_name = _scope_directory(scope)
            except ValueError:
                continue
            source = game_directory / scope_name
            if not source.is_dir():
                continue
            raw_time = next(
                (record.backed_up_at for record in scope_records if record.backed_up_at),
                generated_at,
            )
            timestamp = _local_datetime(raw_time) if raw_time else None
            if timestamp is None:
                timestamp = datetime.fromtimestamp(source.stat().st_mtime).astimezone()
            snapshots.append(BackupSnapshot(
                timestamp=timestamp,
                timestamp_key=_timestamp_key(timestamp),
                game_directory=game_directory,
                scope_name=scope_name,
                source_directory=source,
                current=True,
                record_scope=scope,
                records=tuple(scope_records),
            ))

        for source in game_directory.iterdir():
            if not source.is_dir():
                continue
            match = ARCHIVE_DIRECTORY.fullmatch(source.name)
            if match is None:
                continue
            timestamp = _directory_timestamp(match.group("timestamp"))
            if timestamp is None:
                continue
            scope_name = match.group("scope")
            archived_scope, archived_records = archive_history.get(source.name, ("", []))
            fallback_scope = "local" if scope_name == "local" else f"remote:{scope_name}"
            restore_records = archived_records or current_by_scope.get(archived_scope or fallback_scope, [])
            snapshots.append(BackupSnapshot(
                timestamp=timestamp,
                timestamp_key=match.group("timestamp"),
                game_directory=game_directory,
                scope_name=scope_name,
                source_directory=source,
                current=False,
                ordinal=int(match.group("ordinal") or 1),
                record_scope=archived_scope or fallback_scope,
                records=tuple(restore_records),
            ))
    return sorted(snapshots, key=_snapshot_rank, reverse=True)


def list_history(backup_root: Path) -> list[HistoryPoint]:
    grouped: dict[str, list[BackupSnapshot]] = {}
    for snapshot in discover_snapshots(backup_root):
        grouped.setdefault(snapshot.timestamp_key, []).append(snapshot)
    points = [
        HistoryPoint(
            timestamp=max(item.timestamp for item in snapshots),
            timestamp_key=key,
            snapshots=tuple(sorted(snapshots, key=_snapshot_rank, reverse=True)),
        )
        for key, snapshots in grouped.items()
    ]
    return sorted(points, key=lambda point: point.timestamp, reverse=True)


def snapshots_for_package(
    snapshots: Iterable[BackupSnapshot],
    timestamp_key: str,
    complete_state: bool,
) -> list[BackupSnapshot]:
    """Select updated-only or complete-state snapshots for one history point."""
    available = list(snapshots)
    target = _directory_timestamp(timestamp_key)
    if target is None:
        raise ValueError(f"无效的历史时间：{timestamp_key}")

    selected: dict[tuple[Path, str], BackupSnapshot] = {}
    for snapshot in available:
        if complete_state:
            if snapshot.timestamp > target:
                continue
        elif snapshot.timestamp_key != timestamp_key:
            continue
        identity = (snapshot.game_directory, snapshot.scope_name)
        previous = selected.get(identity)
        if previous is None or _snapshot_rank(snapshot) > _snapshot_rank(previous):
            selected[identity] = snapshot
    return sorted(
        selected.values(),
        key=lambda item: (item.game_directory.name.casefold(), item.scope_name.casefold()),
    )


def _zip_directory(
    archive: zipfile.ZipFile,
    snapshot: BackupSnapshot,
    root_prefix: Path | None = None,
) -> int:
    if snapshot.scope_name in {"", ".", ".."}:
        raise ValueError(f"无效的备份分区名：{snapshot.scope_name!r}")
    prefix = (root_prefix or Path()) / snapshot.game_directory.name / snapshot.scope_name
    file_count = 0
    wrote_anything = False
    for root, directories, files in os.walk(snapshot.source_directory, followlinks=False):
        directories.sort(key=str.casefold)
        files.sort(key=str.casefold)
        root_path = Path(root)
        relative_root = root_path.relative_to(snapshot.source_directory)
        archive_root = prefix / relative_root
        if not directories and not files:
            archive.writestr(archive_root.as_posix().rstrip("/") + "/", b"")
            wrote_anything = True
        for filename in files:
            source = root_path / filename
            archive.write(source, (archive_root / filename).as_posix())
            file_count += 1
            wrote_anything = True
    if not wrote_anything:
        archive.writestr(prefix.as_posix().rstrip("/") + "/", b"")
    return file_count


def _write_package(
    output: Path,
    entries: list[tuple[Path | None, BackupSnapshot, str]],
    complete_state: bool,
    layout: str,
) -> PackageResult:
    if not entries:
        raise ValueError("没有可打包的存档。")
    output = output.resolve()
    if output.suffix.casefold() != ".zip":
        output = output.with_suffix(".zip")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.stem}-",
        suffix=".zip.tmp",
        dir=output.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    file_count = 0
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            manifest = {
                "format": PACKAGE_FORMAT,
                "version": 1,
                "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "mode": "complete_state" if complete_state else "updated_only",
                "layout": layout,
                "time_points": list(dict.fromkeys(point for _prefix, _snapshot, point in entries)),
                "snapshots": [
                    {
                        "time_point": point,
                        "game_directory": snapshot.game_directory.name,
                        "scope": snapshot.scope_name,
                        "records": [_record_to_package(record) for record in snapshot.records],
                    }
                    for _prefix, snapshot, point in entries
                ],
            }
            archive.writestr(
                PACKAGE_MANIFEST,
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            )
            for prefix, snapshot, _point in entries:
                file_count += _zip_directory(archive, snapshot, prefix)
        os.replace(temporary, output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    snapshots = [snapshot for _prefix, snapshot, _point in entries]
    return PackageResult(
        output=output,
        snapshot_count=len(snapshots),
        game_count=len({snapshot.game_directory for snapshot in snapshots}),
        file_count=file_count,
    )


def _record_to_package(record: BackupRecord) -> dict[str, object]:
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


def _record_from_package(value: object) -> BackupRecord | None:
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


def package_history(
    backup_root: Path,
    timestamp_key: str,
    output: Path,
    complete_state: bool,
) -> PackageResult:
    """Create an atomic ZIP whose scope folders never contain version suffixes."""
    selected = snapshots_for_package(
        discover_snapshots(backup_root),
        timestamp_key,
        complete_state,
    )
    if not selected:
        raise ValueError("该时间点没有可打包的存档。")

    return _write_package(
        output,
        [(None, snapshot, timestamp_key) for snapshot in selected],
        complete_state,
        "single_point",
    )


def package_history_points(
    backup_root: Path,
    timestamp_keys: Iterable[str],
    output: Path,
    complete_state: bool,
) -> PackageResult:
    """Package several selected points into one ZIP, grouped by timestamp."""
    keys = list(dict.fromkeys(timestamp_keys))
    if not keys:
        raise ValueError("请先选择至少一个历史时间点。")
    available = discover_snapshots(backup_root)
    entries: list[tuple[Path, BackupSnapshot, str]] = []
    for key in keys:
        selected = snapshots_for_package(available, key, complete_state)
        entries.extend((Path(key), snapshot, key) for snapshot in selected)
    if not entries:
        raise ValueError("所选时间点没有可打包的存档。")
    return _write_package(output, entries, complete_state, "multiple_points")


def import_history_package(backup_root: Path, package_path: Path) -> ImportResult:
    """Import one of this application's ZIP packages as non-current history."""
    backup_root = backup_root.resolve()
    backup_root.mkdir(parents=True, exist_ok=True)
    package_path = package_path.resolve()
    imported: list[Path] = []
    imported_points: list[str] = []
    with zipfile.ZipFile(package_path) as archive:
        try:
            manifest = json.loads(archive.read(PACKAGE_MANIFEST).decode("utf-8-sig"))
        except KeyError as error:
            raise ValueError(f"压缩包中缺少 {PACKAGE_MANIFEST}，无法确认备份结构。") from error
        except (UnicodeError, ValueError) as error:
            raise ValueError("压缩包中的备份清单无法读取。") from error
        if not isinstance(manifest, dict) or manifest.get("format") != PACKAGE_FORMAT:
            raise ValueError("这不是受支持的 Steam 存档备份压缩包。")
        layout = str(manifest.get("layout", ""))
        if layout not in {"single_point", "multiple_points"}:
            raise ValueError("压缩包使用了未知的目录布局。")
        snapshots = manifest.get("snapshots")
        if not isinstance(snapshots, list):
            raise ValueError("压缩包没有有效的存档清单。")

        for item in snapshots:
            if not isinstance(item, dict):
                continue
            point = str(item.get("time_point", ""))
            game_name = str(item.get("game_directory", ""))
            scope_name = str(item.get("scope", ""))
            if _directory_timestamp(point) is None:
                raise ValueError(f"压缩包包含无效时间点：{point!r}")
            _validate_component(game_name, "游戏目录")
            _validate_component(scope_name, "备份分区")
            prefix = (
                PurePosixPath(point, game_name, scope_name)
                if layout == "multiple_points" else
                PurePosixPath(game_name, scope_name)
            )
            game_directory = backup_root / game_name
            game_directory.mkdir(parents=True, exist_ok=True)
            staging_parent = Path(tempfile.mkdtemp(prefix=".import-", dir=game_directory))
            staged = staging_parent / "content"
            staged.mkdir()
            try:
                _extract_snapshot(archive, prefix, staged)
                destination = _unique_import_path(game_directory, scope_name, point)
                staged.rename(destination)
                imported.append(destination)
                imported_points.append(point)
                raw_records = item.get("records", [])
                records = [
                    record for value in raw_records
                    if (record := _record_from_package(value)) is not None
                ] if isinstance(raw_records, list) else []
                logical_scope = records[0].scope if records else (
                    "local" if scope_name == "local" else f"remote:{scope_name}"
                )
                write_archive_history_entry(
                    game_directory,
                    destination.name,
                    logical_scope,
                    records,
                )
            finally:
                if staging_parent.exists():
                    shutil.rmtree(staging_parent, ignore_errors=True)
    if not imported:
        raise ValueError("压缩包中没有可导入的存档目录。")
    return ImportResult(tuple(imported), tuple(dict.fromkeys(imported_points)))


def _validate_component(value: str, label: str) -> None:
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError(f"压缩包包含无效的{label}：{value!r}")


def _extract_snapshot(archive: zipfile.ZipFile, prefix: PurePosixPath, destination: Path) -> None:
    prefix_parts = prefix.parts
    found = False
    for info in archive.infolist():
        if "\\" in info.filename:
            raise ValueError("压缩包包含不安全的路径。")
        parts = PurePosixPath(info.filename).parts
        if len(parts) < len(prefix_parts) or parts[:len(prefix_parts)] != prefix_parts:
            continue
        relative = parts[len(prefix_parts):]
        if any(part in {"", ".", ".."} for part in relative):
            raise ValueError("压缩包包含不安全的相对路径。")
        found = True
        target = destination.joinpath(*relative) if relative else destination
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output)
    if not found:
        raise ValueError(f"压缩包缺少清单中声明的目录：{prefix.as_posix()}")


def _unique_import_path(parent: Path, scope_name: str, timestamp_key: str) -> Path:
    candidate = parent / f"{scope_name}-{timestamp_key}"
    ordinal = 2
    while candidate.exists():
        candidate = parent / f"{scope_name}-{timestamp_key}-{ordinal}"
        ordinal += 1
    return candidate


def list_game_history(backup_root: Path, game_directory: Path) -> list[HistoryPoint]:
    game_directory = game_directory.resolve()
    points: list[HistoryPoint] = []
    for point in list_history(backup_root):
        snapshots = tuple(
            snapshot for snapshot in point.snapshots
            if snapshot.game_directory.resolve() == game_directory
        )
        if snapshots:
            points.append(HistoryPoint(point.timestamp, point.timestamp_key, snapshots))
    return points


def restore_game_history_point(
    backup_root: Path,
    game_directory: Path,
    timestamp_key: str,
) -> RestoreResult:
    """Merge a game's complete state at a point back into its original paths."""
    game_directory = game_directory.resolve()
    snapshots = [
        snapshot for snapshot in snapshots_for_package(
            discover_snapshots(backup_root),
            timestamp_key,
            complete_state=True,
        )
        if snapshot.game_directory.resolve() == game_directory
    ]
    if not snapshots:
        raise ValueError("该游戏在这个时间点没有可恢复的存档。")
    restored_sources = 0
    restored_files = 0
    skipped: list[str] = []
    for snapshot in snapshots:
        if not snapshot.records:
            skipped.append(snapshot.scope_name)
            continue
        for record in snapshot.records:
            backup_source = _record_backup_path(snapshot, record)
            original = Path(record.source)
            if not backup_source.exists():
                raise FileNotFoundError(f"备份内容不存在：{backup_source}")
            if record.kind == "directory":
                if not backup_source.is_dir():
                    raise ValueError(f"备份类型不匹配：{backup_source}")
                original.mkdir(parents=True, exist_ok=True)
                shutil.copytree(backup_source, original, dirs_exist_ok=True, symlinks=True)
                restored_files += sum(len(files) for _root, _dirs, files in os.walk(backup_source))
            else:
                original.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup_source, original, follow_symlinks=False)
                restored_files += 1
            restored_sources += 1
    if restored_sources == 0:
        raise ValueError("该历史版本缺少原始路径映射，无法自动恢复。")
    return RestoreResult(restored_sources, restored_files, tuple(skipped))


def _record_backup_path(snapshot: BackupSnapshot, record: BackupRecord) -> Path:
    destination = Path(record.destination)
    parts = destination.parts
    if parts and parts[0].casefold() == snapshot.scope_name.casefold():
        parts = parts[1:]
    candidate = snapshot.source_directory.joinpath(*parts).resolve()
    try:
        candidate.relative_to(snapshot.source_directory.resolve())
    except ValueError as error:
        raise ValueError(f"备份记录指向了分区之外：{record.destination}") from error
    return candidate


def clear_history_point(
    backup_root: Path,
    timestamp_key: str,
    game_directory: Path | None = None,
) -> ClearResult:
    """Remove every snapshot at a point and untrack deleted current scopes."""
    backup_root = backup_root.resolve()
    matching = [
        snapshot for snapshot in discover_snapshots(backup_root)
        if snapshot.timestamp_key == timestamp_key
        and (game_directory is None or snapshot.game_directory.resolve() == game_directory.resolve())
    ]
    removed: list[Path] = []
    current_scopes: dict[Path, set[str]] = {}
    archived_names: dict[Path, set[str]] = {}
    for snapshot in matching:
        source = snapshot.source_directory.resolve()
        try:
            source.relative_to(backup_root)
        except ValueError as error:
            raise ValueError(f"拒绝清除备份根目录之外的路径：{source}") from error
        if source.is_dir():
            shutil.rmtree(source)
            removed.append(source)
        if snapshot.current and snapshot.record_scope:
            current_scopes.setdefault(snapshot.game_directory, set()).add(snapshot.record_scope)
        elif not snapshot.current:
            archived_names.setdefault(snapshot.game_directory, set()).add(snapshot.source_directory.name)
    for game_directory, scopes in current_scopes.items():
        _remove_hash_records(game_directory / "hashes.txt", scopes)
    for archived_game, directory_names in archived_names.items():
        remove_archive_history_entries(archived_game, directory_names)
    return ClearResult(tuple(removed))


def _remove_hash_records(path: Path, scopes: set[str]) -> None:
    """Remove complete current scopes without changing unrelated hash metadata."""
    if not path.is_file() or not scopes:
        return
    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    kept: list[str] = []
    remaining_records = 0
    for line in lines:
        fields = line.split("\t")
        is_record = len(fields) in {7, 8} and fields[0] not in {"scope", "generated_at"}
        if is_record and fields[0] in scopes:
            continue
        kept.append(line)
        remaining_records += int(is_record)
    if remaining_records == 0:
        path.unlink(missing_ok=True)
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("\n".join(kept) + "\n", encoding="utf-8")
    os.replace(temporary, path)
