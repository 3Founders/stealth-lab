# Sandboxing: what to run agent-written code in, and who pays

**Findings and a recommendation. 2026-11-22.** Researched against vendor pricing pages and comparison
surveys, then checked against how this repo actually executes a step (`app/execution/step_binding.py`,
`behavioral_validation.py`). Prices are the vendor's own, read at the dates cited; treat every number as a
rate card, not a quote.

---

## 0. The answer, first

**Recommendation: local-first, cloud only where it pays.**

| Where the code runs | Who it is for | Sandbox |
|---|---|---|
| The user's own machine | Anyone running a Way on their own files | **No sandbox** — approval gates instead. This is what Cursor, Aider, Claude Code and your own desktop already do. |
| A kell server, on a stranger's request | Someone who paid ₹150 and has no install | **Cloud sandbox, but only for steps that execute code.** Start on **Agent37** (cheapest, gVisor, SOC 2); keep the call behind an abstraction so E2B or self-hosted microsandbox is a config change. |

And the answer to *"should each procedure declare a suggested sandbox?"* — **yes, but it should declare a
requirement, not a vendor.** Details in §6; this distinction matters more than the vendor choice.

---

## 1. What this repo actually does today

`app/execution/step_binding.py` names the executor for each kind of step:

| Binding | Executor | Isolation **today** |
|---|---|---|
| `command` / `sandbox` | `deterministic` | ❌ **A timeout and a temp dir. Nothing else.** |
| `mcp_tool` | `tool` — HTTP to a remote MCP server | ✅ by construction (the code is not yours) |
| `model` | `frontier` | ✅ N/A — no code executes |
| `adapter` | `api` | ✅ N/A |
| `binary` / `wasm` / `container` | **no executor** — fails loudly | ⚠️ storable, not runnable |

And `behavioral_validation.py` runs:

```python
subprocess.run([sys.executable, driver_path], timeout=…, cwd=repo_root, env=dict(os.environ))
```

**That `env=dict(os.environ)` is the finding that matters most in this document.** The subprocess receives the
backend's full environment — `DATABASE_URL`, the Razorpay secret, every API key. There is no container, no
seccomp, no network namespace, no user separation. It runs as the same user on the same filesystem.

*(Fixed during this research: the child now receives an allowlist, not the whole environment. See §10 step 1.
The test `tests/test_generated_code_env_offline.py` fails if anyone widens it back.)*

**The second finding is worse, because it is a false assurance.** `app/execution/adapters.py`'s
`SubprocessSandboxExecutor` declares `network_access=False` — and says, in its own runtime warning:

> "SubprocessSandboxExecutor: network_access=False is NOT enforced -- this executor cannot actually block
> network calls. If the generated code makes one, it will succeed."

So the thing named "sandbox" in the executor registry **cannot block egress**. Any procedure that relies on
`network_access=False` to stop a step exfiltrating data is relying on a promise the code does not keep. This is
not a theoretical gap; it is a live, self-documented one.

So the honest starting position is **"we are already unsandboxed"**. Everything below is about deciding where
that is acceptable, not about adding a first defence.

---

## 2. Why "just run it" is defensible on a laptop and indefensible on a server

Three of the market's own tools made this call explicitly. From the research on coding-agent editors:

> "Cursor, Copilot agent mode, Continue, Aider, and Claude Code in their default local mode run commands
> directly on your machine in your repo directory. **There is no kernel-level jail.** Safety comes from
> allowlists/denylists … and human approval gates. This is cheap and zero-latency but **the isolation is
> social, not technical**."

That trade is **correct** when the person running the code is the person who owns the machine, and can see the
command before it runs. It becomes **wrong** the moment the code arrives from a stranger over a network and
executes on infrastructure they do not own — which is exactly what a paid, hosted Way is.

The isolation research is blunt about the boundary:

> "Once the code comes from an LLM or a user, the shared kernel is the wrong default boundary."

And on which boundary to pick:

> "Choose the boundary by who wrote the code and who shares the host. If one customer's agent-written code can
> run on the same kernel as another customer's, **the boundary is too weak**."

Your situation: a paying stranger's Way runs on your server, beside other paying strangers. **That is the case
the research names directly.**

---

## 3. What "sandbox" means as an isolation level

These are not interchangeable, and the marketing flattens them.

| Level | What it is | Escape cost | Fits |
|---|---|---|---|
| **Plain container** | namespaces + cgroups, **shared kernel**, ~350 syscalls reach the host | one kernel exploit = node-wide escape | your own code, through CI. **Not untrusted code.** |
| **gVisor** | userspace kernel (Sentry) intercepts syscalls; no hardware virt needed | strong, no KVM required | dense untrusted execution on shared nodes |
| **Firecracker microVM** | its own kernel, hardware boundary | strongest practical; needs KVM | untrusted code at scale — the standard choice |
| **Kata** | real VM per pod, Kubernetes-native | strongest; heaviest, slowest cold start | per-tenant isolation inside k8s |
| **Wasm** | capability-based, no syscalls at all | very strong, but a *different runtime* | pure-compute leaves only |

**The relevant correction to make internally:** a "container" is a **packaging tool, not a security boundary**
for untrusted code. If anyone proposes Docker as the agent sandbox, that is the sentence to quote.

gVisor's own documentation adds the caveat worth repeating: *"one should not assume that the mere use of
virtualization hardware makes a system more or less secure"* and *"a sandbox is not a substitute for a secure
architecture."* A sandbox bounds the blast radius. It does not fix a step that was given the wrong credentials.

---

## 4. The providers, on the numbers

### 4.1 Rate cards (vendor pages, 2026)

| | **Agent37** | **E2B** | **Modal** | **Daytona** | **Fly Sprites** | **Vercel** | **Cloudflare** | **Blaxel** |
|---|---|---|---|---|---|---|---|---|
| Isolation | **gVisor** | Firecracker | gVisor (VM opt.) | LXC container | Firecracker | Firecracker | container in VM | microVM |
| vCPU rate | **$0.0011 / vCPU-hr** | $0.0504 | $0.1419 / core-hr | $0.0504 | $0.0385 / CPU-hr | n/p | n/p | mem-based |
| RAM rate | **$0.00096 / GB-hr** | $0.0162 / GiB-hr | $0.0240 | $0.0162 | $0.021875 | n/p | n/p | $0.0414 / GB-hr |
| Billed idle | disk only | no (paused free) | **yes** (60 s) | **yes** | **no** (~30 s) | — | sleeps 10 min | **no** (25 ms resume) |
| Monthly, 2 vCPU/4 GB always-on | **$4.76** | ~$9.44 | $13.56 | $9.44 | — | $19.43 | $11.34 | — |
| Self-hostable | ❌ | ✅ **Apache-2.0** | ❌ | ❌ closed | ❌ | ❌ | ❌ | ❌ |
| SOC 2 | **Type II** | — | — | — | — | — | — | — |

**Cost of 1,000 real builds** (clone, install, typecheck — ComputeSDK benchmark, quoted by Agent37):

| Rank | Provider | Cost |
|---|---|---|
| 1 | **Agent37** | **$0.99** |
| 2 | Blaxel | $9.81 |
| 3 | E2B | $13.96 |
| 4 | Vercel Sandbox | $27.64 |
| 5 | Modal | $27.99 |
| 6 | Cloudflare Sandbox | $37.98 |

**Agent37 is ~14× cheaper than E2B on that benchmark**, and ~9% cheaper than a Hetzner VPS running the same
thing. That is a large enough gap to change the recommendation (§7.4), so it belongs in the table rather than
in a footnote.

**Its isolation claim, quoted exactly:** *"Every sandbox is enforced by gVisor, with its own filesystem and a
hard disk quota. Tenants can never see each other."* Plus *"Egress is firewalled per sandbox: the public internet
and nothing else. Not the host, not private networks."* That is gVisor (not Firecracker) — strong, but a
**userspace kernel**, not a hardware boundary. See §3: gVisor is a good choice and not the strongest one.

**The catch, stated plainly:** the benchmark is **Agent37's own page quoting a third party**, the rate is a
prepaid-balance model with no self-host path, and the company is young. Cheap is not the same as durable — §4.2
is the same lesson from the other direction.

**The single most useful number here:** a default E2B sandbox (2 vCPU, 0.5 GiB) costs

```
(2 × $0.000014 + 0.5 × $0.0000045) × 300 s  ≈  $0.009 per 5-minute run
```

On Agent37's rates the same 5-minute run is **≈ $0.0006** — under a tenth of a cent. Either way a sandbox
allocation is a rounding error against ₹150: see §5.

### 4.2 The one that should shape your architecture

**Daytona raised $24M in February 2026 and closed its source in June 2026** — four months. From the analysis:

> "Daytona announced a $24M Series A … to build 'agent-native compute' … On June 11, the same company moved its
> production codebase closed-source, freezing its public repository … the license funeral."

And the pattern across the tier:

> "The vendors most willing to sell you sandboxes are, with one big exception, the vendors least willing to let
> you run them yourself."

E2B is the exception: **Apache-2.0 runtime, self-hostable**, ~$32M raised. Modal is closed, valued at $4.65B,
SDK only. In April 2026 OpenAI blessed seven providers at once (Blaxel, Cloudflare, Daytona, E2B, Modal,
Runloop, Vercel) — which is good for portability and a warning that **the sandbox is becoming a commodity
primitive, not a moat**.

**Practical consequence: do not build kell's execution layer against one vendor's SDK shape.** The OpenAI
Agents SDK's answer is the right one — a **Manifest** describing the workspace (mounts, output dirs, storage)
that any provider satisfies. Adopt that idea regardless of which vendor you start with.

---

## 5. The cost question — and why it is smaller than it looks

You asked whether the sandbox cost should be carried by each procedure's price, or pushed onto the user by
making them install the desktop app.

**The arithmetic says the desktop-install argument is weak.**

| Item | Cost |
|---|---|
| E2B, one 5-minute run (2 vCPU, 0.5 GiB) | **$0.009** |
| Same, 20-minute run | $0.036 |
| Licence price | ₹150 ≈ **$1.70** |
| Sandbox as a share of a licence | **0.5% – 2%** |

Compare that with the cost of the alternative. Making someone install a desktop app to save two cents:

- costs a download, an install, a sign-in, and a machine that may not have the right runtime;
- **moves the security problem onto their machine** — and a paid Way from a stranger running unsandboxed on a
  customer's laptop is the *worst* version of this design, because now the blast radius is their `~/.ssh`;
- only works for people who have the app — which excludes the entire web buyer of a ₹150 PDF.

So: **charge the sandbox into the price as a small line, and keep the desktop as a choice, not a tax.**
The desktop's real argument is *latency and local files* — near-zero spawn, no upload, works on their repo —
not cost. Framing it as "cheaper" invites the question "cheaper by 2%?" and loses.

**One exception worth naming:** a Way whose step is *long-running* (a build, a training run, an hour of
browsing) makes the arithmetic flip. A 1-hour 2 vCPU sandbox is **$0.109** — still small, but 20 of them a day
is **$65/month**, and a step that *forgets to exit* is billed until the session cap. So the rule is not "cost
is negligible", it is **"cost is negligible per short run and unbounded per runaway run"** — which is a
**max-duration budget per step**, not a pricing decision (see §7).

---

## 6. Should each procedure declare a sandbox?

**Yes — but declare the requirement, never the vendor.**

If a Way says `sandbox: e2b`, then:

- moving vendors is a data migration across every stored Way;
- a vendor's closure (see Daytona) silently breaks stored procedures;
- the price quoted at sale time is tied to one rate card;
- self-hosted deployments cannot run it at all.

Declare **what the step needs**, and let the platform map it:

```jsonc
// on the step, alongside `effect` (docs/backend_composability.md §3)
"sandbox": {
  "needs": "none" | "process" | "network" | "gpu",
  "max_seconds": 300,
  "network": "deny" | "allowlist",     // deny by default; list the hosts if allowed
  "writes": "none" | "workspace",      // never "host"
  "reads_secrets": []                  // names, injected at the egress proxy, never inside the VM
}
```

**`needs` is the field that decides cost.**

| `needs` | Runs where | Example |
|---|---|---|
| `none` | in-process, no sandbox at all | `model`, `adapter`, `mcp_tool` calls |
| `process` | local subprocess (desktop) or microVM (server) | run a script, parse a file |
| `network` | microVM **with egress allowlist** | fetch a page, call a third-party API |
| `gpu` | microVM with a GPU (E2B has none; Modal does) | training, inference |

**Most Ways need `none`.** This is the FAOP insight from `docs/backend_composability.md`: `op_tool` and
`op_compute` are distinct effects precisely so a step that calls a registered tool never needs a shell. A Way
that gathers sources and writes a report is `mcp_tool` + `model` + pure data work — **zero sandbox minutes**.

So the suggested-sandbox field is not overhead on every procedure. It is a **marker on the minority of steps
that genuinely execute code**, and because it is `none` for most, the cost estimate stays honest.

### 6.1 Does the price include it?

**Yes — but as a computed line, not a guess.** The Way page already has a **Run cost** row
(`run_cost_paise`). Compute it as:

```
run_cost = model_tokens_cost + tool_calls_cost + sandbox_cost
sandbox_cost = Σ over steps (needs ≠ "none") of  max_seconds × rate
```

with `rate` from whichever provider the deployment configured. A Way that declares no sandboxing step shows
`₹0` for that component — and if the whole run cost is unknown, the page already prints *"has not recorded a
typical run cost yet"* rather than a number. Keep that honesty; do not estimate.

---

## 7. Recommended architecture

### 7.1 Split the harness from the compute

This is the OpenAI Agents SDK's own framing and it is correct here:

> "Agent systems should be designed assuming prompt-injection and exfiltration attempts. **Separating harness and
> compute** helps keep credentials out of environments where model-generated code executes."

Concretely for kell:

- **The harness** (planner, model calls, evidence writes) runs in the backend — it holds the credentials.
- **The compute** (a step with `needs: process|network`) runs in a microVM that holds **no credentials**.
  Secrets are injected at the **egress proxy**, not inside the sandbox: E2B's "secrets vault injects credentials
  at the egress proxy so the sandbox never holds them"; Cloudflare "injects authorization headers in the Worker".

**This single change removes the `env=dict(os.environ)` problem** described in §1, and it is worth doing
**whether or not** you ever adopt a paid sandbox provider.

### 7.2 Default network to deny

Both E2B and Modal allow deny-by-default egress. A step that declares `network: "deny"` and then tries to fetch
something should **fail**, not silently succeed. That failure is information: it means the Way's declared
requirement was wrong.

### 7.3 Bound every run

Every sandboxed step gets `max_seconds`, enforced by the provider *and* by kell. Without it, a runaway step is
billed until the session cap (E2B 24 h, Daytona indefinite). The bound is also what makes `run_cost` a number
rather than a range.

### 7.4 Provider choice

| If | Use |
|---|---|
| **Starting now, cheapest** | **Agent37** — gVisor, ~14× cheaper than E2B on the build benchmark, SOC 2 Type II, egress firewalled to the public internet only. Accept that you cannot self-host it. |
| **Need a hardware boundary** | **E2B** — Firecracker (own kernel) rather than gVisor, Apache-2.0, self-hostable later. ~14× the cost of Agent37. |
| **Cost at volume with GPU** | Modal (E2B and Agent37 have no GPU; Modal does) |
| **Sovereignty / India data residency** | **microsandbox** — Apache-2.0, self-hosted microVMs, <100 ms boot. 9.1k stars, but **self-described beta**. |
| **Never** | a container as the security boundary for untrusted code |

**The honest tension, and how to hold it:**

Agent37 is the cheapest and its isolation is real (gVisor, per-tenant firewall, SOC 2). E2B is ~14× dearer and
gives a stronger boundary (Firecracker: hardware, not userspace) **plus the exit route**.

At ₹150 a licence, **14× of almost nothing is still almost nothing** — the absolute gap per run is fractions of
a cent (§5). So the cost argument does *not* settle this. What settles it is §4.2: a vendor that closed its
source four months after raising $24M. **A provider you cannot leave is a risk you cannot price.**

**Recommendation: start on Agent37 (cheap, real isolation, fast to adopt), and keep the sandbox call behind the
`needs` abstraction in §6 so moving to E2B — or to self-hosted microsandbox — is a config change, not a
migration.** The abstraction is the insurance; the vendor is the temporary choice.

---

## 8. Local vs cloud: the honest trade

| | Local (desktop) | Cloud sandbox |
|---|---|---|
| Cost | ₹0 | ~$0.009 / 5-min run |
| Latency | near-zero spawn | 100 ms–2 s cold, + repo clone/install |
| Isolation | **social** (approval gates) | **technical** (kernel boundary) |
| Blast radius of a bad step | **the user's own machine** | the VM |
| Works for a web buyer with no install | ❌ | ✅ |

The last row is decisive for the product question: **a ₹150 PDF way sold on the web cannot require a desktop
install.** The desktop is for people doing real work on their own repo. The web buyer needs a server-side run,
and that run needs a kernel boundary because the code is not theirs.

---

## 9. Pitfalls

1. **Calling a container a sandbox.** Shared kernel, ~350 syscalls. Use it for your own code; not for a stranger's.
2. **Passing `os.environ` into the child.** Already happening (§1). Hands the subprocess your database and
   payment keys. Allowlist, or inject at the egress proxy.
3. **Billing an idle sandbox.** Modal bills 60 s after last input; Daytona bills while started. The cheapest rate
   card can produce the biggest bill on bursty work — the exact shape agent work has.
4. **No `max_seconds`.** A step that forgets to exit is billed to the session cap.
5. **Pinning a vendor into stored data.** A procedure that names `e2b` becomes a migration when you move.
6. **Assuming the provider will stay.** Daytona, four months. Self-hostability is a requirement, not a nicety.
7. **Sandboxing when you did not need to.** Most steps are `model`/`tool` calls. Paying for microVMs on those is
   waste, and it slows the common path.
8. **A sandbox as the only control.** The research's own warning: a sandbox is not a substitute for a secure
   architecture. It bounds damage; it does not authorise.

---

## 10. What to build, in order

1. **Stop passing the full environment** to any subprocess. ✅ **Done in this change** —
   `_SUBPROCESS_ENV_ALLOWLIST` in `behavioral_validation.py`, pinned by `tests/test_generated_code_env_offline.py`.
2. **Stop advertising `network_access=False` where it is not enforced.** Either implement it or refuse it
   loudly. A false assurance is worse than none: it makes someone skip a control they would otherwise add.
3. **Add `needs` to the step schema** — `none | process | network | gpu`, default `none`. Nothing runs differently
   yet; it makes the requirement explicit and countable.
4. **Compute `run_cost_paise`** as model + tool + `max_seconds × rate` over the steps that declare a need.
5. **Route `needs: process|network` to E2B** (or microsandbox self-hosted), with deny-by-default egress and
   secrets at the proxy.
6. **Report the count**: of the Ways in the library, how many declare a sandbox need at all. This is the number
   that tells you whether the cloud cost is real or theoretical — and the predicted answer is *few*.

---

## 11. One sentence

**Most Ways never touch a sandbox, the ones that do cost about a cent a run — so declare `needs` per step rather
than pinning a vendor, keep credentials out of the VM, put a `max_seconds` on every run, and choose a provider
you could self-host, because this tier has already shown it will close its source four months after raising.**

---

## Sources

- Agent37 pricing & isolation — https://www.agent37.com/ · benchmarks — https://www.agent37.com/benchmarks
- Blaxel pricing — https://blaxel.ai/pricing
- Fly.io, *Sprites vs E2B vs Modal vs Daytona* — https://fly.io/learn/sprites-vs-e2b-vs-modal-vs-daytona/
- Fly.io, *Where to Run Agent Code: AI Agent Sandbox Providers Compared* — https://fly.io/learn/agent-sandbox-providers/
- E2B pricing — https://e2b.dev/pricing · cost formula — https://docs.e2b.dev/faq/calculate-sandbox-price
- Modal pricing — https://modal.com/pricing
- Sandboxer, *Sandbox Isolation Compared: Containers, gVisor, Kata, MicroVMs, Wasm* — https://sandboxer.com/guides/sandbox-isolation-technologies-compared
- Archil, *What isolation do agent sandboxes actually use?* — https://archil.com/article/what-isolation-do-agent-sandboxes-actually-use-firecracker-gvisor-and-containers-compared
- bex.co, *Daytona Raised $24M in February, Then Closed Its Source in June* — https://bex.co/blog/2026/10/14/daytona-sandbox-vendor-buy-vs-build
- bex.co, *gVisor vs Kata vs Firecracker* — https://bex.co/blog/2026/09/22/gvisor-vs-kata-vs-firecracker-agent-sandbox-isolation
- OpenAI, *The next evolution of the Agents SDK* — https://openai.com/index/the-next-evolution-of-the-agents-sdk/
- microsandbox — https://github.com/superradcompany/microsandbox/
- Docker, *Compare local and cloud sandboxes* — https://docs.docker.com/ai/sandboxes/cloud/local-vs-cloud/
- *Building an Agentic Coding Editor*, execution & sandboxing — https://science-prof-robot.github.io/agentic-editor-guide/11-execution-sandbox.html
