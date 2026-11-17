from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from anchorpass import AnchorpassError, AsyncClient, Client

ANCHOR = "https://www.google.com/recaptcha/api2/anchor?k=test&co=origin"


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("expired", [False, True])
@pytest.mark.parametrize("failure", ["lock", "replace"])
async def test_persistence_failure_retains_floor_and_requires_recovery(
    origin, signer, tmp_path, monkeypatch, asynchronous, expired, failure
):
    url, state = origin
    keys, sign = signer
    path = tmp_path / "bundle.json"
    path.write_bytes(sign("2"))
    candidate = sign("3", issued_at=1, expires=2) if expired else sign("3")
    state["bundle"] = candidate
    client = (AsyncClient if asynchronous else Client)(
        bundle_url=url + "/bundle", trusted_keys=keys, cache_path=path
    )
    client._origin = url

    async def invoke(name, *args):
        method = getattr(client, name)
        return await method(*args) if asynchronous else await asyncio.to_thread(method, *args)

    try:
        with monkeypatch.context() as patch:
            if failure == "lock":
                path.with_name(path.name + ".lock").mkdir()
            else:

                def fail_replace(source, destination):
                    raise OSError("Synthetic cache write failure")

                patch.setattr("anchorpass.bundle.os.replace", fail_replace)
            with pytest.raises(AnchorpassError, match="ConfigUnavailable"):
                await invoke("solve", ANCHOR)
            assert client._store.current.revision == "3"
            assert path.read_bytes() != candidate
            assert all(request[1] == "/bundle" for request in state["requests"])
        if failure == "lock":
            path.with_name(path.name + ".lock").rmdir()
        state["bundle"] = sign("2")
        with pytest.raises(AnchorpassError, match="ConfigRollback"):
            await invoke("refresh")
        state["bundle"] = candidate
        if expired:
            with pytest.raises(AnchorpassError, match="ConfigExpired"):
                await invoke("refresh")
        else:
            assert (await invoke("refresh")).revision == "3"
        assert path.read_bytes() == candidate
        state["bundle"] = sign("4")
        assert (await invoke("refresh")).revision == "4"
        assert (await invoke("solve", ANCHOR)).bundle_revision == "4"
    finally:
        await client.close() if asynchronous else client.close()


def test_invalid_signature_does_not_raise_floor_when_cache_is_unwritable(origin, signer, tmp_path):
    url, state = origin
    keys, sign = signer
    path: Path = tmp_path / "bundle.json"
    path.write_bytes(sign("2"))
    path.with_name(path.name + ".lock").mkdir()
    invalid = json.loads(sign("9"))
    invalid["signature"] = "AA=="
    state["bundle"] = json.dumps(invalid).encode()
    with Client(bundle_url=url + "/bundle", trusted_keys=keys, cache_path=path) as client:
        assert client.prepare().revision == "2"


@pytest.mark.parametrize("asynchronous", [False, True])
def test_concurrent_cache_read_cannot_undo_a_failed_newer_persistence(
    signer, tmp_path, monkeypatch, asynchronous
):
    keys, sign = signer
    path = tmp_path / "bundle.json"
    cached = sign("2")
    path.write_bytes(cached)
    client = (AsyncClient if asynchronous else Client)(
        trusted_keys=keys, cache_path=path, offline=True
    )
    client._store.load_cache()
    compared = threading.Event()
    release_reader = threading.Event()
    compare = client._store._compare

    def pause_cache_reader(candidate, current):
        compare(candidate, current)
        if threading.current_thread().name.startswith("cache-reader"):
            compared.set()
            assert release_reader.wait(3)

    def prepare():
        return asyncio.run(client.prepare()) if asynchronous else client.prepare()

    def fail_replace(source, destination):
        raise OSError("Synthetic concurrent cache failure")

    def attempt(operation):
        try:
            result = operation()
            return result.revision if result is not None else None
        except AnchorpassError as error:
            return error.code

    monkeypatch.setattr(client._store, "_compare", pause_cache_reader)
    monkeypatch.setattr("anchorpass.bundle.os.replace", fail_replace)
    try:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="cache-reader") as readers:
            stale = readers.submit(lambda: attempt(prepare))
            try:
                assert compared.wait(3)
                # The reader must serialize its compare-and-publish with acceptance.
                unlocked = client._guard.acquire(blocking=False)
                if unlocked:
                    client._guard.release()
                assert not unlocked
                with ThreadPoolExecutor(max_workers=1) as writers:
                    newer = writers.submit(
                        lambda: attempt(lambda: client._accept_update(sign("3")))
                    )
                    release_reader.set()
                    assert stale.result(timeout=3) in {"2", "ConfigUnavailable"}
                    assert newer.result(timeout=3) == "ConfigUnavailable"
            finally:
                release_reader.set()
        assert client._store.current.revision == "3"
        assert attempt(lambda: client._accept_update(sign("2"))) == "ConfigRollback"
        assert attempt(prepare) == "ConfigUnavailable"
        assert path.read_bytes() == cached
    finally:
        release_reader.set()
        asyncio.run(client.close()) if asynchronous else client.close()
