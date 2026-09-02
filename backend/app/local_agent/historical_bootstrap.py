"""
Historical local memory bootstrap (Ideal V1 directive §4-§12): converge the
work a user ALREADY has -- repositories, Claude/ChatGPT conversation exports,
agent traces -- into the SAME LocalProcedureStore substrate that live runs
write to. No parallel historical data model: every source normalizes to one
`HistoricalEpisode`, and every candidate lands in `local_store.py`'s
`local_procedures` table via the SAME `capture_local_procedure` /
`merge_bootstrap_evidence` writers live captures use, with the SAME
v0_gate provenance/scope validation.

CONSERVATISM CONTRACT (directive §7/§8 -- this is the module's load-bearing
rule):
  - Chat messages are NOT execution evidence merely for containing
    imperative language. "I would run pytest" is a RECOMMENDATION; a
    candidate may only be created from it at all when a concrete command
    block exists, and its evidence_status stays "recommended" (never
    verified, never a fabricated outcome).
  - "executed" requires an explicit outcome marker co-occurring with a
    concrete command (past-tense run/execution, tests passed, nonzero
    exit reported, error then fix). Even then the candidate is born
    `candidate`/`fresh` with zero verification -- historical evidence can
    ground a CANDIDATE, never a VERIFIED capability (directive §10).
  - A script existing on disk grounds "run <script>" as a RECOMMENDATION
    with its provenance intact -- never prerequisites, never expected
    success, never universal applicability.
  - Discussion (no command blocks, no outcome markers) produces NO
    candidate at all; it is counted in the summary as skipped.

PRIVACY (directive §9/§11): everything here writes to the caller's own
LocalProcedureStore file, inside their workspace. Nothing is sent anywhere;
there is no network call in this module. Steps carry concrete COMMANDS and
file names the user's own material contains -- never raw conversation text
beyond the goal line the user's own title/first message supplies -- and
publication to the global corpus remains the separate explicit
`publish.py` path with its own redaction.

DEDUP (directive §11): before capturing, the candidate's goal is matched
against the existing library (semantic when a query embedding is supplied,
word-overlap otherwise). A sufficiently similar existing row RECEIVES the
new evidence ref (merge, provenance preserved per-source) instead of a
duplicate row being born. The threshold is deliberately high -- a false
NON-merge is preferred to a false merge -- and an exact name match merges
even without embeddings.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from app.local_agent.local_store import LocalProcedureStore
from app.services.skill_ingestion import parse_skill_md

# A merge must clear this bar. High on purpose (§11: prefer false
# non-merge); rows below it stay separate, each keeping its own evidence.
MERGE_SIMILARITY_THRESHOLD = 0.90

# The no-embedding lexical fallback is EXACT-goal match only. A
# word-overlap rule over-merges near-identical-but-distinct goals (two
# different CI workflows, two scripts with the same verb phrase) -- a
# false merge destroys a real procedure, while a false non-merge merely
# leaves two rows (§11's stated preference). Cross-wording convergence
# is the semantic path's job (MERGE_SIMILARITY_THRESHOLD).

PROVENANCE = "prior_library"

SOURCE_TYPES = ("repository", "claude_code_trace", "claude_chat", "chatgpt_chat")

# Evidence statuses. Only "recommended" and "executed" ever become
# candidates; "discussion" never does.
EVIDENCE_DISCUSSION = "discussion"
EVIDENCE_RECOMMENDED = "recommended"
EVIDENCE_EXECUTED = "executed"

# Outcome markers that upgrade a command block from "recommended" to
# "executed" -- past-tense/outcome language about THIS material's own
# commands, not hypotheticals. Deliberately a closed, auditable list.
_EXECUTED_MARKERS = (
    r"\bran\b.{0,60}\b(successfully|passed|worked)\b",
    r"\bexecuted\b",
    r"\btests?\s+(passed|pass now|all pass)\b",
    r"\bpytest\b.{0,40}\bpassed\b",
    r"\bexit code (0|zero)\b",
    r"\bbuild (succeeded|passes)\b",
    r"\bit('| i)?s? ?working now\b",
    r"\bfixed it\b.{0,40}\b(test|build|run)",
    r"\berror (is )?gone\b",
)
# Hypothetical/recommendation language that explicitly does NOT count as
# execution evidence even when a command block is present.
_RECOMMENDATION_MARKERS = (
    r"\byou could\b",
    r"\byou (can|may) run\b",
    r"\bi would\b",
    r"\byou should\b",
    r"\bconsider running\b",
    r"\btry running\b",
    r"\bif you want\b",
    r"\bwould run\b",
)
_EXECUTED_RE = re.compile("|".join(_EXECUTED_MARKERS), re.IGNORECASE)
_RECOMMENDATION_RE = re.compile("|".join(_RECOMMENDATION_MARKERS), re.IGNORECASE)
_FENCED_BLOCK_RE = re.compile(r"```[a-zA-Z0-9_+-]*\n(.*?)```", re.DOTALL)


@dataclass
class HistoricalEpisode:
    """The ONE normalized representation every source parser produces
    (directive §10's HistoricalSource shape). Nothing here is invented:
    every field is carried from the source material or honestly None."""

    source_type: str  # one of SOURCE_TYPES
    source_id: str  # stable identifier: file path, conversation uuid
    source_location: str  # locator ("file#span", "conversation#msg-id")
    goal: str
    steps: list[dict] = field(default_factory=list)
    evidence_status: str = EVIDENCE_DISCUSSION
    timestamp: Optional[str] = None
    session_id: Optional[str] = None

    def evidence_ref(self) -> dict:
        """The provenance record stored on the local row (§6: source type,
        identifier, location, timestamp, privacy level all survive)."""
        return {
            "source_type": self.source_type,
            "source_id": self.source_id,
            "source_location": self.source_location,
            "timestamp": self.timestamp,
            "evidence_status": self.evidence_status,
            "privacy": "local",
        }


# ---------------------------------------------------------------------------
# Source parsers -- pure functions, no store, no network. Each is honest
# about what its source can and cannot ground.
# ---------------------------------------------------------------------------


def _classify_chat_text(text: str) -> tuple[str, list[str]]:
    """One chat message -> (its strongest evidence signal, concrete command
    blocks). Recommendation language never upgrades a block to executed;
    outcome language only upgrades blocks that actually exist."""
    blocks = [b.strip() for b in _FENCED_BLOCK_RE.findall(text) if b.strip()]
    executed = bool(_EXECUTED_RE.search(text))
    recommended = bool(_RECOMMENDATION_RE.search(text))
    if blocks and executed and not recommended:
        status = EVIDENCE_EXECUTED
    elif blocks:
        status = EVIDENCE_RECOMMENDED
    else:
        status = EVIDENCE_DISCUSSION
    return status, blocks


def _goal_from(text: str, fallback: str, limit: int = 120) -> str:
    text = (text or "").strip().replace("\n", " ")
    return (text[:limit] or fallback).strip()


def parse_claude_export(path: str) -> list[HistoricalEpisode]:
    """Claude's own `conversations.json` export: a list of conversations
    with `chat_messages` carrying `sender` ("human"/"assistant") and
    `text`. Segments each conversation into candidate work episodes where
    evidence permits; pure discussion yields no episode at all."""
    with open(path, "r", encoding="utf-8") as f:
        conversations = json.load(f)
    if isinstance(conversations, dict):  # tolerate {"conversations": [...]}
        conversations = conversations.get("conversations", [])
    episodes: list[HistoricalEpisode] = []
    for conv in conversations:
        conv_id = conv.get("uuid") or conv.get("id") or "unknown"
        title = conv.get("name") or conv.get("title") or "untitled conversation"
        messages = conv.get("chat_messages") or []
        steps: list[dict] = []
        any_executed_marker = False
        for i, msg in enumerate(messages):
            text = msg.get("text") or ""
            if not text:
                content = msg.get("content")
                if isinstance(content, list):  # [{"type":"text","text":...}]
                    text = "\n".join(
                        b.get("text", "") for b in content if isinstance(b, dict)
                    )
            if _EXECUTED_RE.search(text):
                any_executed_marker = True  # outcome language anywhere in the conversation
            status, blocks = _classify_chat_text(text)
            if status == EVIDENCE_DISCUSSION:
                continue
            for j, block in enumerate(blocks):
                # Keep the FIRST line (the command itself); never fold the
                # whole block of prose into the procedure step.
                command = block.splitlines()[0].strip()
                steps.append({
                    "order": len(steps),
                    "goal": command,
                    "properties": {
                        "command": command,
                        "evidence_status": status,
                        "source_message_index": i,
                        "source_block_index": j,
                    },
                })
        if not steps:
            continue  # discussion-only conversation: skipped, not fabricated
        # Conversation-level outcome language upgrades grounded steps to
        # executed; recommendation language anywhere does NOT (the
        # _classify_chat_text per-message rule already kept hypotheticals
        # from becoming steps at all when they carry no concrete block).
        evidence_status = EVIDENCE_EXECUTED if any_executed_marker else EVIDENCE_RECOMMENDED
        episodes.append(HistoricalEpisode(
            source_type="claude_chat",
            source_id=str(conv_id),
            source_location=f"conversations.json#{conv_id}",
            goal=_goal_from(title, "claude conversation"),
            steps=steps,
            evidence_status=evidence_status,
            timestamp=conv.get("created_at") or conv.get("updated_at"),
            session_id=str(conv_id),
        ))
    return episodes


def parse_chatgpt_export(path: str) -> list[HistoricalEpisode]:
    """ChatGPT's `conversations.json` export: a list of conversations whose
    `mapping` is a node tree of `{message: {author: {role}, content:
    {parts: [...]}, create_time}}`. Same conservatism as the Claude
    parser -- one shared classification function, zero source-specific
    leniency."""
    with open(path, "r", encoding="utf-8") as f:
        conversations = json.load(f)
    if isinstance(conversations, dict):
        conversations = conversations.get("conversations", [])
    episodes: list[HistoricalEpisode] = []
    for conv in conversations:
        conv_id = conv.get("uuid") or conv.get("conversation_id") or "unknown"
        title = conv.get("title") or "untitled conversation"
        mapping = conv.get("mapping") or {}
        messages = []
        for node in mapping.values():
            msg = node.get("message") if isinstance(node, dict) else None
            if not msg:
                continue
            role = (msg.get("author") or {}).get("role")
            if role not in ("user", "assistant"):
                continue
            parts = (msg.get("content") or {}).get("parts") or []
            text = "\n".join(p for p in parts if isinstance(p, str))
            if text:
                messages.append((
                    msg.get("create_time") or 0, msg.get("id") or "", text,
                ))
        messages.sort(key=lambda m: m[0])
        steps: list[dict] = []
        any_executed_marker = any(_EXECUTED_RE.search(text) for _t, _m, text in messages)
        for i, (_t, msg_id, text) in enumerate(messages):
            status, blocks = _classify_chat_text(text)
            if status == EVIDENCE_DISCUSSION:
                continue
            for j, block in enumerate(blocks):
                command = block.splitlines()[0].strip()
                steps.append({
                    "order": len(steps),
                    "goal": command,
                    "properties": {
                        "command": command,
                        "evidence_status": status,
                        "source_message_id": msg_id or i,
                        "source_block_index": j,
                    },
                })
        if not steps:
            continue
        episodes.append(HistoricalEpisode(
            source_type="chatgpt_chat",
            source_id=str(conv_id),
            source_location=f"conversations.json#{conv_id}",
            goal=_goal_from(title, "chatgpt conversation"),
            steps=steps,
            evidence_status=EVIDENCE_EXECUTED if any_executed_marker else EVIDENCE_RECOMMENDED,
            timestamp=str(conv.get("create_time")) if conv.get("create_time") else None,
            session_id=str(conv_id),
        ))
    return episodes


def bootstrap_repository(repo_root: str) -> list[HistoricalEpisode]:
    """Point at an existing repository; extract GENUINELY procedural
    material (directive §6): SKILL.md files, agent instruction files
    (AGENTS.md), CI workflows, top-level scripts. NOT arbitrary prose --
    a README paragraph or a source file is never a procedure. Everything
    found is `recommended` at most: the repo proves the artifact exists,
    never that it was run or succeeded (directive §8)."""
    root = os.path.abspath(repo_root)
    episodes: list[HistoricalEpisode] = []

    def _steps_from(lines: list[str], extra: Optional[dict] = None) -> list[dict]:
        return [
            {"order": i, "goal": s,
             "properties": {"evidence_status": EVIDENCE_RECOMMENDED, **(extra or {})}}
            for i, s in enumerate(lines)
        ]

    # SKILL.md -- the richest source; reuse the REAL parser production
    # ingestion uses (local_ingestion.py's same `parse_skill_md`), never a
    # second, weaker one.
    for dirpath, _dirnames, filenames in os.walk(root):
        if "SKILL.md" not in filenames:
            continue
        skill_path = os.path.join(dirpath, "SKILL.md")
        with open(skill_path, "r", encoding="utf-8") as f:
            parsed = parse_skill_md(f.read(), fallback_name="unnamed-skill")
        rel = os.path.relpath(skill_path, root)
        episodes.append(HistoricalEpisode(
            source_type="repository",
            source_id=rel,
            source_location=f"{rel}#whole-file",
            goal=parsed.description or parsed.name,
            steps=_steps_from(parsed.steps),
            evidence_status=EVIDENCE_RECOMMENDED,
            session_id=rel,
        ))

    # AGENTS.md -- structure-derived directive lines only; never prose.
    agents_path = os.path.join(root, "AGENTS.md")
    if os.path.isfile(agents_path):
        with open(agents_path, "r", encoding="utf-8") as f:
            lines = [
                re.sub(r"^(?:\d+[.)]|[-*])\s+", "", s)
                for s in (l.strip() for l in f)
                if re.match(r"^(?:\d+[.)]|[-*])\s+\S", s)
            ]
        if lines:
            episodes.append(HistoricalEpisode(
                source_type="repository",
                source_id="AGENTS.md",
                source_location="AGENTS.md#directive-lines",
                goal="repo agent instructions",
                steps=_steps_from(lines),
                evidence_status=EVIDENCE_RECOMMENDED,
                session_id="AGENTS.md",
            ))

    workflows_dir = os.path.join(root, ".github", "workflows")
    if os.path.isdir(workflows_dir):
        run_re = re.compile(r"^\s*(?:-\s+)?run:\s*(.+)$")
        name_re = re.compile(r"^name:\s*(.+)$")
        for fname in sorted(os.listdir(workflows_dir)):
            if not fname.endswith((".yml", ".yaml")):
                continue
            wf_path = os.path.join(workflows_dir, fname)
            try:
                with open(wf_path, "r", encoding="utf-8") as f:
                    lines = f.read().splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            name = next(
                (m.group(1).strip().strip("\"'") for l in lines if (m := name_re.match(l))),
                fname,
            )
            commands = [m.group(1).strip() for l in lines if (m := run_re.match(l))]
            if not commands:
                continue
            rel = os.path.relpath(wf_path, root)
            episodes.append(HistoricalEpisode(
                source_type="repository",
                source_id=rel,
                source_location=f"{rel}#run-steps",
                goal=f"CI workflow: {name}",
                steps=_steps_from(
                    commands, {"command": True, "note": "CI workflow run step"},
                ),
                evidence_status=EVIDENCE_RECOMMENDED,
                session_id=rel,
            ))

    scripts_dir = os.path.join(root, "scripts")
    if os.path.isdir(scripts_dir):
        for fname in sorted(os.listdir(scripts_dir)):
            if not fname.endswith((".sh", ".ps1", ".py")):
                continue
            if not os.path.isfile(os.path.join(scripts_dir, fname)):
                continue
            rel = os.path.join("scripts", fname)
            episodes.append(HistoricalEpisode(
                source_type="repository",
                source_id=rel,
                source_location=f"{rel}#exists",
                goal=f"run {fname}",
                steps=_steps_from(
                    [f"run {rel}"],
                    {"command": rel, "note": "script exists in the repository; never observed executed"},
                ),
                evidence_status=EVIDENCE_RECOMMENDED,
                session_id=rel,
            ))
    return episodes


def bootstrap_claude_code_traces(trace_dir: str) -> list[HistoricalEpisode]:
    """Claude Code session transcripts (`~/.claude/projects/**.jsonl`):
    one episode per session that contains real tool_use activity, steps
    one per real tool invocation IN the user's own material -- these are
    OBSERVED commands from a real agent runtime, so their evidence status
    is `executed` (the strongest historical source). Discussion-only
    sessions yield nothing."""
    if not os.path.isdir(trace_dir):
        return []
    episodes: list[HistoricalEpisode] = []
    for fname in sorted(os.listdir(trace_dir)):
        if not fname.endswith(".jsonl"):
            continue
        fpath = os.path.join(trace_dir, fname)
        steps: list[dict] = []
        first_goal: Optional[str] = None
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                for line in f:
                    if "tool_use" not in line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    message = record.get("message") or {}
                    if first_goal is None and record.get("type") == "user":
                        text = message.get("content")
                        if isinstance(text, str) and text.strip():
                            first_goal = _goal_from(text, "agent session")
                    content = message.get("content")
                    if not isinstance(content, list):
                        continue
                    for block in content:
                        if not isinstance(block, dict) or block.get("type") != "tool_use":
                            continue
                        tool_input = block.get("input") or {}
                        command = next(
                            (tool_input[k] for k in ("command", "cmd")
                             if isinstance(tool_input.get(k), str)),
                            None,
                        )
                        if not command:
                            continue
                        first_line = command.splitlines()[0].strip()
                        steps.append({
                            "order": len(steps),
                            "goal": first_line,
                            "properties": {
                                "command": first_line,
                                "tool": block.get("name"),
                                "evidence_status": EVIDENCE_EXECUTED,
                            },
                        })
        except OSError:
            continue
        if not steps:
            continue
        episodes.append(HistoricalEpisode(
            source_type="claude_code_trace",
            source_id=fname,
            source_location=f"{fname}#tool_use-steps",
            goal=first_goal or f"agent session {fname}",
            steps=steps,
            evidence_status=EVIDENCE_EXECUTED,
            session_id=fname[:-len(".jsonl")],
        ))
    return episodes


# ---------------------------------------------------------------------------
# Convergence: HistoricalEpisode -> dedup -> LocalProcedureStore
# ---------------------------------------------------------------------------


def _find_merge_target(
    store: LocalProcedureStore,
    episode: HistoricalEpisode,
    query_embedding: Optional[list[float]],
) -> Optional[dict]:
    """The existing row this episode should merge into, or None. Semantic
    cosine similarity when an embedding is available; exact-goal match as
    the no-embedding lexical fallback (§11: conservative merging)."""
    matches = store.search_local_procedures(
        episode.goal, query_embedding=query_embedding, limit=5,
    )
    from app.local_agent.local_store import _cosine_similarity
    for row in matches:
        if query_embedding is not None and row.get("embedding"):
            if _cosine_similarity(query_embedding, row["embedding"]) >= MERGE_SIMILARITY_THRESHOLD:
                return row
        if row["name"].strip().lower() == episode.goal.strip().lower():
            return row
    return None


def converge_episode(
    store: LocalProcedureStore,
    episode: HistoricalEpisode,
    *,
    workspace_entity_id: Optional[str] = None,
    embedding: Optional[list[float]] = None,
) -> dict:
    """One normalized episode -> the local library. Returns
    {"status": "captured"|"merged"|"skipped", ...}. A merge APPENDS the
    new evidence ref to the existing row (multi-source convergence, §11);
    a capture births a candidate row with the episode's own provenance
    record -- never verified, never global."""
    if episode.evidence_status not in (EVIDENCE_RECOMMENDED, EVIDENCE_EXECUTED):
        return {"status": "skipped", "reason": "discussion-only"}
    if not episode.steps:
        return {"status": "skipped", "reason": "no grounded steps"}

    target = _find_merge_target(store, episode, embedding)
    if target is not None:
        store.merge_bootstrap_evidence(
            target["id"],
            evidence_ref=episode.evidence_ref(),
            source_episode_id=episode.source_id,
        )
        return {
            "status": "merged", "id": target["id"],
            "procedure_id": target["procedure_id"],
        }

    result = store.capture_local_procedure(
        name=episode.goal,
        goal=episode.goal,
        steps=episode.steps,
        scope={"bootstrap_sources": [episode.evidence_ref()]},
        evidence_refs=[episode.evidence_ref()],
        source_episode_ids=[episode.source_id],
        provenance=PROVENANCE,
        scope_type="repository",
        scope_entity_id=workspace_entity_id or episode.source_id,
        embedding=embedding,
    )
    return {"status": "captured", **result}


def run_bootstrap(
    store: LocalProcedureStore,
    *,
    repo_root: Optional[str] = None,
    claude_export: Optional[str] = None,
    chatgpt_export: Optional[str] = None,
    claude_traces_dir: Optional[str] = None,
    embed: Optional[Any] = None,
    workspace_entity_id: Optional[str] = None,
) -> dict:
    """The whole bootstrap pass: parse every requested source, converge
    each episode into the store, return an honest summary. `embed` is an
    optional async callable(text, *, input_type) -> list[float] (the same
    Embedder every real caller uses); when absent, dedup falls back to
    the strict lexical path -- disclosed, never silently weaker than it
    looks."""
    import asyncio

    episodes: list[HistoricalEpisode] = []
    if repo_root:
        episodes.extend(bootstrap_repository(repo_root))
    if claude_export:
        episodes.extend(parse_claude_export(claude_export))
    if chatgpt_export:
        episodes.extend(parse_chatgpt_export(chatgpt_export))
    if claude_traces_dir:
        episodes.extend(bootstrap_claude_code_traces(claude_traces_dir))

    summary = {"episodes": len(episodes), "captured": 0, "merged": 0, "skipped": 0, "ids": []}
    loop: Optional[Any] = None
    for episode in episodes:
        embedding: Optional[list[float]] = None
        if embed is not None:
            try:
                loop = loop or asyncio.new_event_loop()
                embedding = loop.run_until_complete(
                    embed(episode.goal, input_type="document")
                )
            except Exception:  # noqa: BLE001 -- embedding is optional; degrade honestly
                embedding = None
        result = converge_episode(
            store, episode,
            workspace_entity_id=workspace_entity_id,
            embedding=embedding,
        )
        if result["status"] in ("captured", "merged"):
            summary[result["status"]] += 1
            summary["ids"].append(result["id"])
        else:
            summary["skipped"] += 1
    if loop is not None:
        loop.close()
    return summary





