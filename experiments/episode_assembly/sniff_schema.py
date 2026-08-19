#!/usr/bin/env python3
"""
Schema sniffer for Claude Code session transcripts (ticket 11, step B0).

Why this exists: ticket 07's research established that the per-line transcript
schema is undocumented and explicitly unstable across Claude Code releases, and
`backend/IMPLEMENTATION_HANDOFF.md` warns against building a segmenter against
assumed field names. So before any segmentation logic is written, this reports
what is *actually* in the files on this machine.

PRIVACY -- load-bearing, not a nicety. Transcripts contain real private
conversations. This script reports SCHEMA AND COUNTS ONLY: key names, type
names, value counts, timestamps, and id linkage. It never emits message text,
tool inputs, tool outputs, file contents, or file paths from inside the
transcripts. The only strings it prints from transcript data are structural:
JSON key names, `type`/`subtype` values, tool *names*, and CLI version strings.
Anything that could carry user content is counted, never quoted.

Default corpus is this project only, per the repo owner's decision -- other
project directories hold unrelated private work.

Usage:
    python3 sniff_schema.py                      # this project, summary to stdout
    python3 sniff_schema.py --json report.json   # also write a machine-readable report
    python3 sniff_schema.py --project <slug>     # a different project directory
    python3 sniff_schema.py --limit 5            # only the N largest sessions (fast pass)
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

DEFAULT_PROJECT_SLUG = "c--Users-chait-Prog-3Found-Stealth-StealthLab"
PROJECTS_ROOT = Path.home() / ".claude" / "projects"

# Content-bearing keys. We record that they were present and their shape, never
# their value. Everything not on this list is still only reported as a key NAME.
CONTENT_KEYS = {
    "content", "message", "toolUseResult", "snapshot", "attachment",
    "lastPrompt", "compactMetadata", "error",
}


class SessionStats:
    """Per-file structural facts. No transcript content is retained."""

    def __init__(self, path: Path, is_subagent: bool) -> None:
        self.path = path
        self.is_subagent = is_subagent
        self.lines = 0
        self.parse_errors = 0
        self.top_level_keys: Counter[str] = Counter()
        self.types: Counter[str] = Counter()
        self.keys_by_type: dict[str, set[str]] = defaultdict(set)
        self.versions: Counter[str] = Counter()
        self.session_ids: set[str] = set()
        self.git_branches: Counter[str] = Counter()
        self.cwd_present = 0
        self.roots = 0                      # parentUuid is null -> forest root
        self.lines_without_timestamp = 0
        self.types_without_timestamp: Counter[str] = Counter()
        self.timestamps: list[str] = []     # ISO strings only, for gap analysis
        self.tool_use_ids: set[str] = set()
        self.tool_result_ids: set[str] = set()
        self.tool_names: Counter[str] = Counter()
        self.content_block_types: Counter[str] = Counter()
        self.attachment_types: Counter[str] = Counter()
        self.is_sidechain: Counter[bool] = Counter()
        self.source_tool_assistant_uuids = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "file": self.path.name,
            "is_subagent": self.is_subagent,
            "lines": self.lines,
            "parse_errors": self.parse_errors,
            "distinct_top_level_keys": sorted(self.top_level_keys),
            "types": dict(self.types.most_common()),
            "keys_by_type": {t: sorted(k) for t, k in sorted(self.keys_by_type.items())},
            "cli_versions": dict(self.versions.most_common()),
            "n_session_ids": len(self.session_ids),
            "git_branches": dict(self.git_branches.most_common()),
            "forest_roots": self.roots,
            "lines_without_timestamp": self.lines_without_timestamp,
            "types_without_timestamp": dict(self.types_without_timestamp.most_common()),
            "tool_use_count": len(self.tool_use_ids),
            "tool_result_count": len(self.tool_result_ids),
            "unmatched_tool_uses": len(self.tool_use_ids - self.tool_result_ids),
            "orphan_tool_results": len(self.tool_result_ids - self.tool_use_ids),
            "tool_names": dict(self.tool_names.most_common()),
            "content_block_types": dict(self.content_block_types.most_common()),
            "attachment_types": dict(self.attachment_types.most_common()),
            "is_sidechain": {str(k): v for k, v in self.is_sidechain.items()},
            "source_tool_assistant_uuid_lines": self.source_tool_assistant_uuids,
        }


def _scan_message(msg: Any, st: SessionStats) -> None:
    """Walk a `message` object for block types and tool ids. Counts only."""
    if not isinstance(msg, dict):
        return
    content = msg.get("content")
    if isinstance(content, str):
        st.content_block_types["<plain string>"] += 1
        return
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type", "<no type>")
        st.content_block_types[btype] += 1
        if btype == "tool_use":
            if isinstance(block.get("id"), str):
                st.tool_use_ids.add(block["id"])
            name = block.get("name")
            if isinstance(name, str):
                st.tool_names[name] += 1
        elif btype == "tool_result":
            tid = block.get("tool_use_id")
            if isinstance(tid, str):
                st.tool_result_ids.add(tid)


def scan_file(path: Path, is_subagent: bool) -> SessionStats:
    st = SessionStats(path, is_subagent)
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            st.lines += 1
            # Defensive per line: three CLI versions were observed inside a
            # single real file, so schema drift is intra-file. One bad line
            # must not abort the scan.
            try:
                rec = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                st.parse_errors += 1
                continue
            if not isinstance(rec, dict):
                st.parse_errors += 1
                continue

            for k in rec:
                st.top_level_keys[k] += 1

            rtype = rec.get("type", "<no type>")
            if isinstance(rtype, str):
                st.types[rtype] += 1
                st.keys_by_type[rtype].update(rec.keys())

            ver = rec.get("version")
            if isinstance(ver, str):
                st.versions[ver] += 1

            sid = rec.get("sessionId")
            if isinstance(sid, str):
                st.session_ids.add(sid)

            branch = rec.get("gitBranch")
            if isinstance(branch, str) and branch:
                st.git_branches[branch] += 1
            if isinstance(rec.get("cwd"), str):
                st.cwd_present += 1

            # Forest structure: parentUuid null marks a root. Real files have
            # several (compaction / resume), so a single-chain walk truncates.
            if "parentUuid" in rec and rec.get("parentUuid") is None:
                st.roots += 1

            ts = rec.get("timestamp")
            if isinstance(ts, str) and ts:
                st.timestamps.append(ts)
            else:
                st.lines_without_timestamp += 1
                if isinstance(rtype, str):
                    st.types_without_timestamp[rtype] += 1

            if "isSidechain" in rec:
                st.is_sidechain[bool(rec.get("isSidechain"))] += 1
            if rec.get("sourceToolAssistantUUID"):
                st.source_tool_assistant_uuids += 1

            att = rec.get("attachment")
            if isinstance(att, dict):
                atype = att.get("type")
                if isinstance(atype, str):
                    st.attachment_types[atype] += 1

            _scan_message(rec.get("message"), st)
    return st


def discover(project_dir: Path) -> tuple[list[Path], list[Path]]:
    """(session files, subagent files). Subagents live in sibling directories,
    NOT inline in the session file -- joined by sourceToolAssistantUUID."""
    sessions = sorted(project_dir.glob("*.jsonl"))
    subagents = sorted(project_dir.glob("*/subagents/*.jsonl"))
    return sessions, subagents


def aggregate(stats: list[SessionStats]) -> dict[str, Any]:
    keys: Counter[str] = Counter()
    types: Counter[str] = Counter()
    versions: Counter[str] = Counter()
    tools: Counter[str] = Counter()
    blocks: Counter[str] = Counter()
    atts: Counter[str] = Counter()
    no_ts: Counter[str] = Counter()
    branches: Counter[str] = Counter()
    keys_by_type: dict[str, set[str]] = defaultdict(set)
    total_lines = total_errors = total_roots = 0
    for s in stats:
        keys.update(s.top_level_keys)
        types.update(s.types)
        versions.update(s.versions)
        tools.update(s.tool_names)
        blocks.update(s.content_block_types)
        atts.update(s.attachment_types)
        no_ts.update(s.types_without_timestamp)
        branches.update(s.git_branches)
        for t, k in s.keys_by_type.items():
            keys_by_type[t].update(k)
        total_lines += s.lines
        total_errors += s.parse_errors
        total_roots += s.roots
    return {
        "files": len(stats),
        "total_lines": total_lines,
        "parse_errors": total_errors,
        "forest_roots_total": total_roots,
        "distinct_top_level_keys": sorted(keys),
        "types": dict(types.most_common()),
        "keys_by_type": {t: sorted(k) for t, k in sorted(keys_by_type.items())},
        "cli_versions": dict(versions.most_common()),
        "git_branches": dict(branches.most_common()),
        "tool_names": dict(tools.most_common()),
        "content_block_types": dict(blocks.most_common()),
        "attachment_types": dict(atts.most_common()),
        "types_without_timestamp": dict(no_ts.most_common()),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--project", default=DEFAULT_PROJECT_SLUG,
                    help="project directory slug under ~/.claude/projects")
    ap.add_argument("--json", type=Path, help="also write the full report here")
    ap.add_argument("--limit", type=int,
                    help="only scan the N largest session files")
    ap.add_argument("--include-subagents", action="store_true", default=True,
                    help="include <session>/subagents/*.jsonl (default: on)")
    args = ap.parse_args()

    project_dir = PROJECTS_ROOT / args.project
    if not project_dir.is_dir():
        print(f"ERROR: no such project directory: {project_dir}")
        return 1

    sessions, subagents = discover(project_dir)
    if args.limit:
        sessions = sorted(sessions, key=lambda p: p.stat().st_size, reverse=True)[: args.limit]
        keep = {s.stem for s in sessions}
        subagents = [p for p in subagents if p.parent.parent.name in keep]
    if not args.include_subagents:
        subagents = []

    if not sessions:
        print(f"ERROR: no .jsonl session files in {project_dir}")
        return 1

    print(f"project      {args.project}")
    print(f"sessions     {len(sessions)}")
    print(f"subagents    {len(subagents)}")
    print(f"total bytes  {sum(p.stat().st_size for p in sessions + subagents):,}")
    print()

    session_stats = [scan_file(p, False) for p in sessions]
    subagent_stats = [scan_file(p, True) for p in subagents]

    agg_sessions = aggregate(session_stats)
    agg_subagents = aggregate(subagent_stats) if subagent_stats else None

    print("=== SESSIONS ===")
    print(f"lines               {agg_sessions['total_lines']:,}")
    print(f"parse errors        {agg_sessions['parse_errors']}")
    print(f"forest roots        {agg_sessions['forest_roots_total']} "
          f"(parentUuid null; >1 per file means compaction/resume)")
    print(f"top-level keys      {len(agg_sessions['distinct_top_level_keys'])}")
    print(f"CLI versions        {agg_sessions['cli_versions']}")
    print(f"git branches        {agg_sessions['git_branches']}")
    print()
    print("line types:")
    for t, n in agg_sessions["types"].items():
        missing = agg_sessions["types_without_timestamp"].get(t, 0)
        flag = f"   [{missing} without timestamp]" if missing else ""
        print(f"  {t:<24} {n:>7,}{flag}")
    print()
    print("content block types:", agg_sessions["content_block_types"])
    print("attachment types:   ", agg_sessions["attachment_types"])
    print("tool names:         ", agg_sessions["tool_names"])
    print()

    tu = sum(len(s.tool_use_ids) for s in session_stats)
    tr = sum(len(s.tool_result_ids) for s in session_stats)
    print(f"tool_use ids {tu:,} vs tool_result ids {tr:,} "
          f"(difference = interrupted/denied calls; do not assume pairing)")

    if agg_subagents:
        print()
        print("=== SUBAGENTS (separate files, joined by sourceToolAssistantUUID) ===")
        print(f"files               {agg_subagents['files']}")
        print(f"lines               {agg_subagents['total_lines']:,}")
        print(f"line types          {agg_subagents['types']}")
        linked = sum(s.source_tool_assistant_uuids for s in subagent_stats)
        print(f"lines carrying sourceToolAssistantUUID: {linked}")

    if args.json:
        report = {
            "project": args.project,
            "sessions": agg_sessions,
            "subagents": agg_subagents,
            "per_session": [s.as_dict() for s in session_stats],
            "per_subagent": [s.as_dict() for s in subagent_stats],
        }
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
