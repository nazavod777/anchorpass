"""Check shared data, fixtures and the signature contract."""

from __future__ import annotations

import base64
import json
from pathlib import Path

from anchorpass import AnchorpassError
from anchorpass.bundle import PREFIX, verify_bundle
from anchorpass.core import resolve_origin
from anchorpass.proxy import normalize_proxy
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    contract = json.loads((ROOT / "spec/contract.json").read_bytes())
    keys = json.loads((ROOT / "data/bundled/keys.json").read_bytes())
    envelope_raw = (ROOT / "data/bundled/bundle.json").read_bytes()
    bundle = verify_bundle(envelope_raw, keys)
    envelope = json.loads(envelope_raw)
    schema = json.loads((ROOT / "spec/bundle.schema.json").read_bytes())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(envelope)
    payload_schema = dict(schema, **schema["$defs"]["payload"])
    Draft202012Validator(payload_schema).validate(
        json.loads(base64.b64decode(envelope["payload"], validate=True))
    )
    assert contract["family"] == "recaptcha-v3/1"
    assert contract["signature_prefix"].encode() == PREFIX
    assert set(bundle.fields) == set(contract["field_keys"])
    vectors = json.loads((ROOT / "spec/vectors/proxies.json").read_bytes())
    for case in vectors:
        try:
            normalize_proxy(case["value"])
        except AnchorpassError as error:
            assert error.code == "InvalidProxy"
            assert not case["valid"]
        else:
            assert case["valid"]
    options_path = ROOT / "spec/vectors/solve-options.json"
    options = json.loads(options_path.read_bytes())
    for case in options["hostname"]:
        try:
            resolved = resolve_origin(case["co"], case["hostname"])
        except AnchorpassError as error:
            assert error.code == "InvalidInput"
            assert not case["valid"]
        else:
            assert case["valid"]
            assert resolved == case["expected_co"]
    for destination in ["go/testdata/solve-options.json", "rust/tests/data/solve-options.json"]:
        assert (ROOT / destination).read_bytes() == options_path.read_bytes(), destination
    print(f"PASS: contract, signed bundle, {len(vectors)} proxy vectors")
    print(f"PASS: {len(options['hostname'])} hostname vectors and standalone copies")


if __name__ == "__main__":
    main()
