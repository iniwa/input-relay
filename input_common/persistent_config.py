"""Durable, per-user JSON settings shared by Input Relay entry points."""

import json
import os
from pathlib import Path
import tempfile
import time


CONFIG_FILENAMES = (
    "config.json",
    "sender_config.json",
    "presets.json",
    "layout_presets.json",
)
BACKUP_LIMIT = 5


def get_config_root():
    """Return the per-user settings directory without consulting the checkout."""
    override = os.environ.get("INPUT_RELAY_CONFIG_DIR")
    if override:
        # Deliberately do not expand or reinterpret an explicit override.
        return Path(override)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "InputRelay"
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home) / "input-relay"
    return Path.home() / ".config" / "input-relay"


def migrate_legacy_configs(legacy_config_dir, config_root=None):
    """Copy known legacy settings once, without changing their source files."""
    legacy_config_dir = Path(legacy_config_dir)
    config_root = Path(config_root) if config_root is not None else get_config_root()
    for filename in CONFIG_FILENAMES:
        source = legacy_config_dir / filename
        destination = config_root / filename
        if source.is_file() and not destination.exists():
            _atomic_copy_if_missing(source, destination)
    return config_root


def load_object_json(path, default, *, legacy_config_dir=None):
    """Load an object JSON file, recovering the newest valid backup if needed."""
    path = Path(path)
    loaded = _read_object(path)
    if loaded is not None:
        return loaded
    for backup in _backup_paths(path):
        loaded = _read_object(backup)
        if loaded is not None:
            _atomic_copy(backup, path)
            return loaded
    if legacy_config_dir is not None:
        migrate_legacy_configs(legacy_config_dir, path.parent)
        loaded = _read_object(path)
        if loaded is not None:
            return loaded
    return default


def write_object_json(path, data, *, legacy_config_dir=None):
    """Atomically replace an object JSON file while retaining bounded history."""
    if not isinstance(data, dict):
        raise ValueError("persistent settings must be JSON objects")
    path = Path(path)
    if legacy_config_dir is not None:
        migrate_legacy_configs(legacy_config_dir, path.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = _backup_dir(path) / f"{path.name}.{time.time_ns()}.bak"
        _atomic_copy(path, backup)
        _prune_backups(path)
    _atomic_write_bytes(path, json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"))


def _read_object(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _backup_paths(path):
    return sorted(
        _backup_dir(path).glob(f"{path.name}.*.bak"),
        key=lambda candidate: candidate.stat().st_mtime_ns,
        reverse=True,
    )


def _prune_backups(path):
    application_backups = [
        backup for backup in _backup_paths(path)
        if backup.name != f"{path.name}.initial.bak"
    ]
    for backup in application_backups[BACKUP_LIMIT:]:
        try:
            backup.unlink()
        except OSError:
            pass


def _backup_dir(path):
    return Path(path).parent / "backups"


def _atomic_copy(source, destination):
    _atomic_write_bytes(destination, Path(source).read_bytes())


def _atomic_copy_if_missing(source, destination):
    """Atomically create a migrated file, losing harmlessly to another writer."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=str(destination.parent), prefix=f".{destination.name}.", suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(Path(source).read_bytes())
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        try:
            # A hard link is an atomic create: unlike replace(), it can never
            # overwrite a setting another process has already made persistent.
            os.link(temporary, destination)
        except FileExistsError:
            pass
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass


def _atomic_write_bytes(destination, contents):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=str(destination.parent), prefix=f".{destination.name}.", suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(contents)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.replace(temporary, destination)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
