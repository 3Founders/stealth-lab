# How Wayfinder works

Wayfinder is a planning skill. It turns a loose, foggy idea into a **shared map** on the
repo's issue tracker, then works the map's **decision tickets** one at a time until the route
to a destination is clear. It does not execute the work itself — it produces the decisions
someone (human or agent) needs before they can execute.

## The core idea

A big effort usually can't be planned in one sitting, because most of it is still fog: you
know roughly where you're headed, but you can't yet state every decision precisely. Wayfinder
handles that by keeping two things separate at all times:

- **What's sharp enough to decide right now** → becomes a **ticket**.
- **What's still too vague to decide** → stays as **fog**, written down loosely so nobody
  forgets it's coming, but not forced into a ticket before it's ready.

Resolving a ticket often clears the fog just ahead of it, which turns more of that fog into
fresh tickets. The map is deliberately incomplete at every point in time — that's the design,
not a gap to fill immediately.

## The map

One issue (or, on a tracker without native issues, one file) per effort, holding:

- **Destination** — what reaching the end looks like: a locked spec, a decision, a completed
  migration. Named first, because it fixes the scope of everything else.
- **Notes** — the domain, standing preferences, and which skills every session should consult.
- **Decisions so far** — an index, not a store. One line per closed ticket: a gist of the
  answer plus a link to the ticket that holds the real detail. A decision lives in exactly
  one place.
- **Not yet specified** — the fog: questions you can tell are coming but can't phrase
  precisely yet.
- **Out of scope** — work consciously ruled outside the destination. Different from fog: fog
  is in scope but not sharp yet; out-of-scope work never graduates, no matter how sharp it
  gets, unless the destination itself is redrawn.

## Tickets

Each ticket is a child of the map, holding one question sized to about one working session.
Four types, split along one axis — does resolving it require a live human, or can an agent
resolve it alone:

| Type | Human needed? | What it's for |
|---|---|---|
| **research** | No (AFK) | Reading docs/APIs/knowledge bases to surface a fact a decision is waiting on. |
| **prototype** | Yes (HITL) | A cheap, rough, concrete artifact to react to, when "how should this look/behave" is the real question. |
| **grilling** | Yes (HITL) | Conversation. The default case — most architectural/design decisions land here. |
| **task** | Either | Manual work that has to happen before a decision *can* be made — nothing to decide, just something to do first (provisioning access, moving data, writing an inventory). The only type that does rather than decides. |

A ticket is **claimed** by assigning it to whoever's working it, before any work starts, so
concurrent sessions don't collide. **Blocking** uses the tracker's native dependency
mechanism where possible, so the human can see at a glance what's takeable. The **frontier**
is every ticket that's open, unblocked, and unclaimed — the actual edge of the known.

## Resolving a ticket

1. Claim it.
2. Work it — call whatever skill(s) the map's Notes specify (grilling, domain-modeling,
   research, prototype).
3. Record the answer directly on the ticket, close it.
4. Add one line to the map's Decisions-so-far pointing at it.
5. Check whether the answer clears any fog into new, sharp tickets — if so, create them.
   Check whether it reveals any ticket (this one or another) actually sits past the
   destination — if so, close it and note it under Out of scope instead of resolving it.

Only one ticket gets resolved per working session (research tickets are the exception — those
can run several at once in the background, since they don't need a live human).

## Two ways to invoke it

- **Chart the map** — starting from a loose idea. Pin down the destination, fan out
  breadth-first across the whole space to surface the tickets that are takeable now, create
  the map, create those tickets, sketch everything else into the fog. This session creates
  nothing beyond the map itself — it doesn't resolve anything.
- **Work through the map** — given an existing map, pick the next ticket (or take the one the
  user named), resolve it, record it, and stop.

## The one hard rule underneath all of this

**Wayfinder plans; it doesn't build.** Every ticket resolves a decision, not a slice of
implementation. The moment the urge shows up to just write the code instead of deciding how
it should work, that's the signal the map is finished — implementation starts outside
Wayfinder, informed by everything the map settled.
