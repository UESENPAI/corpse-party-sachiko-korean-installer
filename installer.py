#!/usr/bin/env python3
"""Offline, reversible Korean patch installer for the configured Steam game.

Requires Python 3.10+. No third-party Python packages are needed.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tempfile
from typing import Iterable

from project_config import GAME
APP_ID = str(GAME["app_id"])
PATCH_ID = GAME["patch_id"]
STATE_DIR = "." + PATCH_ID
MANAGED_FILES = {"scene.bcx", "scene_jpn.bcx", "font.bcx", "system.bcx", "system_jpn.bcx"}
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PatchError(Exception):
    """A validation failure that must not be bypassed automatically."""


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def safe_relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise PatchError(f"Invalid relative payload path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in ("", ".", "..") for p in value.split("/")):
        raise PatchError(f"Unsafe payload path: {value!r}")
    if path.parts[0] == STATE_DIR:
        raise PatchError("Payload may not overwrite installer state.")
    # These names alias devices on Windows, even when followed by an extension.
    for part in path.parts:
        if part.endswith((" ", ".")) or re.match(r"(?i)^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", part):
            raise PatchError(f"Unsafe Windows payload path: {value!r}")
    return path.as_posix()


def checked_path(root: Path, relative: str) -> Path:
    relative = safe_relative(relative)
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            raise PatchError(f"Symbolic links are not allowed in patch destinations: {current}")
    return current


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise PatchError(f"Cannot read JSON {path}: {exc}") from exc


def atomic_json(path: Path, value) -> None:
    data = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    fd, temp_name = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def parse_vdf(text: str) -> dict:
    """Read Valve's quoted KeyValues, retaining Windows backslashes correctly."""
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|[{}]', re.sub(r"(?m)^\s*//.*$", "", text))
    position = 0

    def string(token):
        if not token.startswith('"'):
            raise ValueError("Expected quoted VDF string")
        return token[1:-1].replace('\\"', '"').replace("\\\\", "\\")

    def object_():
        nonlocal position
        result = {}
        while position < len(tokens):
            token = tokens[position]
            position += 1
            if token == "}":
                return result
            key = string(token)
            if position >= len(tokens):
                raise ValueError("Incomplete VDF")
            value = tokens[position]
            position += 1
            result[key] = object_() if value == "{" else string(value)
        return result

    return object_()


def _case_correct_path(path: Path) -> Path:
    """Repair case aliases only for a Windows path mounted by WSL."""
    current = Path(path.anchor)
    for part in path.parts[1:]:
        candidate = current / part
        if not candidate.exists():
            try:
                matches = [p for p in current.iterdir() if p.name.casefold() == part.casefold()]
            except OSError:
                matches = []
            if len(matches) == 1:
                candidate = matches[0]
        current = candidate
    return current


def native_library_path(value: str) -> Path:
    if os.name != "nt" and re.match(r"^[A-Za-z]:[\\/]", value):
        path = Path("/mnt") / value[0].lower() / value[3:].replace("\\", "/")
        return _case_correct_path(path)
    return Path(value).expanduser()


def _path_identity(path: Path) -> tuple:
    """Identify existing aliases without folding distinct Linux directories."""
    try:
        info = path.stat()
        if info.st_ino:
            return ("inode", info.st_dev, info.st_ino)
    except OSError:
        pass
    return ("path", os.path.normcase(str(path)))


def _mounted_steam_drives(home: Path) -> list[Path]:
    drives = []
    locations = [Path("/mnt"), Path("/run/media"), Path("/media")]
    for base in (Path("/run/media"), Path("/media")):
        locations.extend(base / user for user in dict.fromkeys((home.name, "deck")))
    for location in locations:
        try:
            drives.extend(p for p in location.iterdir() if p.is_dir())
        except OSError:
            pass
    return drives


def steam_roots(home: Path | None = None) -> list[Path]:
    home = home or Path.home()
    roots = [
        home / ".steam/steam", home / ".steam/root", home / ".local/share/Steam",
        home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
        home / ".var/app/com.valvesoftware.Steam/data/Steam",
        home / ".var/app/com.valvesoftware.Steam/.steam/steam",
        home / ".var/app/com.valvesoftware.Steam/.steam/root",
        home / "snap/steam/common/.local/share/Steam",
        home / "snap/steam/common/.steam/steam",
        home / "snap/steam/common/.steam/root",
    ]
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg and Path(xdg).is_absolute():
        roots.append(Path(xdg) / "Steam")
    snap = os.environ.get("SNAP_USER_COMMON")
    if snap and Path(snap).is_absolute():
        roots.extend(Path(snap) / suffix for suffix in (".local/share/Steam", ".steam/steam", ".steam/root"))
    for key in ("STEAM_DIR", "STEAM_PATH", "STEAM_COMPAT_CLIENT_INSTALL_PATH"):
        if os.environ.get(key):
            roots.append(native_library_path(os.environ[key]))
    library_paths = os.environ.get("STEAM_COMPAT_LIBRARY_PATHS", "")
    separator = ";" if ";" in library_paths or re.match(r"^[A-Za-z]:[\\/]", library_paths) else os.pathsep
    roots.extend(native_library_path(value) for value in library_paths.split(separator) if value)
    for key in ("STEAM_COMPAT_DATA_PATH", "STEAM_COMPAT_INSTALL_PATH"):
        if os.environ.get(key):
            data_path = native_library_path(os.environ[key])
            for parent in (data_path, *data_path.parents):
                if parent.name.casefold() == "steamapps":
                    roots.append(parent.parent)
                    break
    drives = []
    if os.name == "nt":
        import winreg
        for hive, key, name in (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
        ):
            try:
                with winreg.OpenKey(hive, key) as handle:
                    roots.append(Path(winreg.QueryValueEx(handle, name)[0]))
            except OSError:
                pass
        for key in ("ProgramFiles(x86)", "ProgramFiles"):
            if os.environ.get(key):
                roots.append(Path(os.environ[key]) / "Steam")
        drives = [Path(f"{letter}:/") for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if Path(f"{letter}:/").is_dir()]
    else:
        drives = _mounted_steam_drives(home)
    for drive in drives:
        roots.extend(drive / suffix for suffix in (
            "", "Steam", "steam", "SteamLibrary", "steamlibrary", "Games/Steam", "Games/SteamLibrary",
            "Program Files (x86)/Steam", "Program Files/Steam",
            "program files (x86)/steam", "program files/steam",
        ))
    return unique_paths(roots)


def unique_paths(paths: Iterable[Path]) -> list[Path]:
    result, seen = [], set()
    for path in paths:
        try:
            path = path.expanduser()
            if os.name != "nt" and re.match(r"^/mnt/[A-Za-z](?:/|$)", str(path)):
                path = _case_correct_path(path)
            path = path.resolve()
            key = _path_identity(path)
        except (OSError, ValueError, RuntimeError):
            continue
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def steam_libraries(roots: Iterable[Path]) -> list[Path]:
    libraries = unique_paths(roots)
    index = 0
    while index < len(libraries):
        root = libraries[index]
        index += 1
        additional = []
        for relative in ("steamapps/libraryfolders.vdf", "config/libraryfolders.vdf"):
            vdf = root / relative
            if not vdf.is_file():
                continue
            try:
                data = parse_vdf(vdf.read_text(encoding="utf-8-sig"))
                entries = data.get("libraryfolders", data.get("LibraryFolders", {}))
                for key, value in entries.items():
                    if not key.isdigit():
                        continue
                    value = value.get("path") if isinstance(value, dict) else value
                    if value:
                        path = native_library_path(value)
                        if path.is_absolute():
                            additional.append(path)
            except (OSError, ValueError, AttributeError, TypeError):
                continue
        if additional:
            libraries = unique_paths([*libraries, *additional])
    return libraries


def validate_game(path: Path) -> Path:
    paths = unique_paths([path])
    if not paths or not paths[0].is_dir():
        raise PatchError("게임 설치 폴더가 없습니다.")
    path = paths[0]
    steamapps = path.parent.parent
    if path.parent.name.casefold() != "common" or steamapps.name.casefold() != "steamapps":
        raise PatchError("Steam 설치 폴더가 아닙니다.")
    manifest = steamapps / f"appmanifest_{APP_ID}.acf"
    try:
        app = parse_vdf(manifest.read_text(encoding="utf-8-sig")).get("AppState", {})
        flags = int(app.get("StateFlags", "0"))
    except (OSError, ValueError, AttributeError) as exc:
        raise PatchError("해당 게임의 Steam 설치 기록이 없습니다.") from exc
    name = app.get("installdir", "")
    if (str(app.get("appid")) != APP_ID or not flags & 4 or not name
            or name in (".", "..") or "/" in name or "\\" in name
            or _path_identity((steamapps / "common" / name).resolve()) != _path_identity(path)):
        raise PatchError("해당 게임의 정상 Steam 설치 기록과 일치하지 않습니다.")
    exe = checked_path(path, GAME["executable"])
    if not exe.is_file() or exe.stat().st_size == 0:
        raise PatchError("게임 실행 파일이 없거나 비어 있습니다.")
    return path


def discover_games(roots: Iterable[Path] | None = None) -> list[dict]:
    found = []
    for library in steam_libraries(steam_roots() if roots is None else roots):
        manifest = library / "steamapps" / f"appmanifest_{APP_ID}.acf"
        try:
            app = parse_vdf(manifest.read_text(encoding="utf-8-sig")).get("AppState", {})
            if app.get("appid") != APP_ID:
                continue
            directory = app.get("installdir", "")
            if not directory or "/" in directory or "\\" in directory or directory in (".", ".."):
                continue
            game = validate_game(library / "steamapps/common" / directory)
            found.append({"path": str(game), "build_id": app.get("buildid", ""),
                          "language": app.get("UserConfig", {}).get("language", ""),
                          "library": str(library)})
        except (OSError, ValueError, AttributeError, PatchError):
            continue
    result, seen = [], set()
    for game in found:
        key = _path_identity(Path(game["path"]))
        if key not in seen:
            result.append(game)
            seen.add(key)
    return result


def load_payload(payload: Path) -> tuple[dict, dict]:
    from public_plan import validate_manifest, validate_plan

    if payload.is_symlink() or (payload / "files").is_symlink():
        raise PatchError("Payload directories may not be symbolic links.")
    manifest = read_json(checked_path(payload, 'manifest.json'))
    try:
        entry = validate_manifest(manifest)
        if {p.name for p in payload.iterdir()} != {'manifest.json', 'files'}:
            raise ValueError('Unexpected files in the public payload')
        if {p.name for p in (payload / 'files').iterdir()} != {'recipe.json'}:
            raise ValueError('Public payload must contain only the recipe metadata')
        path = checked_path(payload / 'files', 'recipe.json')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry['sha256']:
            raise ValueError('Recipe metadata checksum mismatch')
        recipe = json.loads(raw)
        validate_plan(recipe, manifest['supported_sources'][0]['files'])
    except (ValueError, OSError) as exc:
        raise PatchError(f'Invalid public donor payload: {exc}') from exc
    return manifest, recipe


def original_file(game: Path, relative: str, expected: str, context: dict | None = None) -> Path:
    """Resolve only a fingerprinted original or its verified active backup.

    Localized executables may also be the original-byte encryption key. A
    managed replacement must resolve back to its exact original; an orphan
    backup or an independently modified installed file never unlocks it.
    """
    if not isinstance(expected, str) or not SHA256.fullmatch(expected):
        raise PatchError('Invalid original file fingerprint.')
    target = checked_path(game, relative)
    if target.is_file() and digest(target) == expected:
        return target
    cache = {} if context is None else context
    if 'state' not in cache:
        root = ensure_state_dir(game)
        state = load_state(root)
        validate_state(game, root, state, allow_recoverable=True)
        cache['state'] = (root, state)
    root, state = cache['state']
    entry = state.get('files', {}).get(relative) if state else None
    if (not entry or entry.get('original_sha256') != expected or not target.is_file()
            or digest(target) != entry.get('installed_sha256')):
        raise PatchError('The game file is not a supported original or verified installed resource: ' + relative)
    backup = backup_path(root, expected)
    if not backup.is_file() or digest(backup) != expected:
        raise PatchError('Original backup checksum mismatch: ' + relative)
    return backup


def validate_supported_source(game: Path, manifest: dict) -> str | None:
    sources = manifest.get("supported_sources", [])
    if not sources:
        return None
    cache = {}
    context = {}
    for source in sources:
        matched = True
        for relative, expected in source["files"].items():
            identity = (relative, expected)
            if identity not in cache:
                try:
                    cache[identity] = original_file(game, relative, expected, context)
                except (PatchError, OSError):
                    cache[identity] = None
            if cache[identity] is None:
                matched = False
                break
        if matched:
            return source["label"]
    labels = ", ".join(source["label"] for source in sources)
    raise PatchError("지원하지 않는 게임 버전 또는 언어입니다. Steam 게임 속성에서 지원 언어를 선택하고 파일 다운로드를 마친 뒤 다시 실행하세요. 지원 원본: " + labels)


def ensure_state_dir(game: Path, create: bool = False) -> Path:
    root = game / STATE_DIR
    if root.is_symlink():
        raise PatchError("Installer backup directory may not be a symbolic link.")
    if root.exists() and not root.is_dir():
        raise PatchError("Installer backup directory is not a directory.")
    if create:
        root.mkdir(exist_ok=True)
    return root


@contextlib.contextmanager
def installer_lock(root: Path):
    lock = root / ".lock"
    if lock.is_symlink():
        raise PatchError("Installer lock may not be a symbolic link.")
    with lock.open("a+b") as stream:
        try:
            if os.name == "nt":
                import msvcrt
                stream.seek(0)
                if stream.read(1) == b"":
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise PatchError("Another patch installer is already running for this game.") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def load_state(root: Path) -> dict | None:
    state_file = root / "state.json"
    if state_file.is_symlink():
        raise PatchError("Installer state may not be a symbolic link.")
    if not state_file.exists():
        return None
    state = read_json(state_file)
    if not isinstance(state, dict):
        raise PatchError("Invalid installer backup state object.")
    if state.get("schema") != 1 or state.get("patch_id") != PATCH_ID or not isinstance(state.get("files"), dict):
        raise PatchError("Invalid installer backup state.")
    for path, entry in state["files"].items():
        if path not in MANAGED_FILES or not isinstance(entry, dict):
            raise PatchError(f"Invalid installer backup entry: {path}")
        for key in ("installed_sha256", "original_sha256"):
            value = entry.get(key)
            if value is not None and (not isinstance(value, str) or not SHA256.fullmatch(value)):
                raise PatchError(f"Invalid backup hash: {path}")
        if not entry.get("installed_sha256"):
            raise PatchError(f"Missing installed hash: {path}")
    return state


def backup_path(root: Path, sha: str) -> Path:
    if not SHA256.fullmatch(sha):
        raise PatchError("Invalid backup content address.")
    directory = root / "originals"
    if directory.is_symlink():
        raise PatchError("Backup directory may not be a symbolic link.")
    result = directory / sha
    if result.is_symlink():
        raise PatchError("Backup files may not be symbolic links.")
    return result


def validate_state(game: Path, root: Path, state: dict | None, allow_recoverable: bool = False,
                   supported_originals: dict[str, list[str]] | None = None) -> list[str]:
    recoverable = []
    if not state:
        return recoverable
    for path, entry in state["files"].items():
        target = checked_path(game, path)
        current = digest(target) if target.is_file() else None
        original = entry.get("original_sha256")
        if current != entry["installed_sha256"]:
            restored_original = original is not None and current == original
            missing_added_file = original is None and not target.exists()
            # Only the install path supplies hashes from a verified new payload.
            # A Steam language switch may restore a different supported index.
            supported_replacement = original is not None and current in (supported_originals or {}).get(path, [])
            if allow_recoverable and (restored_original or missing_added_file or supported_replacement):
                recoverable.append(path)
            else:
                raise PatchError(f"Installed file was changed or removed: {path}. Restore its exact original or patched bytes before reinstalling; backups were preserved.")
        if original:
            backup = backup_path(root, original)
            if not backup.is_file() or digest(backup) != original:
                raise PatchError(f"Original backup checksum mismatch: {path}")
    return recoverable


def recover_transaction(game: Path, root: Path) -> bool:
    transaction = root / "transaction"
    if transaction.is_symlink():
        raise PatchError("Installer transaction may not be a symbolic link.")
    if not transaction.exists():
        return False
    journal = transaction / "journal.json"
    if not journal.exists():
        # No journal means no destination write could have started.
        shutil.rmtree(transaction)
        return True
    record = read_json(journal)
    if (not isinstance(record, dict) or record.get("schema") != 1
            or type(record.get('had_state')) is not bool or not isinstance(record.get('changes'), list)
            or len(record['changes']) > 10000):
        raise PatchError("Invalid recovery journal; preserve the backup directory for manual recovery.")
    # Validate every rollback and the saved state before changing any game byte.
    # A corrupt later rollback must not leave an additional partial restore.
    old_state = transaction / "old-state.json"
    if record['had_state']:
        if (old_state.is_symlink() or not old_state.is_file()
                or not isinstance(record.get('state_sha256'), str)
                or not SHA256.fullmatch(record['state_sha256'])
                or digest(old_state) != record['state_sha256']):
            raise PatchError('Transaction state backup checksum mismatch.')
    seen = set()
    for index, change in enumerate(record['changes']):
        if not isinstance(change, dict) or type(change.get('existed')) is not bool:
            raise PatchError('Invalid recovery change entry.')
        relative = safe_relative(change.get('path'))
        if relative not in MANAGED_FILES or relative.casefold() in seen:
            raise PatchError('Unexpected or duplicate recovery path.')
        seen.add(relative.casefold())
        target = checked_path(game, relative)
        if target.exists() and not target.is_file():raise PatchError('Recovery target is not a regular file.')
        if change['existed']:
            rollback = transaction / 'rollback' / str(index)
            expected = change.get('previous_sha256')
            if ((transaction / 'rollback').is_symlink() or rollback.is_symlink() or not rollback.is_file()
                    or not isinstance(expected, str) or not SHA256.fullmatch(expected) or digest(rollback) != expected):
                raise PatchError('Transaction rollback checksum mismatch: ' + relative)
        if 'next_sha256' in change:
            expected_next = change['next_sha256']
            if expected_next is not None and (not isinstance(expected_next, str) or not SHA256.fullmatch(expected_next)):
                raise PatchError('Invalid staged recovery fingerprint.')
            current = digest(target) if target.is_file() else None
            previous = change.get('previous_sha256') if change['existed'] else None
            if current not in {previous, expected_next}:
                raise PatchError('Recovery target was changed by another program: ' + relative)
    for index, change in enumerate(record["changes"]):
        target = checked_path(game, change["path"])
        if change["existed"]:
            rollback = transaction / "rollback" / str(index)
            if not rollback.is_file() or digest(rollback) != change["previous_sha256"]:
                raise PatchError(f"Transaction rollback checksum mismatch: {change['path']}")
            target.parent.mkdir(parents=True, exist_ok=True)
            # Keep the rollback copy intact so recovery itself can be repeated.
            staged = transaction / f"recover-{index}"
            shutil.copy2(rollback, staged)
            os.replace(staged, target)
        elif target.exists():
            if not target.is_file():
                raise PatchError(f"Cannot remove unexpected recovery path: {target}")
            target.unlink()
    if record["had_state"]:
        staged = transaction / "restore-state.json"
        shutil.copy2(old_state, staged)
        os.replace(staged, root / "state.json")
    else:
        (root / "state.json").unlink(missing_ok=True)
    shutil.rmtree(transaction)
    return True


def commit_changes(game: Path, root: Path, changes: list[tuple[str, Path | None]], new_state: dict | None) -> None:
    """Stage everything, journal old bytes, replace files, then commit backup state.

    If interrupted between replacements, the next install/uninstall restores the
    previous state from the on-disk transaction before continuing.
    """
    transaction = root / "transaction"
    transaction.mkdir()
    (transaction / "staged").mkdir()
    (transaction / "rollback").mkdir()
    record = {"schema": 1, "changes": [], "had_state": (root / "state.json").exists()}
    try:
        if record["had_state"]:
            shutil.copy2(root / "state.json", transaction / "old-state.json")
            record["state_sha256"] = digest(transaction / "old-state.json")
        for index, (relative, source) in enumerate(changes):
            if relative not in MANAGED_FILES:
                raise PatchError("Unexpected patch destination.")
            target = checked_path(game, relative)
            existed = target.exists()
            item = {"path": relative, "existed": existed}
            if existed:
                if not target.is_file():
                    raise PatchError(f"Destination is not a regular file: {relative}")
                shutil.copy2(target, transaction / "rollback" / str(index))
                item["previous_sha256"] = digest(target)
            if source is not None:
                shutil.copyfile(source, transaction / "staged" / str(index))
                mode = stat.S_IMODE(target.stat().st_mode) if existed else 0o644
                os.chmod(transaction / "staged" / str(index), mode)
                item['next_sha256'] = digest(transaction / 'staged' / str(index))
            else:
                item['next_sha256'] = None
            record["changes"].append(item)
        atomic_json(transaction / "journal.json", record)
        for index, (relative, source) in enumerate(changes):
            if relative not in MANAGED_FILES:
                raise PatchError("Unexpected patch destination.")
            target = checked_path(game, relative)
            if source is None:
                target.unlink(missing_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(transaction / "staged" / str(index), target)
        if new_state is None:
            (root / "state.json").unlink(missing_ok=True)
        else:
            atomic_json(root / "state.json", new_state)
        # Mark completed before cleanup; a leftover completed transaction is safe.
        atomic_json(transaction / "committed.json", {"committed": True})
        shutil.rmtree(transaction)
    except BaseException:
        if (transaction / "committed.json").exists():
            # Game bytes and state are committed; only cleanup was interrupted.
            shutil.rmtree(transaction, ignore_errors=True)
        elif (transaction / "journal.json").exists():
            recover_transaction(game, root)
        else:
            shutil.rmtree(transaction, ignore_errors=True)
        raise


def install(game: Path, payload: Path, dry_run: bool = False, *, psp_patch: Path) -> dict:
    from psp_import import verified_archive
    from runtime_payload import materialize

    game = validate_game(game)
    manifest, recipe = load_payload(payload)
    # Authenticate user input before any recovery or install writes.
    try:
        archive = verified_archive(psp_patch)
    except (ValueError, OSError) as exc:
        raise PatchError(str(exc)) from exc
    recovery_root = ensure_state_dir(game, create=False)
    transaction = recovery_root / 'transaction'
    if transaction.exists() or transaction.is_symlink():
        if dry_run:raise PatchError('Interrupted transaction found. Run install without --dry-run to recover first.')
        with installer_lock(recovery_root):
            if transaction.is_symlink():raise PatchError('Installer transaction may not be a symbolic link.')
            committed = transaction / 'committed.json'
            if committed.is_symlink():raise PatchError('Installer transaction marker may not be a symbolic link.')
            if committed.is_file():shutil.rmtree(transaction)
            else:recover_transaction(game,recovery_root)
    validate_supported_source(game, manifest)
    root = ensure_state_dir(game, create=not dry_run)

    def apply_materialized(output):
        prepared, prepared_manifest, prepared_files = materialize(
            game, recipe, archive, manifest, Path(output) / 'generated')
        return plan_install(game, prepared, prepared_manifest, prepared_files,
                            root, load_state(root), dry_run)

    with tempfile.TemporaryDirectory(prefix="corpse-korean-resources-") as output:
        if dry_run:
            if (root / "transaction").exists():
                raise PatchError("Interrupted transaction found. Run install without --dry-run to recover first.")
            return apply_materialized(output)
        with installer_lock(root):
            if (root / "transaction/committed.json").is_file():
                shutil.rmtree(root / "transaction")
            else:
                recover_transaction(game, root)
            return apply_materialized(output)


def plan_install(game, payload, manifest, files, root, state, dry_run):
    supported_originals = {path: item.get("source_sha256", []) for path, item in files.items()}
    validate_state(game, root, state, allow_recoverable=True, supported_originals=supported_originals)
    previous = state["files"] if state else {}
    entries = {}
    changes = []
    backups = []
    for path, item in files.items():
        target = checked_path(game, path)
        # Parents must be directories before any writes are made.
        for parent in target.parents:
            if parent == game:
                break
            if parent.exists() and not parent.is_dir():
                raise PatchError(f"Destination parent is not a directory: {parent}")
        if target.exists() and not target.is_file():
            raise PatchError(f"Destination is not a regular file: {path}")
        if path in previous:
            entries[path] = {**previous[path], "installed_sha256": item["sha256"]}
            current = digest(target) if target.is_file() else None
            if (current != previous[path]["installed_sha256"] and
                    current != previous[path].get("original_sha256") and
                    current in item.get("source_sha256", [])):
                # The whole game already matched a supported source set. Keep
                # the exact original restored by Steam as this install's undo
                # target, while retaining the earlier content-addressed backup.
                entries[path]["original_sha256"] = current
                entries[path]["original_mode"] = stat.S_IMODE(target.stat().st_mode)
                backups.append((target, current))
        else:
            current = digest(target) if target.is_file() else None
            if current and current != item["sha256"] and current not in item.get("source_sha256", []):
                raise PatchError(f"Unsupported or modified original file: {path}. No game files were changed.")
            # A file with source hashes must exist: never synthesize a required original.
            if item.get("source_sha256") and current is None:
                raise PatchError(f"Required original game file is missing: {path}")
            entries[path] = {"installed_sha256": item["sha256"], "original_sha256": current,
                             "original_mode": stat.S_IMODE(target.stat().st_mode) if current else None}
            if current:
                backups.append((target, current))
        if not target.is_file() or digest(target) != item["sha256"]:
            changes.append((path, checked_path(payload / "files", path)))
    # Upgrades restore any files no longer shipped by the new version.
    for path, entry in previous.items():
        if path not in files:
            changes.append((path, backup_path(root, entry["original_sha256"]) if entry.get("original_sha256") else None))
    report = {"action": "install", "game_dir": str(game), "version": manifest["version"],
              "files": len(files), "changed_files": len(changes), "dry_run": dry_run}
    if dry_run:
        return report
    for source, sha in backups:
        backup = backup_path(root, sha)
        backup.parent.mkdir(exist_ok=True)
        if backup.exists() and digest(backup) != sha:
            raise PatchError("An existing original backup is corrupt.")
        if not backup.exists():
            shutil.copy2(source, backup)
            if digest(backup) != sha:
                raise PatchError("Original file changed while it was being backed up.")
    new_state = {"schema": 1, "patch_id": PATCH_ID, "app_id": APP_ID, "version": manifest["version"], "files": entries}
    commit_changes(game, root, changes, new_state)
    return report


def uninstall(game: Path, dry_run: bool = False) -> dict:
    game = validate_game(game)
    root = ensure_state_dir(game)
    if not root.exists():
        return {"action": "uninstall", "game_dir": str(game), "changed_files": 0, "installed": False, "dry_run": dry_run}
    def operation():
        state = load_state(root)
        validate_state(game, root, state, allow_recoverable=True)
        if not state:
            return {"action": "uninstall", "game_dir": str(game), "changed_files": 0, "installed": False, "dry_run": dry_run}
        changes = [(path, backup_path(root, entry["original_sha256"]) if entry.get("original_sha256") else None)
                   for path, entry in state["files"].items()]
        report = {"action": "uninstall", "game_dir": str(game), "changed_files": len(changes), "installed": True, "dry_run": dry_run}
        if not dry_run:
            commit_changes(game, root, changes, None)
            # Keep original backups after uninstall for recovery and auditability.
        return report
    if dry_run:
        if (root / "transaction").exists():
            raise PatchError("Interrupted transaction found. Run uninstall without --dry-run to recover first.")
        return operation()
    with installer_lock(root):
        if (root / "transaction/committed.json").is_file():
            shutil.rmtree(root / "transaction")
        else:
            recover_transaction(game, root)
        return operation()


def main(argv=None) -> int:
    # Redirected Windows streams may use a legacy code page. Keep Unicode
    # diagnostics and paths intact without replacing StringIO/mock streams.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if callable(reconfigure):
            reconfigure(encoding='utf-8', errors='backslashreplace')
    parser = argparse.ArgumentParser(description=GAME["title"] + " — 한국어 패치 복원·상태")
    parser.add_argument("action", choices=("uninstall", "status", "detect"))
    parser.add_argument("--game-dir", "--path", type=Path, help="게임 폴더 직접 지정 (생략하면 Steam 자동 검색)")
    parser.add_argument("--dry-run", action="store_true", help="파일을 변경하지 않고 설치/복원 가능 여부 확인")
    parser.add_argument("--json", action="store_true", help="기계가 읽을 수 있는 JSON 결과")
    args = parser.parse_args(argv)
    try:
        games = discover_games() if args.game_dir is None else []
        if args.action == "detect":
            result = games if args.game_dir is None else [{"path": str(validate_game(args.game_dir))}]
        else:
            if args.game_dir is None:
                if not games:
                    raise PatchError("게임을 찾지 못했습니다. Steam에서 설치한 뒤 다시 실행하거나 --game-dir \"게임 경로\"를 지정하세요.")
                if len(games) > 1:
                    raise PatchError("게임 설치가 여러 개입니다. --game-dir로 선택하세요:\n" + "\n".join(g["path"] for g in games))
                game = Path(games[0]["path"])
            else:
                game = validate_game(args.game_dir)
            if args.action == "uninstall":
                result = uninstall(game, args.dry_run)
            else:
                root = ensure_state_dir(game)
                if (root / "transaction").exists():
                    raise PatchError("중단된 설치 기록이 있습니다. install 또는 uninstall을 실행하여 복구하세요.")
                state = load_state(root)
                recoverable = validate_state(game, root, state, allow_recoverable=True)
                result = {"game_dir": str(game), "installed": state is not None, "version": state.get("version") if state else None,
                          "needs_reinstall": bool(recoverable), "missing_or_restored_files": recoverable}
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.action == "detect":
            print("\n".join(g["path"] for g in result) if result else "설치된 게임을 찾지 못했습니다.")
        elif args.action == "status":
            print(f"게임: {result['game_dir']}\n한글패치: " + (result["version"] if result["installed"] else "미설치") + (" (재설치 필요)" if result.get("needs_reinstall") else ""))
        else:
            print(f"복원 {'검사 완료' if args.dry_run else '완료'}: {result['game_dir']} ({result['changed_files']}개 파일)")
        return 0
    except (PatchError, OSError) as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        else:
            print(f"오류: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
