# Handoff: parallel Wayfinder work on the Memory Substrate map

Two people, two laptops, one shared map at `.scratch/memory-substrate/` on branch
`research/claude-code-hooks`. Your part first, then your friend's part below it.

## YOUR FRIEND'S PART

### 1. Get the repo and the branch

```bash
git clone https://github.com/3Founders/stealth-lab.git
cd stealth-lab
git checkout research/claude-code-hooks
git pull origin research/claude-code-hooks
```

(If already cloned: skip `clone`, just `checkout` + `pull`.)

### 2. See what's already going on

Read `.scratch/memory-substrate/map.md` first — Destination, Notes, Decisions so far. Then
check which tickets are open and unclaimed:

```bash
grep -l "^Status:$" .scratch/memory-substrate/issues/*.md
```

(Files with no value after `Status:` are unclaimed. Files with `Status: claimed` or
`Status: resolved` are spoken for — don't touch those.)

As of this handoff, the frontier (open, unblocked, unclaimed) is tickets **06** (Canonical
trace model), **09** (Isolation and auth), and **17** (Migration mechanism). **Avoid ticket
02** (Substrate/domain seam) — that one's already claimed and in progress on the other
laptop.

### 3. Claim a ticket — and push the claim immediately, before doing any real work

This is the coordination step that prevents both of you working the same ticket. Pick one,
e.g. ticket 06:

```bash
git pull origin research/claude-code-hooks   # make sure you have the latest claims first
```

Open `.scratch/memory-substrate/issues/06-canonical-trace-model.md`, change:

```
Status:
```

to:

```
Status: claimed
```

Then immediately:

```bash
git add .scratch/memory-substrate/issues/06-canonical-trace-model.md
git commit -m "Claim ticket 06: Canonical trace model"
git push origin research/claude-code-hooks
```

Don't batch this with your actual work — push the claim by itself first, so it's visible
before you sink time in.

### 4. Work the ticket

Open Claude Code in the repo and run:

```
/wayfinder continue the memory-substrate map, work ticket 06 (or whichever you claimed)
```

Wayfinder will read the map and the ticket, pull in whatever skills the ticket type needs
(`grilling` + `domain-modeling` for most tickets, `research` for research-type, `prototype`
for prototype-type — the map's Notes section says which), and walk you through it the same
way this side has been doing.

### 5. Record the resolution and push it

When the ticket is resolved (Wayfinder will append an `## Answer` section to the ticket file,
close it, and add one line to the map's Decisions-so-far):

```bash
git pull origin research/claude-code-hooks   # pick up anything resolved on the other laptop meanwhile
git add .scratch/memory-substrate
git commit -m "Resolve ticket 06: Canonical trace model"
git push origin research/claude-code-hooks
```

If `git pull` reports a conflict in `map.md`'s "Decisions so far" section, it's always
trivial — both additions are just new lines, keep both and remove the conflict markers.

### 6. Repeat

Go back to step 2 — re-check the frontier (it grows every time a ticket resolves and
unblocks its dependents), claim the next one, work it, push it.
