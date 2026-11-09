# What an AI IDE or desktop AI assistant should have, and the repos worth copying

2026-11-08. Sources: Exa searches on 2026-11-08 (VS Code AI feature docs, the *Building an Agentic Coding Editor* guide, Zed/ACP docs, MCP docs, Claude Code `/goal`, hooks, subagents and skills docs, OpenHands critic and verification-stack posts, SWE-Verifier and SWE-smith, Eclipse/Theia/OpenSumi architecture, VS Code fork/extension discussions, UI performance write-ups, project READMEs and comparison posts), plus **OpenAlex Works API queries on 2026-11-08** for the research-backed backend section. Star counts, licenses, and citation counts are from search snapshots and drift; a few projects named here are archived or read-only, which is flagged in the table. Nothing below is a measured result of ours.

Scope: two product shapes that get conflated — an **AI IDE** (code-centric: repo context, edit/verify loop) and a **desktop AI assistant** (OS-centric: screen context, voice, cross-app actions). They share one spine, so the requirements are written once and the desktop-only deltas are called out separately.

Structure: **Part I** is the system/backend spine, **Part II** is the UI/UX requirement set, **Part III** maps the research literature (OpenAlex) to backend components, **Part IV** specifies the verifier and `/goal` loop, **Part V** is the component-level UI implementation spec, **Part VI** is the terminal/extension/fork decision, then the repo survey and reading order follow.

---

## The spine

```
substrate (editor / OS)  →  context engine  →  agent loop  →  hands (edit / run / browse)
                                  ↑                  ↓
                             memory + index     permission gate + diff / checkpoint
```

The useful framing from the agentic-editor guide: this is not "an LLM in an IDE." It is a layered system under three permanent pressures — **latency** (typing must never wait on a model), **economics** (the median request must be cheap or a flat subscription is impossible), and **trust** (a probabilistic actor with shell access must never silently wreck your repo or machine). Every requirement below is a different answer to one of those three.

---

## Part I — System requirements

### 1. A substrate that never blocks

- Async, multi-process core. The keystroke path stays under ~16 ms; everything slow is off-thread and cancellable.
- Real editor semantics: rope/piece-tree buffers, undo, multi-cursor, virtualized rendering.
- Every model call is streamed (SSE) and cancellable when the client resets.
- Desktop delta: tray and headless mode, global hotkeys, optional overlay — the runtime should work with no window open.

**Why:** a slow model must be structurally incapable of freezing typing. This is the single most common failure in demos.

### 2. A context engine, not "attach files"

- **Hybrid retrieval**: dense embeddings + BM25/grep, reranked to a tight top-k under an explicit token budget.
- **Structural legibility**: tree-sitter for syntax and symbols, LSP for exact references, and a symbol/call graph (LSP `textDocument/references` into an adjacency list is enough).
- **Diagnostics as feedback**: LSP errors are the observation that closes the agent's self-correction loop.
- **Repo map**: Aider's ranked, budgeted map of the most-referenced symbols is still the best cheap trick for large repos.
- **Cache-friendly prompt assembly**: stable system prompt and rules first, volatile question last, so prompt caching actually hits and lost-in-the-middle is fought deliberately.
- Context-usage meter, compaction, and per-turn token/tool/time budgets.

**Why:** context selection, not model choice, is where most quality comes from.

### 3. Three interaction modes, at three latency scales

| Mode | Target latency | Shape |
|---|---|---|
| **Tab / completion** | 100–300 ms | Debounced, dedicated small FIM model, ghost text, separate lightweight filter to suppress bad suggestions |
| **Chat / inline edit** | 1–2 s | Retrieval + mid/frontier model, streamed, edit rendered as accept/reject hunks |
| **Agent** | minutes | `while` loop: build request → tool call → harness executes → observation → repeat, bounded by budgets |

Plus **Plan mode** distinct from **Act mode**, and parallel sessions in isolated git worktrees.

**Why:** one model cannot serve all three. Latency and cost only work out if completion is a separate tiny model on a separate path that never touches retrieval or tools.

### 4. Tools and protocols

- A small, orthogonal tool set: `read_file`, `write_file`/apply-diff, `search_codebase`, `run_command`, `list_directory`, `get_diagnostics`, `get_references`, web/browser.
- **MCP** client for external reach (databases, GitHub, Sentry, browsers). Build one client, inherit thousands of integrations. This is table stakes now, not differentiation.
- **ACP** (Agent Client Protocol) to decouple editor from agent the way LSP decoupled editors from language servers. If you build an editor, supporting ACP means every agent works inside it; if you build an agent, ACP means every editor can host you.
- Also expose the *editor* as a server: debugger, run configurations, refactors over MCP, so an external agent can use the IDE's own capabilities.
- Every tool call passes through the permission gate.

**Why:** protocol-shaped capability is what turns a product into a platform without forking the core per integration.

### 5. Execution with real isolation

- Sandboxed shell: container, microVM, seccomp, or OS-level confinement (e.g. Windows AppContainer + Job Object). Ephemeral, no host filesystem or network by default, scrubbed environment, resource caps.
- Capture exit code, stdout/stderr, and truncate sensibly; feed it back as the observation.
- Distinguish clearly between "isolated folder" (a git worktree) and "security boundary" — they are not the same thing.
- Optional warm pool of pre-provisioned runtimes for startup latency.

**Why:** agents that can write code must be able to run it, and running LLM-generated code on the host is a hard no.

### 6. Change safety

- Every edit is a reviewable diff with per-hunk accept/reject.
- **Checkpoints** you can rewind; three-way merge against concurrent user edits.
- Git worktrees per parallel agent, auto-commit, and a diff pane that stays usable for multi-file changes.
- Undo stack for deletes and destructive tools.

**Why:** this is where most demos fail. The agent loop is easy; making its output safe to accept is the product.

### 7. Permissions and trust

- Tiered gates: silent / confirm / explicit-confirm. Deny-by-default, fail-closed.
- "Allow all" is a setting, never a prerequisite for using the product.
- Always-on audit log; a panic/stop switch that halts all agents immediately.
- Preview/apply contracts (`_preview` + `_apply` pairs) for destructive tools, so dry runs are first-class.
- Steer, queue, and stop for in-flight work; stopping does not undo completed actions, so say so.

**Why:** the trust pressure. Users will not hand shell access to something they cannot audit or stop.

### 8. Memory that is inspectable

- Short rolling buffer that is never persisted.
- Vector store for semantic recall + SQLite for structured state (profiles, permissions, transcripts).
- Project/user rules files, plus an explicit "remember this" memory the user can view and edit.
- Do not persist raw audio.

**Why:** memory that users cannot inspect becomes a liability rather than a feature.

### 9. Model routing and cost control

- BYOK, bring-your-own-subscription, and local models (Ollama, llama.cpp, LM Studio) behind one picker; the harness does not change with the model.
- Cheap local model for simple turns, frontier for reasoning, tiny FIM model for completion.
- A proxy layer that holds keys, routes models, rate-limits, and maximizes prompt-cache hits.
- Budgets on turns, tokens, time, and dollars, with visible spend.

**Why:** economics. Without routing and caching, a flat subscription is impossible.

### 10. Extensibility as first-class config

- Agents, rules, and tools as declarative, version-controllable files (`config.yaml`, `AGENTS.md`, skills) so teams can review agent behavior like code.
- Plugins are MCP servers; adding a capability must not require touching the core.
- Shared/importable config blocks (`owner/item-name` style) for reuse across projects.

**Why:** policy-as-code is what makes the product adoptable by teams, not just individuals.

### 11. Desktop-assistant deltas

- **Screen context**: event-driven capture (app switch, window focus, click/scroll, typing pause, clipboard) rather than fixed-FPS polling; **accessibility tree first, OCR only as fallback**; local SQLite with FTS5; expose it as both a localhost REST API and an MCP server.
- **Voice**: lightweight always-on wake word → full STT on wake → LLM plans → tools execute via MCP → TTS. Never persist raw audio.
- Local-first defaults, opt-in telemetry, OS keychain for keys, multi-profile identity (per-user memory and permissions).
- Computer use / browser use as an explicit, gated tool with the same permission tiers as shell.

**Why:** on the desktop the "repo" is the screen and the OS. Same spine, different context source.

### 12. A headless surface

- An HTTP/OpenAPI server plus a generated SDK, with the GUI as just one client.
- The same engine must power TUI, IDE plugin, desktop app, web task board, and CI.

**Why:** opencode, Cline, and OpenHands all converged on this independently. It is the decision that prevents a rewrite when the second client appears.

### 13. Evaluation

- A task suite (e.g. SWE-bench Verified style), completion acceptance/retention rates, A/B tests, LLM-as-judge where execution is not available.
- The eval loop is the only thing that tells you whether a change helped.

---

## Part II — UI/UX requirements

Five principles first, because they settle most individual decisions:

1. **The diff is the product.** The most important screen is "what did the agent change, and do I accept it." Review UX outranks everything except the chat stream.
2. **Chat-first, not IDE-first, for agent work.** The primary object is the session, not the file buffer. Files, diffs, and terminals appear in service of reviewing what the agent did.
3. **Trust through visibility.** Tool calls stream as expandable cards with command, output, and exit status — never as a spinner. Users must be able to audit what ran.
4. **Every surface degrades gracefully to read-only.** A phone or remote view is a monitor/approve/steer surface: same screens, reduced interaction.
5. **Progressive disclosure of power.** Simplest surface first; model parameters, permissions, and settings appear after the user has succeeded once.

### 1. Layout and information architecture

- Three regions: **session/thread list** (collapsible) → **chat stream + composer** → **tabbed inspector** (terminal, diff, plan, file, PR).
- **Command palette on `Ctrl/Cmd-K` everywhere**, same shortcut in every context. Fuzzy match, aliases, recent commands, and suggestions on open — an empty input is a wasted opportunity.
- **Status bar always visible**: active model, tokens/spend, and permission mode with one-click change. Any "YOLO"/allow-all state renders in a warning color everywhere it appears.
- Threads grouped by project, with search, archive, rename, and persistent metadata across restarts.
- **Persist draft prompts** and queued prompts server-side so a crash or context switch loses nothing.

### 2. Chat stream and message rendering

- Markdown with syntax-highlighted code blocks, copy buttons, and stable anchors.
- **Stream tokens with paced reveal**, but never let token streaming block structured state updates: a tool can fail, a source can be selected, or an approval can be requested while the answer is still generating.
- Collapsible reasoning/thinking sections, collapsed by default.
- Citations and attribution for retrieved context; show which files/snippets were used.
- Per-message actions: edit, resend, branch from here, copy, revert to this checkpoint.
- **Prompt queue while a turn runs**: list queued prompts in order with edit / reorder / delete; drain between turns; pause the queue on a failed turn; never auto-run a queue after a crash.
- Inline quick-reply chips for common follow-ups ("continue", "explain", "undo") instead of forcing typing.

### 3. Tool-call visualization

- Consume **structured events**, not scraped prose: `message.created`, `agent.started`, `agent.thinking`, `tool.started`, `tool.progress`, `tool.completed`, `tool.failed`, `approval.requested`, `approval.resolved`, `artifact.created`, `agent.completed`.
- Every event carries a stable run ID, timestamp, source, and a **display-safe summary**. Raw prompts, credentials, and private tool payloads stay out of client-visible events.
- Tool cards: icon, one-line summary, status, duration, exit code; expand for the full command and truncated output.
- **Domain-specific rendering, not generic JSON**: search → query + source count + selected sources; write → diff preview + approval; browser → URL + screenshot; shell → command + exit code + logs; retrieval → cited documents + confidence.
- **Split "prepare" from "commit"** for risky tools: stage the email, invoice, or file change, then require explicit approval to send/charge/delete/publish.
- Failures are events, not crashes: the card shows the failure, the model reads it, and the user gets retry / revise / skip / escalate.

### 4. Diff and review

- Side-by-side or unified view with aligned rows, line numbers, word-level highlights, and collapsed unchanged lines.
- **Per-hunk accept/reject**, with bulk actions and undo. Partial acceptance is the point; "accept all" alone is not enough.
- Rejected side stays visible but dimmed so the layout does not jump.
- Keyboard-first: `j`/`k` to move between hunks, `a`/`r` to decide, then advance.
- **Streaming patches render incrementally**; finished hunks never move; review controls appear when a hunk is complete.
- Guard very large files (e.g. behind "show anyway" above ~1,000 lines).
- Show review progress ("0 of 2 reviewed"); enable Apply only when decisions are complete, or make "apply selected" explicit.
- A **checkpoint timeline** with rewind, distinct from file-level undo.

### 5. Approval and permission UX

- Approval prompts inline in the stream, not as modal dialogs.
- Keyboard-first: `y` approve, `a` always, `n` deny.
- Show exactly what will run, against what, with what effect; offer a preview for anything destructive.
- "Always allow" must be **narrowly scoped** (this command / this server / this session), not a global switch by default.
- Always-reachable **stop/panic control**; state clearly that stopping does not undo completed actions.
- Audit-log viewer with export, searchable by tool, path, and time.
- Permission changes are visible, logged, and reversible.

### 6. Plan, progress, and long-running work

- A **visible plan/todo list** that updates as work proceeds. Long tasks look frozen without one.
- Phase indicator: plan → implement → verify → review.
- Elapsed time, turn count, token and dollar meter per session.
- Visibility into subagents and parallel sessions, with their worktree/branch labels.
- Long-running processes (dev servers, watchers) get a persistent status surface, not a buried log.

### 7. Context attachment

- `@`-mentions for files, symbols, and docs; `#` for issues/errors; drag-and-drop; paste test output.
- Context chips show token cost and can be removed individually.
- Disclose what was used automatically ("read 12 files, 3 search results") and let the user pin or exclude.
- An explicit "add context" affordance alongside automatic retrieval; users should never wonder what the model can see.

### 8. Onboarding and empty state

- **Pre-populate with a real, impressive example**, not a tour and not a blank box. Output before input flips the mental model from "what do I do?" to "how do I do that?"
- Templates over blank canvas; pre-filled prompts on hover outperform empty placeholders.
- **Show a refusal early, on purpose**, and show one uncertain answer early. Both calibrate trust better than five successes; a tool that says no is a tool whose yes means something.
- State the boundary in one line, in the agent's voice, at the top: "I can read your docs and draft replies; I can't send anything or touch billing."
- First task: reliable, on the user's **real** work, small enough to verify in under a minute. Reduce setup between arrival and first result to near zero.
- Do not let suggested prompts be a highlight reel; choose the three most reliable and most common tasks.
- Never promise autonomy that is not turned on yet.

### 9. Ambient and proactive behavior

- **Tiered escalation**: low-severity events queue silently and expire; medium-severity batch into a periodic summary; high-severity interrupts directly.
- Confidence threshold below which the agent acts but does not interrupt (note it, update state, queue for review).
- Debounce related events: five related triggers in 30 seconds are one interruption.
- Three explicit modes: **Notify** (no action needed), **Question** (human input changes the outcome), **Review** (about to act or reversible action needs confirmation).
- Choose the interrupt channel deliberately: ghost text competes for zero attention until reached for; a modal forces a stop.
- Measure dismissal and ignore rates; if suggestions become wallpaper, the feature has failed even if acceptance looks fine.

### 10. Voice (desktop delta)

- **First audio within ~1 second** of end of speech; treat >2 seconds as a defect. Budget = end-of-speech detection + ASR + time-to-first-token + tool calls + TTS first audio + network.
- Stream every stage; use a fast model for conversation and a larger one only when needed.
- **Acknowledge before acting** ("let me check that") instead of dead air while a tool runs.
- **Barge-in**: let users interrupt TTS and resume control; cancel the assistant's own audio so it does not respond to itself.
- Semantic end-of-turn detection, not silence alone.
- Confirm critical details by reading them back in chunks; implicit confirmation when confidence is high and error cost is low, explicit when either is not.
- Keep spoken lists short (~3 options), then "or I can read more."
- Always offer a non-voice path (keyboard, clipboard, click).
- Push-to-talk with fail-closed auto-send: an unfinished phrase keeps listening rather than sending garbage.
- Reprompt ladder: rephrase, then give an example, then offer an alternative.

### 11. Desktop shell and presence

- Global-hotkey launcher that opens as a few-pixel surface over work, grows without flicker, and can hand a running turn to the full window mid-stream.
- Tray/dock state that mirrors the launcher: idle, listening, speaking, working, needs-input.
- Optional click-through overlay that never blocks the app beneath; hover to wake.
- Multi-monitor and DPI correctness; on Wayland, if the selection cannot be read, say so and offer a clipboard fallback rather than silently doing nothing.
- OS-native notifications that are actionable and respect Do Not Disturb.
- A **visible capture indicator** whenever screen or audio context is being recorded, with a one-click pause.

### 12. Visual and interaction quality

- Respect HCI latency thresholds: **~0.1 s feels instant, ~1 s keeps flow, ~10 s breaks attention.** Use skeletons and optimistic UI; never a bare spinner.
- Consistent density, typography, semantic color, dark/light themes, and reduced-motion support.
- **Color is never the sole carrier of meaning**: diffs also use `+`/`-`, labels, and text; status uses icon + text.
- Full keyboard paths for every action; focus management that does not jump during streaming.
- Screen-reader announcements for streaming text (`aria-live="polite"`), tool status, and approvals. Agent supervision must be accessible — this is now an active research area (CHI 2026 work on screen-reader users in the "vibe coding" era).
- Undo everywhere; confirm destructive actions; never make "restart" the only recovery path.

### 13. Multi-session and remote

- Session list sorted by **needs-attention** (running / needs-input / idle), with clear state icons.
- Parallel sessions labelled by project, branch, and worktree; dependency chains visible.
- Mobile/remote is monitor / approve / steer: same screens, reduced interaction, no side-by-side diff on narrow viewports.
- Push notification when an approval blocks progress.

### 14. Settings and model UX

- Model picker showing capability, cost, and latency, with an "Auto" option and per-task assignment (chat / completion / apply / embedding).
- BYOK keys in the OS keychain; never render a key back into the UI.
- Telemetry opt-in with plain-language disclosure of exactly what is sent.
- Usage and spend dashboard per session, project, and model.
- Configuration editable as files with schema validation and live reload, not hidden only in dialogs.

### 15. UI anti-patterns to design against

- "Thinking" while tools actually run (mystery work).
- Invisible side effects with no preview or audit trail.
- Chat-only control where a button, form, or diff control would do.
- Trace leakage: internal prompts, credentials, or private payloads in client-visible events.
- No recovery path: a tool fails and the only option is to restart.
- Accept-all-only diffs; per-hunk partial acceptance is mandatory.
- A modal for every permission; this trains click-through.
- Highlight-reel onboarding that calibrates users to the top of the distribution.
- Interrupting at high mental workload; interruption cost is not uniform over time.

---

## Part III — Backend research agenda (OpenAlex)

Method: OpenAlex Works API queried on 2026-11-08 with `title_and_abstract.search` / `search`, filtered to 2019+, relevance-ranked, then supplemented with title lookups for known papers. Counts below are OpenAlex citation counts at query time. Some are preprints; venues are as returned. This is a map of the research the backend should be designed against, not a claim that every paper is settled.

### 1. Repository context and retrieval

| Work | Signal | Backend implication |
|---|---|---|
| **RepoCoder** — repository-level completion through iterative retrieval and generation (EMNLP 2023, 488 cites, [doi](https://doi.org/10.18653/v1/2023.emnlp-main.151)) | Retrieve → generate → re-retrieve beats one-shot retrieval | Retrieval is a **loop**, not a single pre-flight step |
| **CodeRAG-Bench** — can retrieval augment code generation? (NAACL Findings 2025, 120 cites, [doi](https://doi.org/10.18653/v1/2025.findings-naacl.176)) | Retrieval helps most when parametric knowledge is insufficient; benefit varies by task | Route retrieval by task type; do not retrieve unconditionally |
| **CodeRAG** — relevant *and necessary* knowledge for repo-level completion (EMNLP 2025, [doi](https://doi.org/10.18653/v1/2025.emnlp-main.1187)) | Precision matters more than volume | Rerank and prune; a bigger context is not a better one |
| **On the Impacts of Contexts on Repository-Level Code Generation** / REPOEXEC (NAACL Findings 2025, [doi](https://doi.org/10.18653/v1/2025.findings-naacl.82)) | Repository context is decisive for executability and dependency use | Evaluate on executable repo-level tasks, not snippet match |
| **Teaching Code LLMs to Use Autocompletion Tools in Repository-Level Code Generation** (TOSEM 2025, 71 cites, [doi](https://doi.org/10.1145/3714462)) | Giving the model tools beats giving it more text | Expose codebase navigation as tools, not just as prompt |
| **Retrieval-Augmented Generation for Code Summarization via Hybrid GNN** (2020, 142 cites, [doi](https://doi.org/10.48550/arxiv.2006.05405)) | Hybrid structural + semantic retrieval works for code understanding | Combine graph/structural signals with embeddings |

**So what:** build a retrieval service with a hybrid index (dense + lexical + symbol graph), a reranker, and an iterative retrieve-generate loop. Budget tokens explicitly and measure on repo-level executability.

### 2. Long context versus retrieval

| Work | Signal | Backend implication |
|---|---|---|
| **Lost in the Middle** (TACL 2024, 3,455 cites, [doi](https://doi.org/10.1162/tacl_a_00638)) | Performance is U-shaped in context position; middle content is under-used | Place the most important context at the start and end; do not dump |
| **RULER** — what is the *real* context size? (2024, 1,058 cites, [doi](https://doi.org/10.48550/arxiv.2404.06654)) | Claimed context windows overstate effective context | Measure effective context per model; do not trust the spec sheet |
| **LooGLE** / **L-Eval** (ACL 2024) | Long-context understanding needs task-level evaluation | Add long-context evals to the harness, not just needle tests |
| **How to Train Long-Context LMs (Effectively)** (ACL 2025, 124 cites, [doi](https://doi.org/10.18653/v1/2025.acl-long.366)) | Training and evaluation protocols matter for real long-context use | Treat context length as a measured capability, not a constant |

**So what:** the context assembler is a ranking and ordering problem. Stable prefix first for cache hits, most relevant evidence at the edges, explicit token budgets, and per-model effective-context limits.

### 3. Inference latency, caching, and cost

| Work | Signal | Backend implication |
|---|---|---|
| **PagedAttention / vLLM** (SOSP 2023, 3,734 cites, [doi](https://doi.org/10.1145/3600006.3613165)) | KV-cache paging and batching make high-throughput serving viable | If self-hosting, this is the baseline architecture |
| **Prompt Cache** — modular attention reuse (2023, 102 cites, [doi](https://doi.org/10.48550/arxiv.2311.04934)) | Overlapping prompt segments can reuse attention states | Order prompts so the stable prefix is byte-identical across calls |
| **vCache** — verified semantic prompt caching (2025, [doi](https://doi.org/10.48550/arxiv.2502.03771)) | Semantic caches can return wrong answers on similar-looking prompts | Any semantic cache needs verification, not just similarity |
| **Efficient Prompt Caching via Embedding Similarity** (2024, [doi](https://doi.org/10.48550/arxiv.2402.01173)) | Embedding similarity is a usable cache key with care | Cache completion and chat separately; measure hit quality |

**So what:** a model gateway with routing, prompt-cache-aware assembly, separate FIM and chat paths, budgets, and cache verification. Latency and unit economics are backend features, not infra afterthoughts.

### 4. Agent architecture, tool use, and harness design

| Work | Signal | Backend implication |
|---|---|---|
| **ReAct** (2022, [arXiv:2210.03629](https://arxiv.org/abs/2210.03629)) | Interleaving reasoning traces and actions improves grounding | The loop should expose thought, action, observation as distinct events |
| **Reflexion** (2023, [arXiv:2303.11366](https://arxiv.org/abs/2303.11366)) | Verbal self-reflection after failure improves subsequent attempts | Persist failure context and feed it back; do not just retry identically |
| **Toolformer** (NeurIPS 2023, 3,086 cites, [doi](https://doi.org/10.52202/075280-2997)) | Models can learn when and how to call tools | Tool descriptions and schemas are part of the product surface |
| **SWE-agent** (2024, [arXiv:2405.15793](https://arxiv.org/abs/2405.15793)) | The agent-computer interface (ACI) materially changes performance | Invest in tool ergonomics: concise output, good errors, navigation commands |
| **SWE-bench** (2023, [arXiv:2310.06770](https://arxiv.org/abs/2310.06770)) and variants ([Goes Live](https://doi.org/10.52202/085713-4923), [SWE-bench+](https://doi.org/10.48550/arxiv.2410.06992), [Multi-SWE-bench](https://doi.org/10.52202/085713-2111)) | Real issue resolution is the meaningful benchmark; multilingual and contamination issues matter | Build an internal issue-resolution suite; track contamination and language coverage |
| **Agentless** (2024, [arXiv:2407.01489](https://arxiv.org/abs/2407.01489)) | A simple localize → repair → validate pipeline can beat complex agents | Do not assume more agent autonomy is better; benchmark simple baselines |

**So what:** the harness is a first-class research object. Tool output truncation, error messages, navigation affordances, and observation formatting matter as much as the model. Keep a simple pipeline as a baseline.

### 5. Verification and execution feedback

| Work | Signal | Backend implication |
|---|---|---|
| **InterCode** (NeurIPS 2023, 119 cites, [doi](https://doi.org/10.52202/075280-1035)) | Interactive coding with execution feedback is a distinct setting | The runtime must return structured observations, not just text |
| **RLEF** — grounding code LLMs in execution feedback (2024, 143 cites, [doi](https://doi.org/10.48550/arxiv.2410.02089)) | Execution feedback is a strong training and inference signal | Log every execution result as reusable data |
| **S\*** — test-time scaling for code (EMNLP Findings 2025, 94 cites, [doi](https://doi.org/10.18653/v1/2025.findings-emnlp.865)) | Generating and selecting among candidates beats single-shot | Support sample → execute → select, with a compute budget |
| **Agentic Rubrics as Contextual Verifiers** (ACL 2026, 26 cites, [doi](https://doi.org/10.18653/v1/2026.acl-long.697)) | Verification is the bottleneck for RL and test-time scaling | A verifier service is a backend component, not a prompt |

**So what:** build a verification service (tests, diagnostics, static analysis, rubric checks) and a generate-verify-select loop. Execution feedback is the ground truth that makes the agent self-correcting.

### 6. Security, sandboxing, and prompt injection

| Work | Signal | Backend implication |
|---|---|---|
| **MCP: Landscape, Security Threats, and Future Research Directions** (TOSEM 2026, 749 cites, [doi](https://doi.org/10.1145/3796519)) | The MCP lifecycle has distinct threat surfaces | Validate server handshakes; scope tokens; audit tool discovery |
| **Agent Security Bench (ASB)** (2024, 250 cites, [doi](https://doi.org/10.48550/arxiv.2410.02644)) | Systematically benchmarks attacks and defenses across tools and memory | Use it as a design checklist and regression suite |
| **Adaptive Attacks Break Defenses Against Indirect Prompt Injection** (NAACL Findings 2025, 121 cites, [doi](https://doi.org/10.18653/v1/2025.findings-naacl.395)) | Static defenses fail under adaptive attack | Assume bypass; enforce least privilege and human gates, not just filters |
| **Task Shield** (ACL 2025, 94 cites, [doi](https://doi.org/10.18653/v1/2025.acl-long.1435)) | Enforcing task alignment reduces indirect injection success | Add a task-alignment check before consequential actions |
| **AgentVigil** (2025, [doi](https://doi.org/10.48550/arxiv.2505.05849)) | Black-box red-teaming is feasible and necessary | Red-team the tool surface continuously |
| **Do Users Write More Insecure Code with AI Assistants?** (CCS 2023, 464 cites, [doi](https://doi.org/10.1145/3576915.3623157)) | Users produced less secure code while believing it was more secure | Surface security findings in the review UI; do not let confidence imply safety |

**So what:** treat all tool output as untrusted input, enforce deny-by-default and least privilege, verify task alignment before side effects, run adaptive red-team tests, and make security state visible in the UI.

### 7. Human factors, trust, and productivity measurement

| Work | Signal | Backend implication |
|---|---|---|
| **Grounded Copilot** (PACMPL 2023, 671 cites, [doi](https://doi.org/10.1145/3586030)) | Programmers use assistants in two modes: **acceleration** and **exploration** | Support both; exploration needs different context and UI than autocomplete |
| **Agentic AI Software Engineers: Programming with Trust** (CACM 2026, 55 cites, [doi](https://doi.org/10.1145/3769314)) | Trust has technical and human components; proposes review parity and specification inference for explainability | Generate justifications tied to intent; require AI changes to pass the same review gates |
| **The Impact of AI on Developer Productivity** (2023, 1,394 cites, [doi](https://doi.org/10.48550/arxiv.2302.06590)) | Controlled experiment found a large speedup on a greenfield task | Do not generalize greenfield results to maintenance work |
| **Measuring the Impact of Early-2025 AI on Experienced OSS Developers** (2025, 303 cites, [doi](https://doi.org/10.48550/arxiv.2507.09089)) | RCT found experienced developers were ~19% **slower** while believing they were faster | Measure actual cycle time, not perceived speed; instrument for this |
| **Copilot acceptance-rate studies** ([CACM 2024](https://doi.org/10.1145/3633453); [enterprise deployment](https://arxiv.org/abs/2501.13282)) | Acceptance ~27–33% of suggestions; acceptance predicts perceived productivity but varies widely | Track acceptance **and** retention; acceptance alone is gameable |
| **Trust calibration** ([Lebiere 2021](https://doi.org/10.3389/frobt.2021.652776); [Tatasciore 2025](https://doi.org/10.1080/10447318.2025.2487861)) | Transparency can improve calibration but too much overwhelms | Show evidence on demand, not a firehose; design for appropriate reliance |

**So what:** instrument actual and perceived productivity separately, design for both acceleration and exploration, and make explanations evidence-linked. Trust is a measurable system property.

### 8. Code review and explainability

| Work | Signal | Backend implication |
|---|---|---|
| **Fine-tuning and prompt engineering for LLM-based code review** (IST 2024, 151 cites, [doi](https://doi.org/10.1016/j.infsof.2024.107523)) | LLMs can automate review comments with the right training and prompts | Review is a separate service with its own evaluation |
| **LLaMA-Reviewer** (ISSRE 2023, 170 cites, [doi](https://doi.org/10.1109/issre59848.2023.00026)) | Parameter-efficient tuning is enough for useful review | Do not assume a frontier model is required for every review task |
| **Explainable AI for Software Engineering** (ASE 2021, 88 cites, [doi](https://doi.org/10.1109/ase51524.2021.9678580)) | Explanations are necessary for adoption of ML4SE | Attach rationale and evidence to generated findings |

**So what:** a review agent that runs before the human, with findings linked to lines, rules, and evidence. Review parity: AI changes pass the same gates as human changes.

### 9. Hybrid program analysis + LLM

| Work | Signal | Backend implication |
|---|---|---|
| **Advancing Code Coverage: program analysis + LLMs** (TOSEM 2025, 68 cites, [doi](https://doi.org/10.1145/3748505)) | Static/dynamic analysis plus LLM beats either alone | Keep LSP, tree-sitter, and test coverage as first-class inputs |
| **ROCODE** — backtracking + program analysis (ICSE-Companion 2025, [doi](https://doi.org/10.1109/icse55347.2025.00133)) | Program analysis catches errors the model cannot see | Feed diagnostics into the loop as observations |
| **Distributional Program Analysis** (ASE 2026, [doi](https://doi.org/10.1145/3832783.3834555)) | Sampling many programs and analyzing the distribution is a new analysis primitive | Support multi-sample generation and cross-sample analysis |
| **FAMiT** — mitigating false alarms (2025, [doi](https://doi.org/10.1007/978-3-031-98208-8_16)) | LLMs can reduce static-analysis false positives | Use the model to triage analyzer output, not replace it |

**So what:** program analysis is the ground-truth layer, not legacy. The backend should fuse exact (LSP/graph/analyzers) and fuzzy (embeddings/LLM) signals.

### 10. HCI and interface research

| Work | Signal | Backend implication |
|---|---|---|
| **A Comparative Analysis of Industry Human-AI Interaction Guidelines** (2020, 41 cites, [doi](https://doi.org/10.48550/arxiv.2010.11761)) | Apple/Google/Microsoft guidelines converge on a common set | Use them as a UI review checklist |
| **Assessing Human-AI Interaction Early through Factorial Surveys** (TOCHI 2022, 70 cites, [doi](https://doi.org/10.1145/3511605)) | Interaction designs can be evaluated before engineering investment | Test UI variants with factorial surveys before building |
| **Mixed-initiative interaction** (CHI EA 2023, [doi](https://doi.org/10.1145/3544549.3577061)) | Control should shift between human and system, not sit with one | Design explicit handoff points, not a single "agent mode" |
| **Programmers Who Use Screen Readers in the Vibe Coding Era** (CHI 2026, 13 cites, [doi](https://doi.org/10.1145/3772318.3790726)) | Agent supervision creates new accessibility barriers | Screen-reader-accessible diffs, status, and approvals are required |
| **Privacy-Respecting Type Error Telemetry at Scale** (Programming 2024, [doi](https://doi.org/10.22152/programming-journal.org/2024/8/12)) | Useful telemetry can be collected without violating privacy | Design telemetry with aggregation and opt-in from the start |
| **Pre-Filtering Code Suggestions using Developer Behavioral Telemetry** (ASEW 2025, [doi](https://doi.org/10.1109/asew67777.2025.00032)) | Behavioral signals can suppress low-value suggestions before they cost latency | Filter at the client before the model call where possible |

**So what:** UI decisions should be validated, not asserted. Telemetry can improve both relevance and latency; accessibility is a research-grade requirement for agent supervision, not a compliance afterthought.

### 11. Evaluation methodology

- Use **execution-based** benchmarks (SWE-bench-style) for agent quality, **RULER-style** probes for effective context, and **retention** (does accepted code survive?) for completion quality.
- Pair offline evals with **human studies**; perceived productivity and actual productivity diverge (see the 2025 RCT).
- Use LLM-as-judge only where execution is impossible, and validate the judge against human labels.
- Keep a **regression suite for security** (ASB, indirect prompt injection) alongside quality evals.

### 12. What this means for our backend

If we build the spine, the research points to these services as separate, testable components:

1. **Indexer** — tree-sitter chunks, embeddings, BM25, symbol/call graph, incremental Merkle-style sync.
2. **Retriever + reranker** — hybrid search, budgeted top-k, iterative re-retrieval.
3. **Context assembler** — cache-aware ordering, stable prefix, per-model effective-context limits, truncation.
4. **Agent harness / ACI** — small orthogonal tool set, structured observations, output truncation, error surfacing, simple baseline.
5. **Verifier** — tests, diagnostics, static analysis, rubric checks; generate-verify-select.
6. **Sandbox runtime** — ephemeral, least privilege, egress control, auditable I/O.
7. **Policy + audit** — permission tiers, task-alignment checks, preview/apply, panic switch, exportable log.
8. **Model gateway** — routing, BYOK, prompt caching, semantic cache with verification, budgets.
9. **Evaluation + telemetry** — execution benchmarks, retention, security regressions, privacy-preserving usage signals.

The UI sits on top of these and should never have to guess what the backend did. Every backend action that matters should arrive as a structured, display-safe event.

---

## Part IV — Verification and the `/goal` loop

### 4.1 What Claude Code's `/goal` actually does

This is documented, not inferred ([code.claude.com/docs/en/goal](https://code.claude.com/docs/en/goal)):

- `/goal <condition>` sets a completion condition. It immediately starts a turn with the condition as the directive.
- After **every turn**, Claude Code sends the condition and the conversation so far to the **configured small fast model**. That model returns one of three verdicts, each with a short reason: **not yet met**, **met**, **impossible**.
- On "not yet met", Claude starts another turn and takes the reason as guidance. On "met", the goal clears and an `achieved` entry is recorded. On "impossible", the goal clears with a `failed` entry and the reason.
- It is a wrapper around a **session-scoped prompt-based `Stop` hook**. Auto-approve mode removes per-tool prompts; `/goal` removes per-turn prompts. They are complementary.
- The evaluator **does not run commands or read files independently**. It judges only what the actor surfaced in the transcript. So the condition must be something the actor's own output can demonstrate ("`npm test` exits 0", "`git status` is clean").
- Conditions can be up to **4,000 characters** and should contain one measurable end state, a stated check, and constraints ("no other test file is modified").
- Budget clauses go in the condition text ("or stop after 20 turns").
- If the actor answers without tool use for several turns, the loop stops, warns, and returns control with the goal still set.
- **Background work defers evaluation**: if a subagent or background shell is still running, evaluation is skipped until a turn ends with no background work.
- Four classes of unrecoverable error clear the goal (auth failure, and similar causes you must fix); other errors leave it set.
- UI: a `◎ /goal active` indicator with elapsed time, each verdict in the transcript, `Ctrl+O` for the reason, and the latest reason in the status view.

The important design move is not the slash command. It is **separating the actor from the completion judge**, and making the judge cheap, fast, and bounded.

### 4.2 Generalized architecture

```
goal contract ──► actor loop ──► evidence (transcript + tool results)
      ▲                │                    │
      │                ▼                    ▼
   budgets      deterministic checks ──► evaluator (small model)
      │                │                    │
      └──────────── verdict + reason ◄──────┘
                       │
        met ──► stop / record success
        not_met ──► inject reason, continue
        impossible ──► stop / record failure
```

Four rules that make it work:

1. **The evaluator sees evidence, not intentions.** Feed it tool outputs, exit codes, diffs, and the actor's claims. Require quoted evidence for a "met" verdict.
2. **Deterministic checks run first.** Exit codes, test results, `git status`, file counts, schema validation. The model only judges what cannot be checked mechanically.
3. **The harness owns the budget.** Max turns, max wall-clock, max dollars, max consecutive no-tool-use turns. The evaluator never decides whether to keep spending.
4. **The judge is a different model from the actor.** Ideally smaller and faster; never the same call that produced the work.

### 4.3 Verifier taxonomy

SWE-Critix (arXiv 2610.13356, 2026) gives a useful 2×2 for coding-agent verifiers:

| | Outcome-only | Process-aware |
|---|---|---|
| **Training-free** | Agentless, LLM-as-a-Verifier, Agentic Rubrics | SWE-PRM, TrajDebug, SWE-Prime, RETRACE, LivePlan, AgentLens |
| **Trained, scalar** | SWE-Gym, R2E-Gym, SWE-RM, SWE-TRACE | OpenHands Critic, Critic Rubrics |
| **Trained, generalist** | Agentic Rubrics, ExecCritic, SWE-Review | SWE-Critix, Scout |

Three practical distinctions:

- **Outcome vs process**: judging the final patch is cheaper; judging the trajectory catches "went off track", loops, and scope creep before code is pushed.
- **Scalar vs explanatory**: a 0–1 score is easy to threshold but hard to act on; a natural-language analysis is what the actor can use as guidance.
- **Trained vs prompted**: a prompted small model is a day of work; a trained verifier is a data pipeline.

### 4.4 What the evidence says

| Finding | Source | Implication |
|---|---|---|
| A small 4B verifier that selectively checks **<4% of steps** improved resolution 75.5%→77.2% at **1.09× cost**, and generalized to another policy model | [SWE-Verifier](https://arxiv.org/abs/2610.30284), [repo](https://github.com/zkjzou/SWE-Verifier) | Do not verify every step. Gate: score the action, sample alternatives only when the score is low |
| A critic trained only on benchmark traces was **near-random on production outcomes** (AUC 0.45–0.48); production supervision reached 0.58 (PR merge) and 0.69 (code survival) | [OpenHands, Learning to Verify](https://www.openhands.dev/blog/20260305-learning-to-verify-ai-generated-code) | Train/tune on your own traces and your own success proxy, not SWE-bench alone |
| A small critic runs **sub-second to ~1s**, cheap enough for interactive use | same | The evaluator can sit in the hot loop if it is small |
| Pairwise LLM judgments flip **13.6%** of the time on average, **28%** of questions exceed 20%, one reached 56%; **11 repeated trials** were needed for a 95% majority verdict | [The Coin Flip Judge?](https://arxiv.org/abs/2606.13685) | Single-trial judging is too noisy for high-stakes gates; use 3 draws + majority, randomize position |
| Absolute LLM scores are unreliable and show systematic pessimism; **ranking is more reliable than scoring** | [AxiomBench](https://arxiv.org/html/2512.20159v2) | Prefer pairwise/relative comparisons for candidate selection; do not treat a scalar as truth |
| Generation and verification improve at different rates — the **generation-verification gap** | [COVERA](https://aclanthology.org/2026.findings-emnlp.1634.pdf) | A stronger generator is not automatically a better judge; verify separately |
| "Don't imagine — execute": models hallucinate execution traces and confidently validate buggy code | [SolidCoder](https://aclanthology.org/2026.findings-acl.361/) | Ground verification in sandboxed execution and property-based oracles wherever possible |
| Execution-feedback process rewards improve correctness and efficiency | [CodePRM](https://aclanthology.org/2025.findings-acl.428.pdf), [ORPS](https://proceedings.mlr.press/v267/yu25f.html) | Feed execution results into step-level scoring, not just final pass/fail |

### 4.5 Goal contract and verdict schema

Goal contract:

```json
{
  "goal_id": "goal_01H...",
  "condition": "All tests in test/auth pass, lint is clean, and no other test file is modified. Stop after 20 turns.",
  "checks": [
    {"id": "tests", "type": "command", "cmd": "npm test -- test/auth", "expect_exit": 0},
    {"id": "lint", "type": "command", "cmd": "npm run lint", "expect_exit": 0},
    {"id": "scope", "type": "git", "assert": "no changes outside test/auth and src/auth"}
  ],
  "budget": {"max_turns": 20, "max_minutes": 30, "max_usd": 2.00},
  "evidence_policy": "transcript_only",
  "evaluator": {"model": "small-fast", "samples": 3, "aggregate": "majority"}
}
```

Verdict:

```json
{
  "verdict": "not_met",
  "confidence": 0.82,
  "reason": "Auth tests pass, but lint reports 2 errors in src/auth/session.ts.",
  "evidence": ["npm test: 14 passed", "npm run lint: 2 errors, exit 1"],
  "next_hint": "Fix the two lint errors without changing test files."
}
```

Design rules for the evaluator prompt:

- Give it the condition, the last N turns (not the whole transcript), and the raw tool outputs.
- Tell it the three verdicts and the exact JSON schema. Reject free-form answers.
- Require `evidence` to quote the transcript. A "met" without evidence is downgraded to "not_met".
- Forbid it from inferring that a command ran if the output is absent.
- Tell it the budget clause and let it judge only progress against it — the harness enforces the actual limit.
- Ask for `next_hint` only on "not_met"; it becomes the next turn's guidance.
- Run 3 samples and take the majority for any gate that can spend money or push code.

### 4.6 Loop pseudocode

```text
set_goal(condition, checks, budget)
while goal_active:
    turn = actor.step(goal, last_hint)
    if turn.error in UNRECOVERABLE: clear_goal("error"); break
    if background_work_running: continue          # defer, like /goal
    det = run_deterministic_checks(checks)
    if det.all_pass and evaluator_confirms(det): mark_met(); break
    v = evaluator.judge(condition, transcript, det)
    if v.verdict == "met": mark_met(); break
    if v.verdict == "impossible": mark_failed(v.reason); break
    last_hint = v.next_hint
    if no_tool_use_streak >= 3: pause("no progress"); break
    if budget_exceeded(): pause("budget"); break
```

Two independent stop conditions matter: the evaluator's judgment and the harness's budget/no-progress guard. Never rely on the evaluator to stop a runaway loop.

### 4.7 Repos and references for the verifier layer

| Repo / work | What it is | What to take |
|---|---|---|
| [zkjzou/SWE-Verifier](https://github.com/zkjzou/SWE-Verifier) | 4B gate + selector for coding agents, with data pipeline, prompts, vLLM serving, mini-swe-agent integration | Selective verification: score the action, sample only when low, rank before execute |
| [OpenHands software-agent-sdk](https://github.com/OpenHands/software-agent-sdk) / [critic docs](https://docs.openhands.dev/sdk/guides/critic) | `APIBasedCritic`, `IterativeRefinementConfig(success_threshold, max_iterations)` | A drop-in critic interface and retry-on-low-score loop |
| [OpenHands verification stack](https://www.openhands.dev/blog/20260506-the-verification-stack) | Two layers: trajectory critic before push, repo reviewer + QA agent after PR | Separate agent-level and repo-level verification |
| [SWE-agent/mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) | ~100-line agent loop: `Agent` / `Model` / `Environment` protocols, linear history, bash-only | A minimal harness to attach a verifier to; easy to instrument |
| [SWE-smith](https://github.com/SWE-bench/SWE-smith) | Generates 100s–1,000s of task instances per repo by synthesizing bugs; 50k instances, 128 repos | Generate verification/training data from our own repos |
| [OSpec](https://github.com/clawplays/ospec) | Spec-driven `plan → act → verify` loop with repo-persisted evidence, reviewer gates, L1/L2/L3 autonomy | Durable goal state in the repo, not in chat history |
| [awesome-verified-coding-agents](https://github.com/raymyers/awesome-verified-coding-agents) | Curated list of verification-first agents and formal-methods work | Landscape scan |
| [llm-verified-with-MCTS](https://github.com/namin/llm-verified-with-monte-carlo-tree-search) | MCTS over generation with a verifier at every step (Dafny/Coq/Lean/Rust) | Verifier-in-the-loop search for high-assurance tasks |
| [AlphaVerus](https://github.com/cmu-l3/alphaverus) | Tree search with verifier feedback for formally verified code | Feedback-driven refinement, reward-hacking filters |
| Aider lint/test loop ([docs](https://aider.chat/docs/usage/lint-test.html)) | Runs linter/tests after edits, reflects errors back, asks before fixing | The cheapest useful verifier is the compiler and the test suite |
| Claude Code hooks ([guide](https://code.claude.com/docs/en/hooks-guide)) | `Stop` / `SubagentStop` prompt hooks with `ok`, `reason`, `impossible` | The exact mechanism `/goal` is built on; copy the event model |

Low-maturity but directionally interesting: **Anvil** (plan-execute-verify-recover, trained verifier), **kode** (verification gates before write), **molt** (verification receipts), **mu** (small judge model for routine decisions), **Mixdog** (lead model + cheaper review models), **OMK** (test-gated goals). Treat these as pattern references, not dependencies — most are very new and low-star.

### 4.8 Build order

| Stage | What | Cost | When |
|---|---|---|---|
| **v0** | Deterministic checks + small-model judge over the transcript, 3-verdict JSON, budgets, no-progress guard | Days | Now |
| **v1** | Process critic: score each turn/segment, trigger reflection on low scores | 1–2 weeks | After v0 has traces |
| **v2** | Trained critic on our own traces, using merge/survival as sparse labels + rubrics for dense supervision | 4–8 weeks + data | Once we have volume |
| **v3** | Selective verification: gate + candidate sampling + selector, as in SWE-Verifier | Research project | Only if v2 plateaus |

### 4.9 Verification anti-patterns

- **Self-verification**: asking the same model that wrote the code whether it is correct. The generation-verification gap is real.
- **Judge without evidence**: evaluating "did you finish?" from the actor's prose alone.
- **Absolute scores as truth**: prefer pass/fail checks and pairwise ranking; calibrate any scalar.
- **Single-trial high-stakes gates**: 13.6% flip rate is too high for merge/deploy decisions.
- **Benchmark-only critics**: near-random on production outcomes in the OpenHands data.
- **Evaluator with unbounded authority**: it must not be able to extend its own budget or approve its own actions.
- **No-progress blindness**: a loop that keeps "trying" without tool use must be stopped by the harness.
- **Reward hacking**: a verifier that only checks "tests pass" invites deleting tests. Add scope constraints ("no other test file modified") and diff review.

---

## Part V — UI/UX implementation spec (granular)

Part II is the requirement set; this is the component-level companion.

### 5.1 Layout spec

| Region | Default | Collapse | Notes |
|---|---|---|---|
| Session rail | 240–280 px | `Ctrl/Cmd-B`, icon-only at 48 px | Grouped by project; needs-attention sort |
| Thread + composer | flexible, min 420 px | — | Chat is the primary column |
| Inspector | 360–520 px, resizable | `Ctrl/Cmd-J` | Tabs: Terminal / Diff / Plan / Files / PR |
| Status bar | 24–28 px | never | Model, tokens/$, permission mode |
| Command palette | overlay, 640 px max width | `Esc` | Same shortcut everywhere |

Breakpoints: ≥1280 px three columns; 900–1280 px inspector becomes a drawer; <900 px single column with a bottom sheet for approvals and a per-file diff list instead of side-by-side.

### 5.2 Agent state machine

Borrow the six-state model from Daintree rather than inventing one:

| State | Meaning | UI |
|---|---|---|
| `idle` | Prompt ready, nothing in flight | Normal composer |
| `working` | Streaming, running a tool, executing shell | Spinner-free progress, tool cards, elapsed timer |
| `waiting` | Blocked on the human; carries a **reason** | Attention badge, notification |
| `directing` | User is typing into that agent | Suppress "needs attention" for this panel |
| `done` | Agent detected its own completion | Summary + review CTA |
| `exited` | Process ended (clean, killed, crash) | Terminal state, restart affordance |

Waiting reasons, ordered by how much a second of attention buys:

1. `approval` — a specific choice is required (one keystroke unblocks).
2. `error` — auth, rate limit, quota, network, failed command (typing may not help).
3. `question` — free-form question (read before answering).
4. `prompt` — empty prompt, no stronger evidence (render as plain "waiting for input").

Never show a generic "waiting" when you can classify the reason. Never claim `prompt` with certainty; it is the fallback.

### 5.3 Streaming render pipeline

The performance trap: every token delta re-parsing Markdown and re-tokenizing code.

A production pattern that works ([orbit.build](https://www.orbit.build/blog/measure-once-ai-chat-virtualization)):

- **Coalesce** streaming Markdown re-parses to one per animation frame (`requestAnimationFrame`). A 30–50 Hz token stream must not trigger 30–50 full re-parses.
- **Virtualize** the message list (`@tanstack/react-virtual` or equivalent). Historical messages should not hold DOM, Shiki, or Mermaid subtrees in memory.
- **Lazy-load** Mermaid, KaTeX, and heavy grammars on first use (`React.lazy` + `Suspense`), not in the baseline bundle.
- **Cache rendered HTML** after a quiet window (~80 ms) so re-entering the viewport costs ~0.05 ms instead of ~1.5 ms per segment. A screen of 20 segments goes from ~30 ms (dropped frames at 120 Hz) to ~1 ms.
- **Measure after settle**: async syntax highlighting produces three size states (mounted, markdown, highlighted). A virtualizer that trusts the first measurement will mis-position. Use a settle-gated snapshot and `ResizeObserver`.
- **Batch events per frame**; never re-render the whole list for a tool-progress tick.

Budgets to hold:

| Metric | Target |
|---|---|
| First visible token | < 1 s |
| Streaming re-parse | ≤ 1 per frame |
| Frame budget | 8.3 ms at 120 Hz, 16.7 ms at 60 Hz |
| Scroll with history | no long tasks > 50 ms |
| Command palette open | < 100 ms |
| Diff render, 1k lines | < 200 ms, then virtualize |

### 5.4 Composer spec

- Auto-resizing textarea, 1–8 lines visible, then internal scroll.
- `Enter` sends; `Shift+Enter` newline; configurable.
- **IME-safe**: track `compositionstart`/`compositionend` and block submit while composing. Without this, Chinese/Japanese/Korean input sends half-finished text.
- Attachments: drag-drop, paste image, file picker; show chips with size and remove.
- `@` opens a fuzzy file/symbol picker; `#` opens issues/errors; `/` opens commands.
- Draft persistence per thread, restored on crash or switch.
- Queue mode: if a turn is running, show queued prompts above the composer with reorder/delete; drain between turns; pause on failure.
- Token/cost estimate for the composed prompt when attachments are large.
- Character counter only near a hard limit; never nag.

### 5.5 Command palette spec

- `Ctrl/Cmd-K` globally, same shortcut in every context; `Esc` closes; `Enter` runs; `↑/↓` moves; `Tab` completes.
- Fuzzy match with aliases (`keywords`) and recents; show shortcuts on the right of each row.
- Group by category (Session, File, Agent, Model, View, Settings) and show a "recent" group first.
- Show suggestions on open — recents, frequent commands, and context-relevant actions. An empty palette is a wasted surface.
- Virtualize above ~2,000–3,000 items (`cmdk` is fine below that; set `shouldFilter={false}` and bring your own filter/virtualizer for larger sets).
- OS-aware labels: `⌘K` on macOS, `Ctrl+K` elsewhere. Never show the wrong platform's glyph.
- Accessible combobox semantics: `role="listbox"`, `aria-activedescendant`, type-ahead.

### 5.6 Diff viewer spec

- Parse once (`parsePatch`/`diffText`), render per file, virtualize lines above ~1,000.
- Unified and split views; split lines wrap so the two sides stay aligned.
- Word-level highlights; collapsed unchanged context (default 3 lines).
- Stable hunk IDs; decisions keyed by hunk, not by line index.
- Per-hunk `Accept` / `Reject`; decided hunks fold to one line with `Show` / `Undo`; bulk actions with undo.
- Streaming patches: hunks append, finished hunks never move; controls appear when a hunk is complete.
- Keyboard: `j`/`k` next/previous hunk, `a` accept, `r` reject, `u` undo.
- Header: file path, rename detection, `+n/−m`, review progress ("2 of 7 reviewed"), `Apply` only when all decided (or explicit `Apply selected`).
- Color is not the only signal: `+`/`−`, "Added"/"Removed" labels, and text for screen readers.
- **Accessible diff viewer**: a mode that reads each hunk as a block ("Change 3 of 12. Removed: … Added: …"), navigable with `F7` / `Shift+F7`, mirroring VS Code's Accessible Diff Viewer.
- Guard very large files behind "show anyway"; never freeze the UI on a 50k-line generated file.

### 5.7 Design tokens and theming

Three tiers, no component consumes a raw color:

```css
:root {
  /* Tier 1: primitives */
  --primitive-slate-900: #0f172a;
  --primitive-blue-600: #2563eb;
  --primitive-blue-400: #60a5fa;
  /* Tier 2: semantic */
  --color-bg-surface: var(--primitive-slate-100);
  --color-text-primary: var(--primitive-slate-900);
  --color-action-primary: var(--primitive-blue-600);
}
[data-theme="dark"] {
  --color-bg-surface: var(--primitive-slate-900);
  --color-text-primary: var(--primitive-slate-100);
  --color-action-primary: var(--primitive-blue-400); /* passes contrast on dark */
}
```

- Validate contrast **at token definition time** for both themes: ≥4.5:1 for body (AA), ≥7:1 preferred for long-form code and status text.
- Semantic names (`bg`, `text`, `border`, `action`, `surface`, `diff-added`, `diff-removed`) survive theme changes; raw hex does not.
- Diff colors need a separate palette for light/dark and a color-blind-safe alternative (blue/orange or pattern + sign).
- High-contrast mode, forced-colors mode, and reduced transparency are separate test targets.
- Do not use color alone for status, diff, or risk. Pair with icon, label, or sign.

### 5.8 Motion and perceived performance

- Durations: 80–120 ms for hover/press, 150–200 ms for panels, 200–250 ms for route changes. Anything over 300 ms needs a progress affordance.
- Easing: ease-out for entrances, ease-in for exits; avoid bounce in a work tool.
- Respect `prefers-reduced-motion`; replace movement with opacity.
- **Optimistic UI** for local actions (accept hunk, rename, queue prompt); reconcile on server response and roll back visibly on failure.
- Skeleton rules: use only when layout is predictable and load is 300 ms–3 s. Under 300 ms, a flash is worse than nothing. Over 3 s, fix the actual load or show progress with a cancel.
- Never a bare spinner for an agent turn; show what is happening (tool name, target, elapsed).
- No layout shift when a tool card expands: reserve space or animate height.

### 5.9 Keyboard and focus

- Every action has a keyboard path; the mouse is an accelerator, not a requirement.
- Shortcuts must be **discoverable** (tooltips, palette hints, a single reference sheet), **memorable** (mnemonics), and **conflict-free** (do not steal OS/browser/editor bindings without a strong reason).
- Show platform-correct glyphs everywhere.
- Focus is visible, never trapped; modals return focus to the trigger.
- Streaming text must not steal focus. Use a live region (`aria-live="polite"`) for new messages and status, and never re-announce the whole transcript.
- Composite widgets follow the APG: `listbox` with `aria-activedescendant` or roving `tabindex`; `grid` when options contain interactive elements.
- `Esc` always means "cancel/close", never "discard work".

### 5.10 Error, empty, and loading states

For each surface, define four states explicitly:

| Surface | Empty | Loading | Error | Success |
|---|---|---|---|---|
| Session list | "No sessions yet" + start CTA | skeleton rows | retry + offline badge | sorted list |
| Thread | templates / example | streaming placeholder | inline error card with retry/revise | messages |
| Diff | "No changes" | streaming hunks | "could not parse patch" + raw view | review progress |
| Terminal | shell prompt | connecting | exit code + stderr | output |
| Plan | "No plan yet" | generating | "plan failed" + retry | checklist |
| Approval | — | — | expired/denied + reason | resolved |

Error copy rules: say what failed, why (if known), what to do next, and what state the system is in. Offer retry / revise / skip / escalate. Never make "restart" the only option.

### 5.11 Notifications and interruptibility

- Three delivery tiers: silent log, batched summary, direct interrupt.
- Classify by severity and confidence; debounce related events.
- OS notification copy is a sentence: "Claude is waiting for approval", "Codex asked a question".
- Respect Do Not Disturb and focus modes.
- Deep-link the notification to the exact session and approval.
- Never interrupt at high mental workload if it can wait; interruption cost is not uniform.
- Track dismissal/ignore rate per notification class and tune thresholds.

### 5.12 Accessibility checklist

- WCAG 2.2 AA minimum; 2.1.1 keyboard, 2.4.3 focus order, 2.4.7 focus visible, 1.4.3 contrast, 1.4.11 non-text contrast, 4.1.2 name/role/value.
- Screen-reader-accessible diff, tool cards, plan, and approvals.
- Live regions for streaming; `aria-busy` during generation.
- No keyboard trap in terminal or editor; provide an escape.
- Zoom to 200% without loss of function; reflow at 320 px.
- Captions/transcripts for voice; text alternative for audio responses.
- Test with NVDA, JAWS, VoiceOver, and at least one screen reader user. CHI 2026 work on screen-reader programmers in the "vibe coding" era documents real barriers here.
- Reduced motion, high contrast, and color-blind simulation are part of the release checklist.

### 5.13 Internationalization and input

- IME-safe submission (see composer).
- RTL layout mirroring for panels, icons with direction, and diff gutters.
- Do not concatenate translated strings; use ICU messages with placeholders.
- Locale-aware number, currency, date, and token counts.
- Code and diffs stay LTR even in RTL UI.
- Font stack must cover CJK and monospace fallbacks; test line-height with CJK.

### 5.14 UI QA matrix

| Area | Test |
|---|---|
| Streaming | 50 Hz token stream, 10-minute session, no frame drops or memory growth |
| History | 1,000 messages, scroll to top and back, no re-highlight storm |
| Diff | 1 file / 100 files, 10 lines / 10k lines, rename, binary, CRLF |
| Approval | keyboard-only, screen reader, expired, denied, always-allow scope |
| Composer | IME (CJK), paste image, 10 MB attachment, offline, queue reorder |
| Palette | 5k commands, fuzzy typo, no results, OS glyphs |
| Theme | light/dark/high-contrast, 200% zoom, reduced motion |
| Failure | backend down, model timeout, tool crash, disk full, auth expiry |
| Mobile | approval bottom sheet, unified diff, push notification, read-only |

---

## Part VI — Terminal, extension, or fork?

### 6.1 First, disambiguate "connect our terminal"

Two different decisions get merged:

1. **Terminal as a product surface**: do we ship a CLI/TUI where the agent lives?
2. **Terminal as a tool**: does the agent get a PTY to run commands in the user's shell?

They are independent. Almost every product needs (2). Only some need (1). Building a terminal emulator is a third thing and rarely necessary.

### 6.2 The options

| Option | UX control | Time to v1 | Ongoing cost | Distribution | Ecosystem | Risk |
|---|---|---|---|---|---|---|
| **A. Headless engine + ACP** | Low (someone else's UI) | Weeks | Low | Broad: Zed, JetBrains, Neovim, Emacs, Eclipse, Kiro | Best | Depends on ACP adoption |
| **B. CLI/TUI client** | Medium | Weeks | Low | Developer-native | Good | Terminal UX limits (mouse, resize, paste, long chat) |
| **C. VS Code extension** | Low–medium | Weeks–months | Low–medium | Marketplace, huge | Best | API deliberately constrained; chat participant deprecated; custom chat layout limited |
| **D. Theia-based branded IDE** | High | Months | Medium (shared upstream) | Own installer + Open VSX | Good (runs VS Code extensions) | Platform learning curve; larger footprint |
| **E. VS Code fork** | Very high | Months | **High, permanent** | Own installer | Marketplace/licensing constraints | Rebasing every month; Microsoft extension breakage; governance risk |
| **F. Standalone editor** (CodeMirror/Monaco + xterm.js/node-pty + Tauri/Electron) | Very high | Months–years | High | Own installer | Must build everything | Editor, LSP, terminal, extensions, accessibility all become your problem |

### 6.3 Evidence on the fork path

- VS Code is an **extensible product**, not a platform: the extension API is intentionally limited to protect UX coherence. Branding, reshaping the workbench, overlays in built-in views, and a fully custom chat surface push past it ([Eclipse Foundation](https://blogs.eclipse.org/post/thomas-froment/why-cursor-windsurf-and-co-fork-vs-code-shouldnt)).
- Forks pay **permanent rebasing** against monthly VS Code releases, plus licensing and marketplace constraints. In 2025, Microsoft's C/C++ tooling stopped working in Cursor and other forks due to licensing/distribution enforcement — a concrete ecosystem risk.
- The VS Code **chat participant API is deprecated** and not supported in the Copilot harness; the recommended path is agent plugins, skills, MCP servers, and MCP Apps ([VS Code docs](https://code.visualstudio.com/api/extension-guides/ai/chat)). Betting a product on the old chat API is a dead end.
- `Code - OSS` is MIT, but the **Visual Studio Marketplace is a Microsoft service** with its own terms; the branded product adds proprietary assets and marketplace integration ([VS Code wiki](https://github.com/microsoft/vscode/wiki/Differences-between-the-repository-and-Visual-Studio-Code)).
- Theia is **not a fork**: it runs VS Code extensions, is vendor-neutral at the Eclipse Foundation, splits frontend/backend over JSON-RPC, and ships **Theia AI** with agents, MCP, Agent Skills, and a default harness ([Theia architecture](https://theia-ide.org/docs/architecture/), [Theia AI](https://theia-ide.org/docs/theia_ai/)).
- OpenSumi is another IDE framework with clean frontend/backend separation and VS Code extension compatibility, if we want a lighter platform than Theia.
- ACP is now implemented by Zed, JetBrains IDEs, Neovim, Emacs, Eclipse, Kiro, and others ([ACP clients](https://agentclientprotocol.com/get-started/clients)). That is real distribution without owning an editor.

### 6.4 Evidence on the terminal path

- Terminal agents win on three properties: the interface and the model share a representation (text), actions are visible in the same medium the user watches, and the barrier to entry is low ([Terminal Is All You Need](https://fictty.com/blog/why-the-terminal/), CHI 2026 workshop).
- Developers often want the agent **beside** the editor, not inside it; the terminal is the cheapest way to get that. The known costs are mouse support, resizing, copy/paste, and rendering a chat interface in a medium built for scrollback ([arnorhs.dev](https://arnorhs.dev/posts/2025-10-11/why-are-coding-agents-all-going-to-the-terminal/)).
- The practical conclusion: a TUI is a great **first client** and a poor **only client**. Expect to add a desktop/web surface later.

### 6.5 Terminal integration, if we do it

- **PTY**: `node-pty` on Node/Electron, `portable-pty` on Rust/Tauri. Windows uses **ConPTY** (Windows 10 1809+); winpty is gone.
- **Display**: `xterm.js` in the renderer, PTY in the main/backend process, IPC between them. This is exactly how VS Code, Hyper, Theia, and many others do it.
- **Shell integration**: emit/parse OSC 633 sequences (`A` prompt start, `B` prompt end, `C` pre-exec, `D` finished + exit code, `E` command line + nonce) so the agent knows command boundaries and exit codes instead of scraping text. The nonce prevents command spoofing ([VS Code shell integration](https://code.visualstudio.com/docs/terminal/shell-integration)).
- **Security**: every process launched from `node-pty` runs at the **same permission level as the parent**. Launch the PTY inside a container/sandbox; never expose a PTY server to the internet ([node-pty](https://github.com/microsoft/node-pty)).
- **Agent terminal tool**: return structured observations — command, cwd, exit code, stdout/stderr, duration, truncation marker — not a raw ANSI blob.
- **Sandboxing is separate from approval**: approvals decide *whether* to run; sandboxing restricts *what* it can touch. VS Code ships this distinction explicitly ([agent sandboxing](https://code.visualstudio.com/docs/agents/run/agent-sandboxing)). We need both.

### 6.6 Recommendation

**Build the engine headless first, then two thin clients, then decide on an IDE.**

1. **Engine** (weeks): OpenAPI/JSON-RPC server, sessions, tools, permissions, checkpoints, event stream. This is the asset.
2. **Terminal client** (weeks): cheapest surface, best for dogfooding, matches how developers already run agents.
3. **VS Code extension** (weeks–months): distribution and editor context without owning an editor.
4. **ACP server** (weeks): every ACP editor becomes a host; no fork required.
5. **Theia product** (months, only if needed): if we need a branded IDE with custom panes and full UX control, Theia is the platform-shaped choice, not a fork.
6. **Fork** (only on a specific trigger): if a required interaction is impossible in both the extension API and Theia, and we can staff a permanent rebase team. Never fork first.

### 6.7 Decision triggers

| If we need… | Choose |
|---|---|
| Fastest path to a usable agent | CLI/TUI on the headless engine |
| Distribution inside existing editor workflows | VS Code extension |
| Presence in Zed/JetBrains/Neovim/Emacs | ACP server |
| A branded IDE with custom panes, still leveraging the VS Code extension ecosystem | Theia |
| A specific UX that neither the extension API nor Theia can express, with budget for a rebase team | VS Code fork |
| A minimal embedded editor inside a larger product | CodeMirror 6 (modular, small) |
| An IDE-grade editor with batteries included | Monaco (heavier, workers, closest to VS Code) |
| Desktop shell with small bundle and OS-level integration | Tauri |
| Maximum ecosystem maturity and Node-native PTY | Electron |

### 6.8 Cost reality

- **TUI**: weeks; the hard part is not rendering, it is input handling, resize, and long-session state.
- **VS Code extension**: weeks to months; the hard part is the constrained chat UI and the deprecated participant API.
- **ACP**: weeks; the hard part is protocol conformance and capability negotiation.
- **Theia**: months; the hard part is learning the platform and customizing the workbench.
- **Fork**: months to first release, then a permanent tax. Budget a dedicated rebase engineer per major upstream cadence.
- **Standalone editor**: months to years; you are rebuilding editor, LSP client, terminal, extension host, accessibility, and theming.

---

## Repositories worth studying

| Repo | License / scale | Why it is world-class | What to take |
|---|---|---|---|
| **[sst/opencode](https://github.com/sst/opencode)** | MIT, ~198k★ | Client–server daemon: the TUI is just one client over an OpenAPI server; SQLite, persistent PTY, file watchers, generated SDK, desktop wrapper | The "one engine, many clients" architecture; headless `serve`; programmatic TUI driving used by IDE plugins |
| **[cline/cline](https://github.com/cline/cline)** | Apache-2.0, ~64k★ | Best-in-class Plan/Act split, per-action approval, checkpoints, diff review; ships as IDE extension, CLI, SDK, and parallel Kanban | Human-in-the-loop permission UX and checkpoint/rewind |
| **[OpenHands](https://github.com/OpenHands/OpenHands)** | MIT core, ~84k★ | EventStream as the central bus; Action/Observation model; Docker, remote, and Modal runtimes; browser environment | Runtime abstraction and sandboxed execution as a swappable component |
| **[Zed](https://github.com/zed-industries/zed)** | GPL-3.0; ACP Apache-2.0 | GPUI native performance; clean agent UI decomposition (`AgentPanel`, `ThreadView`, `AgentDiffPane`, `MessageEditor` with `@`-mentions); invented ACP | Agent UI decomposition and protocol-first agent integration |
| **[Aider](https://github.com/Aider-AI/aider)** | Apache-2.0, ~48k★ | Tree-sitter repo map ranked by a graph algorithm under a token budget; git-native workflow | Cheap, high-leverage codebase comprehension |
| **[block/goose](https://github.com/block/goose)** | Apache-2.0, ~53k★ | Desktop + CLI automation, MCP-native from day one | Desktop agent ergonomics and extension model |
| **[Void](https://github.com/voideditor/void)** | Apache-2.0, ~29k★, **archived** | Still the best reference for forking VS Code: custom FIM provider, `EditCodeService` that streams diffs token by token, IPC/CSP patterns | How to render edits as they stream |
| **[Continue](https://github.com/continuedev/continue)** | Apache-2.0, ~36k★, **read-only** | Config-as-code: models / rules / tools in YAML, shared blocks, secrets resolution | Declarative, version-controlled agent behavior |
| **[Tabby](https://github.com/TabbyML/tabby)** | Apache-2.0, ~34k★ | Self-hosted, completion-only server | Air-gapped / self-hosted FIM serving |
| **[Backboard Desktop](https://github.com/Backboard-io/Backboard-Desktop)** | MIT | Code-OSS-based IDE that runs `cli-ts` as a child process over stdio; worktrees, computer use, BYOS | "IDE shell + separate agent runtime" split |
| **[screenpipe](https://github.com/screenpipe/screenpipe)** | custom license, ~22k★ | Event-driven capture, accessibility-tree-first with OCR fallback, SQLite FTS5, localhost REST + MCP, markdown "pipes" | Screen memory as a queryable local substrate |
| **[Jan](https://github.com/janhq/jan)** | Apache-2.0 | Local chat + model hub + MCP + OpenAI-compatible `localhost:1337` API | Local-first desktop packaging and a local API |
| **[Open Interpreter Workstation](https://github.com/openinterpreter/interpreter-workstation)** | open source | Hard split: the runtime owns agent/harness/providers, the shell owns desktop/browser/policy | Core/UI separation and a provider-agnostic app-server protocol |
| **[AnythingLLM](https://github.com/Mintplex-Labs/anything-llm)** | MIT | Workspaces over documents, agents, MCP, scheduled jobs, Docker multi-user | Document/RAG workspace model |

Notes: Void, Roo Code, and Continue are archived or read-only — study them, do not build on them. Star counts drift.

---

## Reading order (if you only read five)

1. [Building an Agentic Coding Editor — architecture overview](https://science-prof-robot.github.io/agentic-editor-guide/90-overview-architecture.html) — the clearest end-to-end mental model: three latency modes, the context funnel, sandboxing, safety.
2. **opencode** — the daemon/SDK architecture you will probably copy.
3. **Cline** — permission and checkpoint UX.
4. **Aider's repo map post** ([link](https://aider.chat/2023/10/22/repomap.html)) — context engineering on a budget.
5. **ACP spec** ([agentclientprotocol.com](https://agentclientprotocol.com/get-started/introduction)) + **MCP docs** — the two protocols that decide whether the product is a silo or a platform.

---

## What separates world-class from a demo

- It **verifies** — runs tests, reads diagnostics, reports what it actually checked — instead of only generating.
- It **never blocks typing** and never lets a model silently mutate disk.
- It treats **permissions, audit, and undo** as product features, not afterthoughts.
- It is **headless-first**, so one engine serves the IDE, CLI, desktop app, and CI.
- It is **protocol-shaped** (MCP + ACP), so capability grows without forking the core.
- It **measures** — acceptance rate, retention, eval suites — rather than trusting vibes.

---

## Open questions for us

- Which shape are we building first — IDE-shaped, desktop-shaped, or the headless engine both consume? The repo's existing work suggests the engine-first route is cheaper, but it changes what the first demo looks like.
- Do we adopt ACP for agent hosting, or only MCP for tools? ACP buys editor interoperability but is still young for remote agents.
- What is our sandbox story on Windows (AppContainer/Job Object vs. WSL2 vs. container)? This is the highest-risk trust item and should be decided before any shell tool ships.
- Do we ship a VS Code fork (Void/Backboard path) or an extension plus a standalone shell? Forking buys control over the editor UX at a large maintenance cost.
- Which UI principle do we refuse to compromise on? If "the diff is the product," review UX gets staffed before chat polish. If "zero-latency typing," completion gets its own model and path before agent features.
- What is our ambient-interrupt policy for the desktop assistant — notify, question, or review by default — and what confidence threshold gates it?
- Do we measure perceived productivity, actual cycle time, or both? The 2025 RCT says they can diverge, so shipping on perceived speed alone is a known trap.
- Which backend component do we build first as a separately testable service — retriever, verifier, or sandbox? The research suggests verifier and sandbox are the trust bottlenecks; the product suggests retrieval is the quality bottleneck.
- What is our **success proxy** for the verifier? OpenHands found PR merge (AUC 0.58) and code survival (0.69) work better than benchmark labels, but both are sparse. We need to pick one and instrument it before training anything.
- Do we start with a **prompted small model** or a **trained critic**? v0 (prompted, transcript-only, deterministic checks first) is days; a trained critic is a data pipeline. The evidence says benchmark-only critics do not transfer, so v0 is the right first bet.
- Should the evaluator be **transcript-only** (Claude's choice) or allowed to run checks itself? Transcript-only is cheaper and safer; tool-enabled is more accurate but duplicates the sandbox and permission surface.
- Which client ships first — TUI, VS Code extension, or ACP? The engine is the asset; the TUI is the cheapest dogfood, the extension is the widest distribution, ACP is the cheapest way into Zed/JetBrains/Neovim.
- Do we build a branded IDE on **Theia** or stay a guest in other editors? Theia is the platform-shaped option; a VS Code fork is only justified by a specific interaction that neither the extension API nor Theia can express.
- For the terminal tool, do we adopt **OSC 633 shell integration** from day one, or start with plain PTY output and add command boundaries later? The former gives exit codes and command detection; the latter is simpler but makes the agent scrape ANSI.
