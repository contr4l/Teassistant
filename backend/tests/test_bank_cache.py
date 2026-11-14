from __future__ import annotations

import concurrent.futures
import os
import shutil
import threading
import time

from app import bank_practice
from app.bank_cache import clear_bank_cache, load_cached_practice_bank
from test_bank_practice import _fixture


def _count_full_loads(monkeypatch):
    original = bank_practice.load_practice_bank
    calls = 0
    lock = threading.Lock()

    def counted(*args, **kwargs):
        nonlocal calls
        with lock:
            calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(bank_practice, "load_practice_bank", counted)
    return lambda: calls


def test_hot_read_skips_full_loader_and_returns_independent_models(tmp_path, monkeypatch):
    bank_dir, semantic_dir, _, _ = _fixture(tmp_path)
    clear_bank_cache()
    count = _count_full_loads(monkeypatch)

    first = load_cached_practice_bank(bank_dir, semantic_dir)
    assert count() == 1
    second = load_cached_practice_bank(bank_dir, semantic_dir)
    assert count() == 1
    first.questions[0].source_payload["id"] = "poisoned"
    assert second.questions[0].source_payload["id"] == "pair-1-q1"
    assert load_cached_practice_bank(bank_dir, semantic_dir).questions[0].id == "pair-1-q1"
    assert count() == 1


def test_media_change_invalidates_cache(tmp_path, monkeypatch):
    bank_dir, semantic_dir, _, _ = _fixture(tmp_path)
    clear_bank_cache()
    count = _count_full_loads(monkeypatch)
    load_cached_practice_bank(bank_dir, semantic_dir)
    (bank_dir / "media/doc-original/image1.png").write_bytes(b"changed-media")

    result = load_cached_practice_bank(bank_dir, semantic_dir)
    assert count() == 2
    assert result.questions[0].status == "blocked"
    assert "source_media_missing_or_mismatched" in result.questions[0].block_reasons


def test_semantic_evidence_change_invalidates_cache(tmp_path, monkeypatch):
    bank_dir, semantic_dir, _, _ = _fixture(tmp_path)
    clear_bank_cache()
    count = _count_full_loads(monkeypatch)
    load_cached_practice_bank(bank_dir, semantic_dir)
    evidence = tmp_path / "source-run/calls/0001/result.json"
    evidence.write_bytes(evidence.read_bytes() + b" ")

    result = load_cached_practice_bank(bank_dir, semantic_dir)
    assert count() == 2
    assert result.questions[0].status == "blocked"
    assert "semantic_call_digest_mismatch" in result.questions[0].block_reasons


def test_permissions_change_invalidates_cache(tmp_path, monkeypatch):
    bank_dir, semantic_dir, _, _ = _fixture(tmp_path)
    clear_bank_cache()
    count = _count_full_loads(monkeypatch)
    load_cached_practice_bank(bank_dir, semantic_dir)
    media = bank_dir / "media/doc-original/image1.png"
    media.chmod(media.stat().st_mode & ~0o111)

    load_cached_practice_bank(bank_dir, semantic_dir)
    assert count() == 2


def test_same_size_rewrite_with_restored_mtime_invalidates_via_ctime(tmp_path, monkeypatch):
    bank_dir, semantic_dir, _, _ = _fixture(tmp_path)
    clear_bank_cache()
    count = _count_full_loads(monkeypatch)
    load_cached_practice_bank(bank_dir, semantic_dir)
    media = bank_dir / "media/doc-original/image1.png"
    original = media.read_bytes()
    before = media.stat()
    time.sleep(0.002)
    media.write_bytes(b"X" * len(original))
    os.utime(media, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert media.stat().st_mtime_ns == before.st_mtime_ns
    assert media.stat().st_ctime_ns != before.st_ctime_ns

    load_cached_practice_bank(bank_dir, semantic_dir)
    assert count() == 2


def test_directory_replacement_invalidates_cache(tmp_path, monkeypatch):
    bank_dir, semantic_dir, _, _ = _fixture(tmp_path)
    clear_bank_cache()
    count = _count_full_loads(monkeypatch)
    load_cached_practice_bank(bank_dir, semantic_dir)
    replacement = tmp_path / "bank-replacement"
    shutil.copytree(bank_dir, replacement)
    old = tmp_path / "bank-old"
    bank_dir.rename(old)
    replacement.rename(bank_dir)

    load_cached_practice_bank(bank_dir, semantic_dir)
    assert count() == 2


def test_symlinked_dependency_tree_is_full_loaded_each_time(tmp_path, monkeypatch):
    bank_dir, semantic_dir, _, _ = _fixture(tmp_path)
    media_dir = bank_dir / "media/doc-original"
    real_media = bank_dir / "media/doc-original-real"
    media_dir.rename(real_media)
    media_dir.symlink_to(real_media, target_is_directory=True)
    clear_bank_cache()
    count = _count_full_loads(monkeypatch)

    assert load_cached_practice_bank(bank_dir, semantic_dir).questions[0].status == "eligible"
    assert load_cached_practice_bank(bank_dir, semantic_dir).questions[0].status == "eligible"
    assert count() == 2


def test_symlinked_root_is_full_loaded_each_time(tmp_path, monkeypatch):
    bank_dir, semantic_dir, _, _ = _fixture(tmp_path)
    bank_alias = tmp_path / "bank-alias"
    bank_alias.symlink_to(bank_dir, target_is_directory=True)
    clear_bank_cache()
    count = _count_full_loads(monkeypatch)

    assert load_cached_practice_bank(bank_alias, semantic_dir).questions[0].status == "eligible"
    assert load_cached_practice_bank(bank_alias, semantic_dir).questions[0].status == "eligible"
    assert count() == 2


def test_force_always_runs_full_validation(tmp_path, monkeypatch):
    bank_dir, semantic_dir, _, _ = _fixture(tmp_path)
    clear_bank_cache()
    count = _count_full_loads(monkeypatch)
    load_cached_practice_bank(bank_dir, semantic_dir)
    load_cached_practice_bank(bank_dir, semantic_dir)
    load_cached_practice_bank(bank_dir, semantic_dir, force=True)
    assert count() == 2


def test_failed_forced_validation_evicts_previous_success(tmp_path, monkeypatch):
    bank_dir, semantic_dir, _, _ = _fixture(tmp_path)
    clear_bank_cache()
    original = bank_practice.load_practice_bank
    load_cached_practice_bank(bank_dir, semantic_dir)

    def failing(*args, **kwargs):
        raise OSError("synthetic forced read failure")

    monkeypatch.setattr(bank_practice, "load_practice_bank", failing)
    try:
        load_cached_practice_bank(bank_dir, semantic_dir, force=True)
    except OSError as exc:
        assert "synthetic forced read failure" in str(exc)
    else:
        raise AssertionError("forced validation should preserve loader failure")

    # The next ordinary request must consult the loader too; there is no
    # negative cache and the prior success was evicted before force validation.
    try:
        load_cached_practice_bank(bank_dir, semantic_dir)
    except OSError as exc:
        assert "synthetic forced read failure" in str(exc)
    else:
        raise AssertionError("old cached success must not hide a failed recheck")
    monkeypatch.setattr(bank_practice, "load_practice_bank", original)


def test_concurrent_cold_requests_share_one_full_load(tmp_path, monkeypatch):
    bank_dir, semantic_dir, _, _ = _fixture(tmp_path)
    clear_bank_cache()
    original = bank_practice.load_practice_bank
    calls = 0
    lock = threading.Lock()

    def slow_counted(*args, **kwargs):
        nonlocal calls
        with lock:
            calls += 1
        time.sleep(0.03)
        return original(*args, **kwargs)

    monkeypatch.setattr(bank_practice, "load_practice_bank", slow_counted)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: load_cached_practice_bank(bank_dir, semantic_dir), range(8)))
    assert calls == 1
    assert all(result.questions[0].status == "eligible" for result in results)


def test_mutation_during_full_load_retries_before_caching(tmp_path, monkeypatch):
    bank_dir, semantic_dir, _, _ = _fixture(tmp_path)
    clear_bank_cache()
    original = bank_practice.load_practice_bank
    media = bank_dir / "media/doc-original/image1.png"
    calls = 0

    def mutate_after_first_read(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = original(*args, **kwargs)
        if calls == 1:
            media.write_bytes(b"mutation during validation")
        return result

    monkeypatch.setattr(bank_practice, "load_practice_bank", mutate_after_first_read)
    result = load_cached_practice_bank(bank_dir, semantic_dir)
    assert calls == 2
    assert result.questions[0].status == "blocked"
    assert "source_media_missing_or_mismatched" in result.questions[0].block_reasons


def test_repeated_mutation_during_load_fails_after_bounded_retry(tmp_path, monkeypatch):
    bank_dir, semantic_dir, _, _ = _fixture(tmp_path)
    clear_bank_cache()
    original = bank_practice.load_practice_bank
    media = bank_dir / "media/doc-original/image1.png"
    calls = 0

    def mutate_each_time(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = original(*args, **kwargs)
        media.write_bytes(f"mutation-{calls}".encode())
        return result

    monkeypatch.setattr(bank_practice, "load_practice_bank", mutate_each_time)
    try:
        load_cached_practice_bank(bank_dir, semantic_dir)
    except ValueError as exc:
        assert "changed during validation" in str(exc)
    else:
        raise AssertionError("unstable dependencies must fail after bounded retry")
    assert calls == 2


def test_lru_is_bounded_to_four_roots(tmp_path, monkeypatch):
    clear_bank_cache()
    count = _count_full_loads(monkeypatch)
    roots = []
    for index in range(5):
        fixture_root = tmp_path / f"fixture-{index}"
        fixture_root.mkdir()
        roots.append(_fixture(fixture_root)[:2])
    for roots_pair in roots:
        load_cached_practice_bank(*roots_pair)
    assert count() == 5
    load_cached_practice_bank(*roots[0])
    assert count() == 6
    load_cached_practice_bank(*roots[4])
    assert count() == 6


def test_malformed_provenance_is_loaded_but_never_cached(tmp_path, monkeypatch):
    bank_dir, semantic_dir, _, _ = _fixture(tmp_path)
    annotations = semantic_dir / "annotations.json"
    import json

    payload = json.loads(annotations.read_text())
    payload["questions"]["pair-1-q1"]["provenance"]["source_run_path"] = "../outside"
    annotations.write_text(json.dumps(payload))
    clear_bank_cache()
    count = _count_full_loads(monkeypatch)

    # The full loader's normal contract turns unsafe evidence into a blocked
    # row. The cache must not retain that result because the dependency set is
    # not safely enumerable.
    first = load_cached_practice_bank(bank_dir, semantic_dir)
    second = load_cached_practice_bank(bank_dir, semantic_dir)
    assert first.questions[0].status == "blocked"
    assert second.questions[0].status == "blocked"
    assert count() == 2
