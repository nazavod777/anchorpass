from __future__ import annotations

import pytest

from anchorpass import AnchorpassError, AsyncClient, Client
from anchorpass.bundle import BundleStore


@pytest.mark.parametrize("persistent", [False, True])
def test_expired_remote_revision_sets_a_floor(origin, signer, tmp_path, persistent):
    url, state = origin
    keys, sign = signer
    cache_path = tmp_path / "cache.json" if persistent else None
    options = {"trusted_keys": keys, "cache_path": cache_path}
    with Client(bundle_url=url + "/bundle", **options) as client:
        state["bundle"] = sign("1")
        assert client.prepare().revision == "1"
        expired = sign("3", issued_at=1, expires=2)
        state["bundle"] = expired
        with pytest.raises(AnchorpassError, match="ConfigExpired"):
            client.refresh()
        if persistent:
            assert cache_path.read_bytes() == expired
        state["bundle"] = sign("2")
        with pytest.raises(AnchorpassError, match="ConfigRollback"):
            client.refresh()
        with pytest.raises(AnchorpassError, match="ConfigExpired"):
            client.prepare()
    if persistent:
        with Client(offline=True, **options) as client:
            with pytest.raises(AnchorpassError, match="ConfigExpired"):
                client.prepare()
            with pytest.raises(AnchorpassError, match="ConfigRollback"):
                client._store.accept(sign("2"))


async def test_async_expired_remote_revision_sets_a_floor(origin, signer):
    url, state = origin
    keys, sign = signer
    async with AsyncClient(bundle_url=url + "/bundle", trusted_keys=keys) as client:
        state["bundle"] = sign("1")
        assert (await client.prepare()).revision == "1"
        state["bundle"] = sign("3", issued_at=1, expires=2)
        with pytest.raises(AnchorpassError, match="ConfigExpired"):
            await client.refresh()
        state["bundle"] = sign("2")
        with pytest.raises(AnchorpassError, match="ConfigRollback"):
            await client.refresh()


def test_pin_cannot_disable_a_verified_floor_and_invalid_signatures_cannot_raise_it(signer):
    keys, sign = signer
    store = BundleStore(keys=keys, pin="1")
    store.accept(sign("1"))
    with pytest.raises(AnchorpassError, match="ConfigUnavailable"):
        store.accept(sign("3"))
    with pytest.raises(AnchorpassError, match="ConfigRollback"):
        store.accept(sign("1"))
    with pytest.raises(AnchorpassError, match="ConfigInvalid"):
        store.accept(sign("9").replace(b'"signature": "', b'"signature": "bad'))
    assert store.current.revision == "3"
