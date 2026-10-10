"""Pick the OpenAPI tasks: APIs-guru OpenAPI 3.0 contracts in a seeded random order, kept when small and checkable,
and only when a generic reference server built from the contract passes the grade and an empty app fails it.

    python experiments/abtest/openapi_select.py 20        # writes experiments/openapi/data/specs/<name>.json
"""
from __future__ import annotations

import json
import random
import sys
import tempfile
import urllib.request
from pathlib import Path

import openapi_task as ot

SEED = 20261010
MAX_BYTES = 150_000

# The reference server: every operation answers with a value generated once from its first 2xx JSON schema.
REFERENCE = r'''
import json, pathlib
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from hypothesis import HealthCheck, given, settings
from hypothesis_jsonschema import from_schema

SPEC = json.loads(pathlib.Path(__file__).with_name("openapi.json").read_text(encoding="utf-8"))
DROP = {"example", "examples", "xml", "discriminator", "readOnly", "writeOnly", "deprecated", "externalDocs"}

def resolve(node, depth=0):
    if depth > 12:
        return {}
    if isinstance(node, dict):
        if "$ref" in node:
            target = SPEC
            for part in node["$ref"].lstrip("#/").split("/"):
                target = target[part]
            return resolve(target, depth + 1)
        out = {k: resolve(v, depth + 1) for k, v in node.items() if k not in DROP}
        if out.pop("nullable", False):
            out = {"anyOf": [out, {"type": "null"}]}
        return out
    if isinstance(node, list):
        return [resolve(x, depth + 1) for x in node]
    return node

def one(schema):
    found = []
    @settings(max_examples=1, database=None, suppress_health_check=list(HealthCheck), derandomize=True)
    @given(from_schema(schema))
    def pick(v):
        found.append(v)
    pick()
    return found[0]

app = FastAPI()
for path, item in SPEC["paths"].items():
    for method, op in item.items():
        if method not in ("get", "post", "put", "patch", "delete"):
            continue
        code, schema = None, None
        for c, resp in sorted(op.get("responses", {}).items()):
            content = resolve(resp).get("content", {}) if isinstance(resp, dict) else {}
            if str(c).startswith("2") and "application/json" in content:
                code, schema = int(c), content["application/json"].get("schema", {})
                break
        body = one(schema)
        def handler(request: Request, _b=body, _c=code):
            return JSONResponse(_b, status_code=_c)
        app.add_api_route(path, handler, methods=[method.upper()])
'''


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "stealthlab-abtest/1.0 (+openapi task selection)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read(MAX_BYTES + 1)


def eligible(spec: dict) -> bool:
    if not str(spec.get("openapi", "")).startswith("3.0"):
        return False
    ops = ot.operations(spec)
    if not 2 <= len(ops) <= 5:
        return False
    if '"$ref": "http' in json.dumps(spec) or "'$ref': 'http" in json.dumps(spec):
        return False
    for p, m in ops:
        op = spec["paths"][p][m]
        params = op.get("parameters", []) + spec["paths"][p].get("parameters", [])
        if any(isinstance(x, dict) and x.get("in") not in ("path", "query") and "$ref" not in x for x in params):
            return False
        body = op.get("requestBody")
        if body and "$ref" not in body and set(body.get("content", {})) - {"application/json"}:
            return False
        if not any(str(c).startswith("2") and isinstance(r, dict) and "application/json" in r.get("content", {})
                   for c, r in op.get("responses", {}).items()):
            return False
    return True


def verified(spec: dict) -> bool:
    with tempfile.TemporaryDirectory(prefix="oas_ref_") as tmp:
        Path(tmp, "openapi.json").write_text(json.dumps(spec), encoding="utf-8")
        Path(tmp, "app.py").write_text(REFERENCE, encoding="utf-8")
        ok, out = ot.fuzz(Path(tmp), spec, **ot.HIDDEN)
        if not ok:
            return False
        Path(tmp, "app.py").write_text(ot.STUB, encoding="utf-8")
        bad, _ = ot.fuzz(Path(tmp), spec, **ot.VISIBLE)
        return not bad


def main(n: int) -> None:
    listing = json.loads((ot.DATA / "list.json").read_text(encoding="utf-8"))
    names = sorted(listing)
    random.Random(SEED).shuffle(names)
    ot.SPECS.mkdir(parents=True, exist_ok=True)
    kept = len(list(ot.SPECS.glob("*.json")))
    for name in names:
        if kept >= n:
            break
        v = listing[name]["versions"][listing[name]["preferred"]]
        if not str(v.get("openapiVer", "")).startswith("3.0"):
            continue
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name)[:60]
        if (ot.SPECS / f"{safe}.json").exists():
            continue
        try:
            raw = fetch(v["swaggerUrl"])
            if len(raw) > MAX_BYTES:
                continue
            spec = json.loads(raw)
        except Exception:  # noqa: BLE001 -- an unreachable or malformed spec is skipped
            continue
        if not eligible(spec):
            continue
        spec["servers"] = [{"url": "/"}]
        try:
            ok = verified(spec)
        except Exception as e:  # noqa: BLE001
            print(f"skip {name}: {type(e).__name__}", flush=True)
            continue
        print(f"{'KEEP' if ok else 'drop'} {name} ({len(ot.operations(spec))} ops)", flush=True)
        if ok:
            (ot.SPECS / f"{safe}.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
            kept += 1
    print(f"kept {kept}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 20)
