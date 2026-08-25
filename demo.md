# demo.md — The 3-Minute "Earned Memory" Demo

Goal: one recording (GIF ≤60s cut + full video ≤3min) whose climax is a **provable refusal**. Everything shown must be real — receipts on screen, no staged JSON.

---

## 0 · Ground truth for this demo (v0.1 honesty rules)

- Refusal ships in **audit mode**: the agent is *not* blocked; `check_procedure` returns `WOULD_REFUSE` with cited reasons and the event lands in an audit log. **The demo says exactly that, on screen.** Overclaiming enforcement we don't ship yet would burn HN trust permanently.
- Every number visible (similarity scores, claim IDs, timestamps) comes from the live graph via real MCP calls.
- Fixture repo: fresh throwaway repo (`demo-fixture/`) with a scripted two-phase story. No customer data, no fake company names.

## 1 · Setup (scripted before recording)

```bash
docker compose up -d                      # postgres + stealthlab-mcp, migrations auto-run
python scripts/bootstrap_demo.py          # seeds fixture tasks, clears audit log
claude mcp add --transport http stealthlab http://127.0.0.1:8765/mcp \
  --header "Authorization: Bearer $STEALTHLAB_MCP_TOKEN" --scope local
```

Terminal: 18pt+ font, dark theme, ~110×32 panes (editor left / agent right). Recorder: OBS or `ffmpeg -f gdigrab`; export GIF at 12fps for README embed.

## 2 · Beat sheet

### Beat 0 — The pain (0:00–0:20)
Narration: *"Every coding agent user knows this: the same problem, re-solved from scratch."*
Action: run task once ("add pagination to the users endpoint"), watch it fumble through discovery. Cut fast — pain needs only 20 seconds.

### Beat 1 — Memory is born (0:20–0:55)
Narration: *"Now give it memory. Traces flow in through hooks; procedures get distilled with evidence."*
Action: show `ingest_trace` landing → terminal split showing graph counts tick up (`tasks: 3 → knowledge_nodes: 4 → procedures: 1`). Show the distilled procedure node: goal, steps, evidence links, applicability rule.

### Beat 2 — Reuse you can see (0:55–1:35)
Narration: *"Second session, similar bug. It doesn't start over — it cites what it learned."*
Action: new but related task; agent calls `retrieve_precedent`, on-screen response shows matched procedure + confidence + provenance chain (which past fix supports it). Agent solves visibly faster. Overlay timer comparison if honest (same-task-class, not doctored).

### Beat 3 — THE MOMENT: provable refusal (1:35–2:30)
Narration: *"Now break the world on purpose."*
Action: bump the dependency version that the stored procedure's precondition claims (`required_state`: `fastapi<0.100`). Re-run the original task.
On screen, `check_procedure` returns:

```json
{ "verdict": "WOULD_REFUSE",
  "procedure": "proc_pagination_v1",
  "reason": "precondition claim cl_17 'pydantic v1 compatible' superseded by cl_23",
  "evidence": ["changeset_09", "execution_41"],
  "capability_note": "0 failures recorded, environment changed" }
```

Narration: *"It won't blindly reuse last month's fix. It tells you which belief died, when, and why — and adapts instead of breaking your build."* Agent then solves it the new way. Audit-log line appears: `audit: WOULD_REFUSE proc_pagination_v1 (cl_17→cl_23)`.

### Beat 4 — The receipt (2:30–2:50)
`explain_failure` on a deliberately broken earlier step OR `explain_decision` on Beat 2: cause chain rendered as `event → observation → claim → procedure` path with IDs. Narration: *"Every answer carries its evidence. Ask it why, always."*

### Close (2:50–3:00)
Card: **StealthLab — agents that earn the right to remember.**
Sub-line: `pip install git+… · docker compose up · local-first · Apache-2.0`

---

## 3 · Capture checklist
- [ ] Fresh compose volumes (no stale graph rows leaking into counts)
- [ ] Clock overlay optional; keep cuts hard, no music (HN mutes it anyway)
- [ ] Show the MCP tool-call JSON at least twice (credibility > polish)
- [ ] Say "audit mode" out loud in narration AND caption during Beat 3
- [ ] Export: `demo.mp4` (1080p, full) + `demo.gif` (<8MB, beats 3 only) — GIF goes in README hero

## 4 · Fallbacks
| Failure | Fallback |
|---|---|
| Refusal loop not green by shoot time | Shoot Beat 3 as audit-log reveal only (still honest); ship video v2 after enforcement |
| Retrieval match looks weak on camera | Pre-tune threshold on fixture (documented override, like RETRIEVE_PRECEDENT_THRESHOLD note in server.py) |
| Latency ugly on camera | Cut waiting; never fake progress bars |

## 5 · After the shoot
Post full video on X + r/mcp; GIF into README + Show-HN first comment; clip Beat 3 alone (45s) as the standalone shareable.
