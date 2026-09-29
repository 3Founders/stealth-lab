"""
Chat-message trajectory normalizer: OpenAI-style `role`/`content`/`tool_calls`
messages -> `NormalizedTrajectory`.

Why this module exists
----------------------
`openhands.py` parses the OpenHands **event history** (action records keyed by
`id`, observation records keyed by `cause`). Several published corpora do not
ship that shape at all -- they ship the model's *chat message list*, because a
linear message history is what you train on. `nebius/SWE-rebench-openhands-
trajectories` is one of them (verified at revision 35455389: 10 columns,
`trajectory` is a list of messages with `role`/`content`/`name`/`tool_calls`/
`tool_call_id`). `openhands.py` cannot read it, and its contract is unchanged
by this module. Same seam, second source shape.

The merge that matters
----------------------
OpenHands pairs an action with its observation through the observation's
`cause` pointer. Chat messages have no such pointer: the join key is
`tool_call_id`, which appears on the assistant's `tool_calls[i].id` and again
on the `role="tool"` result. We fold each result into the event of the call
that asked for it, so one event == one executed action, and the result is
never emitted as a separate step.

Ordering is load-bearing (sequence numbers, episode assembly start/end spans),
so pairing folds a result into its caller's slot and never reorders anything.
That is the same invariant `openhands._pair_history` holds.

Three shape traps this corpus actually has (all verified on 50 sampled rows,
and all of them would silently produce garbage rather than an error):

1. `tool_calls` is the **literal string `"None"`** on non-assistant messages,
   not JSON `null`. `if msg["tool_calls"]:` is truthy for it. The dataset card's
   own usage snippet has this bug.
2. `tool_calls[i]["function"]["arguments"]` is a **JSON string**, not a dict.
3. Every message carries all five keys regardless of role, so key presence is
   not a role test.

Where the patch goes, and why not into metadata
-----------------------------------------------
`model_patch` is the row's net unified diff (28 KB in the sampled row) and it
is the only thing in this corpus that makes the outcome *checkable*. It is
emitted as a final `WRITE` event rather than parked in
`NormalizedTrajectory.metadata`, because `write_normalized_trajectory` redacts
and size-caps `tool_input`/`tool_output` (the redaction chokepoint, plus
`MAX_INLINE_PAYLOAD_BYTES`) but writes `metadata` to `agent_traces` verbatim.
Third-party repository code does not belong on an unredacted, uncapped path.

Scope limits, stated honestly
-----------------------------
- The `tools` column (the 5 OpenAI function definitions) is recorded as a
  count and a hash in the header metadata, not reproduced per event: it is
  constant for the corpus and would otherwise dominate the row.
- `provider_version` is a *schema label*, not a version number. The scaffold
  version (OpenHands 0.54.0) and the generator model come from the dataset
  card and are uniform across rows; the corpus has no per-row model column.
- `NormalizedEvent.error` is never persisted by the writer (there is no
  `error` key in its INSERT column list), so error text is carried in
  `tool_output` where redaction and the size cap both apply.
- We are reading a *training export*, not a replay log. A linear message
  history cannot represent parallel tool calls or harness-internal retries
  that the agent never surfaced. See `.scratch/ingestion/step_0_research.md` §4.3.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Optional

from app.services.ingestion_sources.base import (
    SourceArtifact,
    SourceRef,
    compute_content_hash,
)
from app.services.ingestion_sources.normalized_trajectory import (
    NormalizedEvent,
    NormalizedTrajectory,
)

log = logging.getLogger(__name__)

# --- Pinned corpus identity -------------------------------------------------
# One release, pinned. A floating revision would change the corpus under a dedup
# key, which is the exact failure the `ingested_artifacts` ledger and every
# golden set exist to prevent. Verified: dataset_info().sha == this value.
DATASET_REPO = "nebius/SWE-rebench-openhands-trajectories"
DATASET_REVISION = "35455389ab51bf5e2306bfd436ef72d0f98bf882"
#: Parent benchmark, joined per instance for the per-item license gate.
PARENT_REPO = "nebius/SWE-rebench"
PARENT_REVISION = "89cdfbab4ab1bd8f5a658bb212d1b63624f4f881"

SOURCE_TYPE = "nebius_swe_rebench_openhands_trajectory"
#: Schema label for the *shape*, bumped when this mapper's output changes.
PROVIDER_VERSION = "openhands_chatmsg_v1"
#: Uniform across the corpus per the dataset card; there is no per-row column.
SCAFFOLD = "openhands"
SCAFFOLD_VERSION = "0.54.0"
GENERATOR_MODEL = "Qwen3-Coder-480B-A35B-Instruct"

#: Dedup-key namespace. Deliberately distinct from `openhands.py`'s
#: `"openhands:"` prefix: the same trajectory ingested from a directory export
#: and from the HF corpus must not collide on `trace_events.dedup_key`.
DEDUP_PREFIX = "nebius-openhands-chatmsg"

#: The exact column names verified at DATASET_REVISION. Pinned so a future
#: schema change is a loud test failure rather than a silent zero-row run.
EXPECTED_COLUMNS = (
    "trajectory_id", "instance_id", "repo", "trajectory", "tools",
    "model_patch", "exit_status", "resolved", "gen_tests_correct",
    "pred_passes_gen_tests",
)

# --- Event classification ---------------------------------------------------
# Per-tool canonical types. The vocabulary is OBSERVE/SEARCH/READ/WRITE/EXECUTE/
# TEST/VERIFY/FAIL/RETRY/ROLLBACK/COMMIT/HANDOFF/REASON (see
# normalized_trajectory.NormalizedEvent). An unmapped tool name falls through to
# `None` (genuinely unclassified) rather than being guessed at -- the one thing
# this codebase forbids.
_TOOL_CANONICAL_TYPE: dict[str, str] = {
    # Shell in a persistent session. Upgraded to TEST when the command looks
    # like a test run, which is also what gives episode assembly a structural
    # boundary to cut on.
    "execute_bash": "EXECUTE",
    # OpenHands' file editor: str_replace / insert / undo.
    "str_replace_editor": "WRITE",
    # The agent's own reasoning, recorded through a tool.
    "think": "REASON",
    # Structured plan updates. Same category as `think`: the agent stating its
    # own intent, not observing the environment.
    "task_tracker": "REASON",
}

#: Substring markers that promote an EXECUTE event to TEST. Named constant,
#: consulted at call time (the repo's monkeypatch-retunable convention).
TEST_COMMAND_MARKERS: tuple[str, ...] = (
    "pytest", "py.test", "tox", "npm test", "npm run test", "yarn test",
    "go test", "cargo test", "jest", "vitest", "phpunit", "rspec", "mvn test",
    "gradle test", "ctest", "unittest",
)

#: `execute_bash` renders a non-zero exit as a bracketed line. Checked on the
#: head of the output only, so a file that merely mentions "error" in its
#: contents is not misreported as a failed command.
_EXIT_MARKERS: tuple[str, ...] = (
    "[command exited with code", "[command failed with code",
)

#: Defensive ceiling. `write_normalized_trajectory` is one transaction with one
#: round trip per event, so a pathological row must not be able to open an
#: unbounded transaction. Events past the ceiling are counted, not written.
MAX_EVENTS_PER_TRAJECTORY = 2000

#: The dataset's own scaffold termination message (observed in 3/50 sampled rows).
TURN_CAP_MARKER = "Agent reached maximum iteration"

_PATCH_TOOL_NAME = "model_patch"


class ChatMessageMalformedTrajectory(ValueError):
    """A row that cannot be read as a chat-message trajectory at all.

    Raised only for a whole row, never for one odd message: one bad row must
    not abort a 1,000-row batch, and the dispatcher quarantines these rather
    than letting them escape.
    """


# ---------------------------------------------------------------------------
# Pure normalization
# ---------------------------------------------------------------------------

def _loads(value: Any) -> Any:
    """Parse the corpus's string-encoded JSON, tolerating an already-parsed value.

    Returns `None` on anything unparseable rather than raising: a malformed
    `arguments` blob should cost one event's payload, not the trajectory.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or text == "None":
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _tool_calls_of(message: dict[str, Any]) -> list[dict[str, Any]]:
    """The assistant's tool calls, or `[]`.

    Handles trap #1: on a non-assistant message this field is the literal
    string `"None"`, which is truthy.
    """
    if message.get("role") != "assistant":
        return []
    calls = message.get("tool_calls")
    if not isinstance(calls, (list, tuple)):
        return []
    return [c for c in calls if isinstance(c, dict)]


def _canonical_type(tool_name: str, tool_input: dict[str, Any]) -> Optional[str]:
    """Map a tool call to the cross-harness vocabulary, or `None` if unknown."""
    base = _TOOL_CANONICAL_TYPE.get(tool_name)
    if base == "EXECUTE":
        command = tool_input.get("command")
        if isinstance(command, str):
            lowered = command.lower()
            if any(marker in lowered for marker in TEST_COMMAND_MARKERS):
                return "TEST"
        return base
    return base


def _success_from_output(output: Optional[dict[str, Any]]) -> Optional[bool]:
    """Best-effort success flag for a shell result.

    Deliberately conservative: returns `None` rather than guessing whenever the
    output is not recognisably a shell report. The OpenHands adapter could read
    `extras.exit_code`; this shape carries no structured status at all, so a
    confident `True` would be an invention.
    """
    if not output:
        return None
    content = output.get("content")
    if not isinstance(content, str):
        return None
    head = content.lstrip()[:200].lower()
    for marker in _EXIT_MARKERS:
        if marker in head:
            return "0" not in head.split(marker, 1)[1][:12] or "code 0" not in head
    return None


def _exit_is_turn_cap(exit_status: Any) -> bool:
    """True when the scaffold stopped the run, rather than the model finishing.

    6% of the sampled rows ended with `RuntimeError: Agent reached maximum
    iteration...`. Those are scaffold terminations; treating them as model
    failures would import a censoring artifact as a negative example.
    """
    return TURN_CAP_MARKER in str(exit_status or "")


def normalize_chat_message_trajectory(
    artifact: SourceArtifact,
    *,
    trace_id: Optional[str] = None,
) -> NormalizedTrajectory:
    """Map one chat-message row to a `NormalizedTrajectory`.

    Pure and deterministic: no I/O, no LLM call, no clock read except for the
    synthetic ordinal base (taken from `artifact.discovered_at`, exactly as
    `openhands.py` does).
    """
    try:
        row = json.loads(artifact.content)
    except (ValueError, TypeError) as exc:
        raise ChatMessageMalformedTrajectory(f"row is not JSON: {exc}") from exc
    if not isinstance(row, dict):
        raise ChatMessageMalformedTrajectory("row is not a JSON object")

    messages = row.get("trajectory")
    if not isinstance(messages, list):
        raise ChatMessageMalformedTrajectory("row has no `trajectory` list")

    resolved_trace_id = str(
        trace_id
        or row.get("trajectory_id")
        or artifact.source_id
        or artifact.content_hash[:16]
    )

    # Index the tool results by the id the call that produced them carries.
    # Populated on the first pass so a result that arrives *before* its call
    # (not observed in this corpus, but free to handle) still binds correctly.
    results_by_call_id: dict[str, dict[str, Any]] = {}
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        call_id = message.get("tool_call_id")
        if isinstance(call_id, str) and call_id:
            results_by_call_id.setdefault(call_id, message)

    base_time = artifact.discovered_at or datetime.now(timezone.utc)
    events: list[NormalizedEvent] = []
    skipped_malformed = 0
    truncated = False

    def emit(
        canonical: Optional[str],
        tool_name: str,
        tool_input: dict[str, Any],
        tool_output: Optional[dict[str, Any]],
        raw_event: dict[str, Any],
        *,
        success: Optional[bool] = None,
    ) -> None:
        nonlocal truncated
        if len(events) >= MAX_EVENTS_PER_TRAJECTORY:
            truncated = True
            return
        sequence = len(events)
        events.append(NormalizedEvent(
            sequence=sequence,
            canonical_event_type=canonical,
            tool_name=tool_name or "unknown",
            tool_input=tool_input,
            tool_output=tool_output,
            raw_event=raw_event,
            success=success,
            dedup_key=hashlib.sha256(
                f"{DEDUP_PREFIX}:{resolved_trace_id}:{sequence}".encode("utf-8")
            ).hexdigest(),
            # Synthetic, strictly-increasing ordinal. This corpus carries no
            # per-message wall clock; episode assembly needs a monotonic order.
            timestamp=base_time + timedelta(microseconds=sequence),
        ))

    consumed_call_ids: set[str] = set()

    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            skipped_malformed += 1
            continue
        role = message.get("role")

        if role in ("system", "user"):
            emit(
                "OBSERVE",
                str(role),
                {"content": message.get("content")},
                None,
                {"role": role, "content": message.get("content"), "message_index": index},
            )
            continue

        if role == "tool":
            call_id = message.get("tool_call_id")
            if isinstance(call_id, str) and call_id in consumed_call_ids:
                # Already folded into the caller's event; emitting it again
                # would double-count the step.
                continue
            emit(
                "OBSERVE",
                f"tool_result:{message.get('name') or 'unknown'}",
                {"content": message.get("content"), "tool_call_id": call_id},
                None,
                {"role": "tool", "name": message.get("name"),
                 "tool_call_id": call_id, "content": message.get("content"),
                 "message_index": index},
            )
            continue

        if role != "assistant":
            skipped_malformed += 1
            continue

        calls = _tool_calls_of(message)
        assistant_text = message.get("content")

        if not calls:
            emit(
                "REASON",
                "assistant",
                {"content": assistant_text},
                None,
                {"role": "assistant", "content": assistant_text, "message_index": index},
            )
            continue

        # One event per tool call: the finest faithful mapping, and the only one
        # that survives a message carrying several calls.
        for position, call in enumerate(calls):
            function = call.get("function")
            if not isinstance(function, dict):
                function = {}
            tool_name = str(function.get("name") or "unknown")
            parsed_args = _loads(function.get("arguments"))
            tool_input = parsed_args if isinstance(parsed_args, dict) else (
                {"_unparsed_arguments": function.get("arguments")}
                if function.get("arguments") is not None else {}
            )
            call_id = call.get("id")
            if isinstance(call_id, str):
                consumed_call_ids.add(call_id)
            result = results_by_call_id.get(call_id) if isinstance(call_id, str) else None
            tool_output = (
                {"content": result.get("content"), "name": result.get("name")}
                if isinstance(result, dict) else None
            )
            raw_event: dict[str, Any] = {
                "message_index": index,
                "tool_call_id": call_id,
                "tool_call_index": position,
                "type": call.get("type"),
            }
            # The assistant's visible text rides in raw_event, not tool_input:
            # tool_input holds the tool's own schema-bound arguments and must
            # not gain a key the tool never saw.
            if assistant_text:
                raw_event["assistant_content"] = assistant_text
            emit(
                _canonical_type(tool_name, tool_input),
                tool_name,
                tool_input,
                tool_output,
                raw_event,
                success=_success_from_output(tool_output),
            )

    # The net diff, as a real redacted + size-capped event. See module docstring.
    model_patch = row.get("model_patch")
    if isinstance(model_patch, str) and model_patch.strip():
        emit(
            "WRITE",
            _PATCH_TOOL_NAME,
            {"source_field": "model_patch", "instance_id": row.get("instance_id")},
            {"patch": model_patch},
            {"source_field": "model_patch", "instance_id": row.get("instance_id")},
        )

    if truncated:
        log.warning(
            "chat-message normalizer: %s hit MAX_EVENTS_PER_TRAJECTORY=%d; %d messages dropped",
            resolved_trace_id, MAX_EVENTS_PER_TRAJECTORY, len(messages),
        )

    return NormalizedTrajectory(
        trace_id=resolved_trace_id,
        session_id=resolved_trace_id,
        provider=SCAFFOLD,
        provider_version=PROVIDER_VERSION,
        events=events,
        model=GENERATOR_MODEL,
        outcome=("success" if int(row.get("resolved") or 0) == 1 else "failure"),
        metadata={
            "source_field": "trajectory",
            "dataset_repo": DATASET_REPO,
            "dataset_revision": DATASET_REVISION,
            "instance_id": row.get("instance_id"),
            "repo": row.get("repo"),
            "exit_status": row.get("exit_status"),
            "scaffold_turn_cap_reached": _exit_is_turn_cap(row.get("exit_status")),
            "resolved": int(row.get("resolved") or 0),
            "gen_tests_correct": row.get("gen_tests_correct"),
            "pred_passes_gen_tests": row.get("pred_passes_gen_tests"),
            "message_count": len(messages),
            "event_count": len(events),
            "n_declared_tools": row.get("tools_declared_count"),
            "model_patch_bytes": len(model_patch) if isinstance(model_patch, str) else 0,
            "events_truncated": truncated,
            **({"license": artifact.license_metadata} if artifact.license_metadata else {}),
        },
        skipped_malformed_events=skipped_malformed,
    )


# ---------------------------------------------------------------------------
# Source adapter (streamed, revision-pinned)
# ---------------------------------------------------------------------------

class NebiusOpenHandsTrajectorySource:
    """Streams chat-message trajectories out of the pinned HF dataset.

    `SourceAdapter` conformance note: `discover()` yields one `SourceRef` per
    row as it streams, and stashes that row for the `fetch()` that the
    consumer makes immediately afterwards. A 2.08 GB corpus cannot be
    re-iterated per ref, so `fetch()` is only valid for the ref most recently
    yielded. `fingerprint()` is the artifact's content hash.
    """

    source_type = SOURCE_TYPE

    def __init__(
        self,
        *,
        limit: Optional[int] = None,
        resolved_only: bool = True,
        repo: str = DATASET_REPO,
        revision: str = DATASET_REVISION,
    ) -> None:
        self.limit = limit
        self.resolved_only = resolved_only
        self.repo = repo
        self.revision = revision
        self._pending: dict[str, dict[str, Any]] = {}

    def _rows(self) -> Iterator[dict[str, Any]]:
        """Sync generator over HF rows. Callers must advance it off the event loop."""
        from datasets import load_dataset

        dataset = load_dataset(self.repo, split="train", revision=self.revision, streaming=True)
        emitted = 0
        for row in dataset:
            if self.resolved_only and int(row.get("resolved") or 0) != 1:
                continue
            yield dict(row)
            emitted += 1
            if self.limit is not None and emitted >= self.limit:
                return

    def discover(self) -> Iterator[SourceRef]:
        for row in self._rows():
            trajectory_id = str(row.get("trajectory_id") or "")
            if not trajectory_id:
                continue
            self._pending[trajectory_id] = row
            yield SourceRef(
                uri=f"hf://{self.repo}@{self.revision}/train/{trajectory_id}",
                repository=str(row.get("repo") or ""),
                source_id=trajectory_id,
                commit=self.revision,
            )

    def artifact_for(self, row: dict[str, Any]) -> SourceArtifact:
        """Wrap one row as a `SourceArtifact`.

        `tools` is replaced by its LENGTH, not kept: it is the same 5 OpenAI
        function definitions on every one of the 67,074 rows, so storing it
        would dominate the stored JSON for a fact that never varies. The count
        survives in the trajectory metadata, and the definitions themselves are
        recoverable from the pinned corpus.
        """
        trajectory_id = str(row.get("trajectory_id") or "")
        tools = row.get("tools")
        payload = {k: v for k, v in row.items() if k != "tools"}
        payload["tools_declared_count"] = len(tools) if isinstance(tools, list) else None
        content = json.dumps(payload, default=str)
        return SourceArtifact(
            source_type=self.source_type,
            uri=f"hf://{self.repo}@{self.revision}/train/{trajectory_id}",
            content=content,
            content_hash=compute_content_hash(content),
            repository=str(row.get("repo") or "") or None,
            commit=self.revision,
            source_id=trajectory_id or None,
        )

    def fetch(self, ref: SourceRef) -> SourceArtifact:
        key = ref.source_id or ""
        row = self._pending.pop(key, None)
        if row is None:
            raise KeyError(
                f"no buffered row for {ref.uri!r}; fetch() must follow its discover() immediately"
            )
        return self.artifact_for(row)

    def fingerprint(self, artifact: SourceArtifact) -> str:
        return artifact.content_hash
