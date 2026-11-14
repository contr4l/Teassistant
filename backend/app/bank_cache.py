"""Process-local cache for fully verified practice-bank snapshots.

The cache is deliberately conservative: if it cannot enumerate every evidence
root safely, the ordinary loader runs and its result is not retained.
"""

from __future__ import annotations

import json
import os
import stat
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

from app.bank_practice_contracts import PracticeBank

_MAX_ENTRIES = 4
_MAX_STABILITY_ATTEMPTS = 2

# Holding this lock across a load also provides singleflight for cold requests.
# These snapshots are small-user, process-local data, so a global lock is a
# useful tradeoff for simple and correct request coalescing.
_LOCK = threading.RLock()
_CACHE: OrderedDict[tuple[str, str], tuple[tuple[Any, ...], PracticeBank]] = OrderedDict()


def clear_bank_cache() -> None:
    """Discard all cached snapshots (also useful to isolate tests)."""
    with _LOCK:
        _CACHE.clear()


def _resolved_roots(bank_dir: Path, semantic_dir: Path) -> tuple[Path, Path]:
    return bank_dir.resolve(strict=True), semantic_dir.resolve(strict=True)


def _source_run_roots(semantic_root: Path) -> tuple[list[Path], bool]:
    """Return referenced run roots and whether their provenance is enumerable."""
    try:
        annotations = json.loads((semantic_root / "annotations.json").read_bytes())
        questions = annotations.get("questions") if isinstance(annotations, dict) else None
        if not isinstance(questions, dict):
            return [], False
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return [], False

    # Import on use: callers and tests can replace the canonical resolver, and
    # this module does not freeze an implementation detail at import time.
    from app import bank_practice

    roots: set[Path] = set()
    values: set[str] = set()
    for row in questions.values():
        if not isinstance(row, dict):
            return [], False
        provenance = row.get("provenance")
        if not isinstance(provenance, dict):
            return [], False
        value = provenance.get("source_run_path")
        if not isinstance(value, str) or not value:
            return [], False
        values.add(value)

    # Large annotation sets commonly share a single run. Validate every
    # provenance field above, but resolve and inspect each distinct path once.
    for value in values:
        try:
            run_root = bank_practice._resolve_source_run_path(semantic_root, value)
        except (KeyError, TypeError, ValueError, OSError):
            return [], False
        if _has_symlink_component(Path(value) if Path(value).is_absolute() else semantic_root.parent / value):
            return [], False
        roots.add(run_root)
    return sorted(roots, key=os.fspath), True


def _has_symlink_component(path: Path) -> bool:
    """Detect a link in the supplied path without following it."""
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                return True
        except FileNotFoundError:
            return True
    return False


def _root_signature(root: Path) -> tuple[tuple[Any, ...] | None, bool]:
    """Stat a tree without following links; return (signature, cacheable)."""
    entries: list[tuple[Any, ...]] = []
    pending = [root]
    try:
        while pending:
            directory = pending.pop()
            directory_stat = directory.lstat()
            if stat.S_ISLNK(directory_stat.st_mode) or not stat.S_ISDIR(directory_stat.st_mode):
                return None, False
            entries.append(_stat_record(root, directory, directory_stat))
            with os.scandir(directory) as iterator:
                children = sorted(iterator, key=lambda entry: entry.name)
            for child in children:
                child_path = Path(child.path)
                child_stat = child.stat(follow_symlinks=False)
                entries.append(_stat_record(root, child_path, child_stat))
                if stat.S_ISLNK(child_stat.st_mode):
                    return None, False
                if stat.S_ISDIR(child_stat.st_mode):
                    pending.append(child_path)
        entries.sort(key=lambda item: item[0])
        return tuple(entries), True
    except OSError:
        return None, False


def _stat_record(root: Path, path: Path, value: os.stat_result) -> tuple[Any, ...]:
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        relative = "."
    return (
        relative,
        stat.S_IFMT(value.st_mode),
        stat.S_IMODE(value.st_mode),
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _fingerprint(bank_root: Path, semantic_root: Path) -> tuple[tuple[Any, ...] | None, bool]:
    run_roots, provenance_known = _source_run_roots(semantic_root)
    if not provenance_known:
        return None, False
    roots = [bank_root, semantic_root, *run_roots]
    tree_signatures: list[tuple[Any, ...]] = []
    for root in sorted(set(roots), key=os.fspath):
        signature, cacheable = _root_signature(root)
        if not cacheable or signature is None:
            return None, False
        tree_signatures.append((os.fspath(root), signature))
    return tuple(tree_signatures), True


def _clone(bank: PracticeBank) -> PracticeBank:
    return bank.model_copy(deep=True)


def load_cached_practice_bank(
    bank_dir: Path, semantic_dir: Path, *, force: bool = False
) -> PracticeBank:
    """Load a verified bank, reusing it only while all dependency stats match."""
    # Keep import dynamic for test instrumentation and to preserve the canonical
    # full-loader behavior as it evolves.
    from app import bank_practice

    with _LOCK:
        supplied_bank_root = Path(bank_dir)
        supplied_semantic_root = Path(semantic_dir)
        roots_have_symlink = _has_symlink_component(supplied_bank_root) or _has_symlink_component(
            supplied_semantic_root
        )
        bank_root, semantic_root = _resolved_roots(bank_dir, semantic_dir)
        key = (os.fspath(bank_root), os.fspath(semantic_root))
        if force:
            # A failed explicit validation must not leave an older success
            # available to a later ordinary request.
            _CACHE.pop(key, None)

        for attempt in range(_MAX_STABILITY_ATTEMPTS):
            before, cacheable = _fingerprint(bank_root, semantic_root)
            cacheable = cacheable and not roots_have_symlink
            if cacheable and not force:
                cached = _CACHE.get(key)
                if cached is not None and cached[0] == before:
                    after, still_cacheable = _fingerprint(bank_root, semantic_root)
                    if still_cacheable and after == before:
                        _CACHE.move_to_end(key)
                        return _clone(cached[1])
                    _CACHE.pop(key, None)

            # Unenumerable dependencies still get the authoritative full load,
            # but are never retained for a later request.
            loaded = bank_practice.load_practice_bank(bank_root, semantic_root)
            after, after_cacheable = _fingerprint(bank_root, semantic_root)
            if not cacheable or not after_cacheable:
                _CACHE.pop(key, None)
                return _clone(loaded)
            if before != after:
                _CACHE.pop(key, None)
                if attempt + 1 < _MAX_STABILITY_ATTEMPTS:
                    continue
                raise ValueError("practice bank changed during validation")

            # `loaded` remains private; every value crossing this API boundary
            # is cloned, so retaining it avoids an unnecessary cold-path copy.
            _CACHE[key] = (after, loaded)
            _CACHE.move_to_end(key)
            while len(_CACHE) > _MAX_ENTRIES:
                _CACHE.popitem(last=False)
            return _clone(loaded)

        raise ValueError("practice bank changed during validation")
