"""Write the tool router's committed data: app/tool_routing/data/<name>.json.gz from the plain files the scripts write.

    python scripts/pack_tool_data.py

The registry snapshot, the expansions and the stars go as they are. The liveness probe is cut to what a card reads
(app/tool_routing/catalog.py card_from_registry: the status, the tool count, and each live server's first 40 tools'
names with 160 characters of description; a dead server keeps its status only): 92 MB -> a few, and the server's
loading peak with it. The vectors are never committed (89 MB; scripts/build_tool_vectors.py rebuilds them).
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "app" / "tool_routing" / "data"
PLAIN = ("mcp_registry.json", "registry_expansions.json", "registry_stars.json")


def slim_probe(probe: dict) -> dict:
    out = {}
    for name, p in probe.items():
        if not isinstance(p, dict):
            continue
        keep = {"status": p.get("status")}
        if p.get("status") != "dead":
            if p.get("tool_count") is not None:
                keep["tool_count"] = p["tool_count"]
            tools = [{"name": str(t.get("name", ""))[:80], "description": str(t.get("description") or "")[:160]}
                     for t in (p.get("tools") or [])[:40] if isinstance(t, dict) and t.get("name")]
            if tools:
                keep["tools"] = tools
        out[name] = keep
    return out


def write_gz(name: str, data) -> None:
    out = DATA / f"{name}.gz"
    with gzip.open(out, "wt", encoding="utf-8", compresslevel=9) as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{out.name}: {out.stat().st_size / 1e6:.1f} MB")


def main() -> int:
    for name in PLAIN:
        write_gz(name, json.loads((DATA / name).read_text(encoding="utf-8")))
    write_gz("registry_probe.json", slim_probe(json.loads((DATA / "registry_probe.json").read_text(encoding="utf-8"))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
