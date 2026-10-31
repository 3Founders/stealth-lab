# Set up kell: Claude Code and Claude web

For someone who has never used kell. Follow the steps in order and type exactly what is shown. It takes about
15 minutes: 10 for Claude Code, 5 for Claude web. You can set up only one of them.

**For the kell team, before you share this page:**
- The npm package `kell-mcp` must be published. Until then, step A3 fails with "404 Not Found"
  (see [handoff_claude_code_launch_2026-10-30.md](handoff_claude_code_launch_2026-10-30.md), section 2).
- The site's one-line installers (`/install.sh`, `/install.ps1`) run kell through `npx`, so the hooks start npx on
  every prompt. That's why this page uses `npm install -g` (see [revisit.md](revisit.md)).
- Decide where a pilot user's model key comes from, (a) or (b) in that handoff, section 3, and fill in step A6.

---

## What kell does

- **In Claude Code**, kell looks at each request before Claude starts. If a cheap open model can do the work, kell
  hands it over: answers, small fixes with tests, summaries. A check confirms the result. Everything else goes to
  Claude as usual. kell also suggests the right outside tool (a browser, docs or GitHub server, and so on) when a
  task needs one.
- **In Claude web** (claude.ai), kell is a connector. Claude asks it what has worked for similar tasks, which tool
  fits, and whether a cheap model can write part of the answer. kell can also make images.

## What you need

- A computer running Windows, macOS or Linux.
- A **Claude Pro, Max, Team or Enterprise** plan. Claude web connectors need a paid plan.
- A Google or GitHub account, to sign in to kell.
- For Claude Code only: **Node.js 18.17 or newer**. Check by typing `node --version` in a terminal. If the command is
  not found or the version is lower, install the "LTS" version from https://nodejs.org, then open a new terminal.

---

## Part A: Claude Code

### A1. Open a terminal

- **Windows:** press the Windows key, type `PowerShell`, press Enter.
- **macOS:** press Cmd+Space, type `Terminal`, press Enter.
- **Linux:** open your terminal app.

Use this terminal for every command below. Open a new one when a step says so.

### A2. Install Claude Code, if you don't have it

Type:

```
npm install -g @anthropic-ai/claude-code
```

Then type `claude`, and follow its sign-in to your Claude account. When you see the Claude prompt, type `/exit`.

### A3. Install kell

Type (the same on every system):

```
npm install -g kell-mcp
```

On macOS or Linux, if it says "permission denied" (EACCES), see https://docs.npmjs.com/resolving-eacces-permissions-errors-when-installing-packages-globally
and run it again. Don't use `sudo`.

**Close the terminal and open a new one**, so the `kell` command is found. Type `kell --help`; it should print
kell's commands.

### A4. Run the setup check

```
kell setup
```

It adds kell to Claude Code (and to any other coding tools it finds, such as Cursor), then prints a checklist: each item is either ok or missing, with the command that fixes it.
On a new computer two items are missing: **sign-in** and the **model key**. The next two steps fix them.

### A5. Sign in

```
kell login
```

1. A browser page opens. If it doesn't, copy the link the terminal prints into your browser.
2. Choose **Continue with Google** or **Continue with GitHub**, and sign in.
3. On the kell page, press **Allow**.
4. The browser says you can close it, and the terminal says you are signed in.

### A6. Add the model key

The cheap models need a key from General Compute. **(kell team: say here where a pilot user gets it.)**
Paste the key where it says `<your key>`; keep the quotes on Windows.

**Windows:**

```
setx GENERAL_COMPUTE_API_KEY "<your key>"
```

**macOS / Linux** (zsh is the default on macOS; use `~/.bashrc` if your shell is bash):

```
echo 'export GENERAL_COMPUTE_API_KEY=<your key>' >> ~/.zshrc
```

**Close the terminal and open a new one.** Never paste the key into a chat, an issue or a file in a repository.

If your company gave you a kell **invite** instead of a key, skip the key and run the command from the invite:
`kell join <invite> --url <...> --api <...>`. Your company's account then pays for the models.

### A7. Check again

```
kell setup
kell doctor
```

`kell setup` should now end with **ready**. `kell doctor` should say the server answers. If an item is still missing,
run the command it prints next to it.

### A8. Try it

Go to a project folder that has tests, and start Claude Code:

```
cd <your project folder>
claude
```

Send these one at a time, replacing the parts in angle brackets with real names from your project:

| Send this | What you should see |
|---|---|
| `what does <a function name> in <a file> do?` | A short pause, then an answer marked as written by a cheap model and checked. Claude itself barely runs. |
| `fix the failing test in <test file>` (when one fails) | A cheap model tries the fix in a separate copy and runs the tests. If they pass, the change is applied and a short report appears. If not, Claude takes over. |
| `check that the home page still renders in a real browser` | Before answering, Claude gets a note from kell naming a browser tool, either one you have or one to add, with the command to add it. |
| `make me a logo image for this project` | kell's own image tool makes a picture (up to 30 a day). |
| `thanks!` | Nothing extra: kell stays out of small talk. |

Starting a request with `!opus` sends it straight to Claude, with no kell.

### A9. If you want to undo, pause or remove it

- Undo the last change a cheap model applied: `kell undo` (`kell undo --list` shows them all).
- Pause kell for one terminal: `$env:STEALTHLAB_HOOK_ROUTING = "off"` on Windows, `export STEALTHLAB_HOOK_ROUTING=off`
  on macOS/Linux, then start `claude` from that terminal.
- Remove it completely: `kell uninstall`. Your own Claude Code settings and hooks stay.

---

## Part B: Claude web (claude.ai)

Use a computer browser for these steps. The phone app uses the connector once it is added, but adding it is easier
on a computer.

### B1. Add the kell connector

1. Go to https://claude.ai and sign in.
2. In the left sidebar choose **Customize**, then **Connectors**.
3. Choose **Add**, then **Add custom connector**.
4. **Name:** `kell`
5. **Remote MCP server URL:** `https://mcp.kell.org.in/mcp`. Type the whole address, ending in `/mcp`.
6. Choose **Continue**. Leave **Sign in now** and **Register automatically** as they are (both say Detected), then
   choose **Add**.

On a Team or Enterprise plan, an owner of your organization may need to add the connector first. Ask them to do
steps 2-6 in the organization settings.

### B2. Connect and sign in

1. Next to kell, choose **Connect**.
2. A kell page opens. Choose **Continue with Google** or **Continue with GitHub**, sign in, and press **Allow**.
3. You are back on claude.ai, and kell lists its tools: *Find proven ways*, *Delegate to a cheap model*, *Report
   the tools a task used*, *Find more tools* and *Run a discovered tool*.

### B3. Let kell's tools run without asking each time

On the same kell connector page, set the tool permissions to **Always allow**, for every group shown. Otherwise
Claude stops and waits for your click on every kell call.

### B4. Tell Claude when to use kell

1. Click your name (bottom left), then **Settings**.
2. Under **Account**, find **Instructions for Claude**.
3. Paste this text exactly, then choose **Save changes**:

> When I start a new task, first call the kell tool find_ways with a self-contained statement of the task and installed = the names of my enabled connectors. Then follow its reply. If it has a "tools" field: use my connector if one is listed; if it lists one with via_kell, use it through kell's use_tool; otherwise do the task as well as you can and offer me the connector_url to add (never claim a tool I don't have). If it has a "delegate" field, or a part of the task is a self-contained summary, explanation, draft, extraction or reformatting, call the kell delegate tool with the task as I put it plus the context it needs (not an outline of the answer) and use the answer only if its check says good; say in one line that it was delegated. Do it yourself for deep multi-step reasoning or anything with private, secret or personal data. Skip kell for thanks and small talk.

### B5. Check that kell is on in your chats

Start a new chat. Choose **+** (next to the message box), then **Connectors**, and check that **kell** is switched on.

### B6. Try it

Send these in a new chat, one at a time:

| Send this | What you should see |
|---|---|
| `What's the difference between OAuth access tokens and refresh tokens? Keep it short.` | Claude calls *Find proven ways*, then *Delegate to a cheap model*. The answer says it was delegated and checked. |
| `Which open issues on omni-us/jsonargparse mention dataclasses?` | Claude answers as best it can, and offers a GitHub connector with its address to add. |
| `Make me a hero banner image for my landing page: a photorealistic mountain lake at sunrise, wide format.` | Claude calls *Run a discovered tool* with kell's image tool, and the picture appears in the chat. |
| `Thanks!` | No kell call. |

When kell suggests a tool that it can run for you (marked "through kell"), the first use shows a one-time link. Open
it, read what will be shared, and press **Allow**. After that the tool runs straight from the chat. kell runs only
tools that read public information, never ones that send, post or pay.

---

## If something goes wrong

| What you see | What to do |
|---|---|
| `kell` is not recognized / command not found | Open a new terminal. If it still fails, run step A3 again. |
| "404 Not Found" during A3 | The package is not published yet; tell the kell team. |
| Every request in Claude Code pauses several seconds before anything happens | kell was set up through `npx` rather than A3. Run `npm install -g kell-mcp`, then `kell setup` again. |
| `kell setup` says the key is not set, after A6 | Open a new terminal (Windows reads a new key only in new terminals), then run `kell setup` again. |
| The browser sign-in page says "Sign-in isn't connected" or shows an error | Send the kell team a screenshot of that page. |
| claude.ai says it couldn't reach the server when adding the connector | Check that the address ends in `/mcp`: `https://mcp.kell.org.in/mcp`. |
| Claude never calls kell on claude.ai | Check B4 was saved and B5 shows kell switched on. Then send "call the kell find_ways tool" once. |
| Claude waits for a click on every kell call | Do B3. |
| A kell tool call on claude.ai is refused right after an update | Customize → Connectors → kell → Disconnect, then Connect again (B2), so claude.ai sees kell's newest tools. |
| A cheap model's change in Claude Code looks wrong | `kell undo`. |

For anything else, send the kell team what you typed, what you saw, and (for Claude Code) the last lines of
`~/.stealthlab/hooks.log`.
