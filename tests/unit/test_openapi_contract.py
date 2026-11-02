"""
Interoperability — the public HTTP contract of the backend, as published in
`/openapi.json`, is a standard OpenAPI 3 document and does not drift silently.

Catalog test implemented (`interoperability`): "standards and schema validation".

The snapshot (`snapshots/openapi_public.json`) records only the semantic parts
of the contract the frontend relies on — paths, methods, parameters, request
bodies and success status codes of the public (non-admin) endpoints — so it does
not depend on how a given FastAPI release renders its own helper schemas.
A deliberate contract change is recorded by regenerating the snapshot:

    UPDATE_OPENAPI_SNAPSHOT=1 pytest tests/unit/test_openapi_contract.py
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main as app_module

pytestmark = pytest.mark.qa_interoperability

_SNAPSHOT = Path(__file__).parent / "snapshots" / "openapi_public.json"
_METHODS = ("get", "post", "put", "patch", "delete")


@pytest.fixture(scope="module")
def spec() -> dict:
    r = TestClient(app_module.app).get("/openapi.json")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    return r.json()


def _public_contract(spec: dict) -> dict:
    contract: dict = {}
    for path, item in sorted(spec["paths"].items()):
        if path.startswith("/api/admin"):
            continue
        for method in _METHODS:
            op = item.get(method)
            if op is None:
                continue
            body = op.get("requestBody")
            contract[f"{method.upper()} {path}"] = {
                "parameters": sorted(
                    ({"name": p["name"], "in": p["in"], "required": bool(p.get("required"))}
                     for p in op.get("parameters", [])),
                    key=lambda p: (p["in"], p["name"]),
                ),
                "requestBody": None if body is None else {
                    "required": bool(body.get("required")),
                    "content": sorted(body.get("content", {})),
                },
                "successCodes": sorted(c for c in op.get("responses", {}) if c.startswith("2")),
                "tags": op.get("tags", []),
            }
    return contract


def test_openapi_document_is_valid_openapi_3(spec):
    assert re.fullmatch(r"3\.\d+\.\d+", spec["openapi"])
    assert spec["info"]["title"] == "Vento API"
    assert spec["info"]["version"]
    assert spec["paths"]
    for path, item in spec["paths"].items():
        assert path.startswith("/api/"), path
        for method, op in item.items():
            assert method in _METHODS, (path, method)
            assert op.get("responses"), (path, method)
            assert op.get("operationId"), (path, method)
            # path templates and declared path parameters agree
            templated = set(re.findall(r"{(\w+)}", path))
            declared = {p["name"] for p in op.get("parameters", []) if p["in"] == "path"}
            assert templated == declared, (path, method)
    ids = [op["operationId"] for item in spec["paths"].values() for op in item.values()]
    assert len(ids) == len(set(ids)), "operationId must be unique"


def test_every_schema_reference_resolves(spec):
    refs = set(re.findall(r'"\$ref":\s*"([^"]+)"', json.dumps(spec)))
    for ref in refs:
        assert ref.startswith("#/"), ref
        node = spec
        for part in ref[2:].split("/"):
            assert part in node, f"dangling $ref {ref}"
            node = node[part]


def test_debug_endpoints_are_not_published(spec):
    assert not [p for p in spec["paths"] if p.startswith("/api/_debug")]


def test_public_contract_matches_snapshot(spec):
    contract = _public_contract(spec)
    if os.getenv("UPDATE_OPENAPI_SNAPSHOT") == "1":
        _SNAPSHOT.parent.mkdir(exist_ok=True)
        _SNAPSHOT.write_text(json.dumps(contract, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")
    expected = json.loads(_SNAPSHOT.read_text(encoding="utf-8"))
    assert sorted(contract) == sorted(expected), "public endpoints added or removed"
    for key in expected:
        assert contract[key] == expected[key], key
