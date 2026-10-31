// The step-by-step setup shown on /connect. Kept in step with docs/new_user_setup.md (the same steps, for someone who has never used kell).

export type Step = { title: string; body?: string; code?: string; codeLabel?: string; list?: string[]; note?: string };

/** The standing instruction for Claude on the web (Settings, Account, Instructions for Claude). */
export const CLAUDE_WEB_PROMPT =
  "When I start a new task, first call the kell tool find_ways with a self-contained statement of the task and installed = the names of my enabled connectors. Then follow its reply. " +
  "If it has a \"tools\" field: use my connector if one is listed; if it lists one with via_kell, use it through kell's use_tool; otherwise do the task as well as you can and offer me the connector_url to add (never claim a tool I don't have). " +
  "If it has a \"delegate\" field, or a part of the task is a self-contained summary, explanation, draft, extraction or reformatting, call the kell delegate tool with the task as I put it plus the context it needs (not an outline of the answer) and use the answer only if its check says good; say in one line that it was delegated. " +
  "Do it yourself for deep multi-step reasoning or anything with private, secret or personal data. Skip kell for thanks and small talk.";

export const CLAUDE_CODE_NEEDS = [
  "Windows, macOS or Linux.",
  "A Claude Pro, Max, Team or Enterprise plan.",
  "A Google or GitHub account, to sign in to keळ.",
  "Node.js 18.17 or newer. Check with node --version; if it is missing or lower, install the LTS version from nodejs.org and open a new terminal.",
];

export const CLAUDE_CODE_STEPS: Step[] = [
  {
    title: "Open a terminal",
    list: ["Windows: press the Windows key, type PowerShell, press Enter.", "macOS: press Cmd+Space, type Terminal, press Enter.", "Linux: open your terminal app."],
    note: "Use this terminal for every command below. Open a new one when a step says so.",
  },
  {
    title: "Install Claude Code, if you don't have it",
    code: "npm install -g @anthropic-ai/claude-code",
    codeLabel: "Install Claude Code",
    body: "Then type claude and follow its sign-in to your Claude account. When you see the Claude prompt, type /exit.",
  },
  {
    title: "Install keळ",
    code: "npm install -g kell-mcp",
    codeLabel: "Install keळ",
    body: "The same on every system. On macOS or Linux, if it says permission denied (EACCES), fix npm's global folder permissions and run it again; don't use sudo. Then close the terminal, open a new one, and type kell --help; it should print keळ's commands.",
  },
  {
    title: "Run the setup check",
    code: "kell setup",
    codeLabel: "Run the setup check",
    body: "It adds keळ to Claude Code (and to other coding tools it finds, such as Cursor), then prints a checklist: each item is ok or missing, with the command that fixes it. On a new computer, sign-in and the model key are missing. The next two steps fix them.",
  },
  {
    title: "Sign in",
    code: "kell login",
    codeLabel: "Sign in",
    list: [
      "A browser page opens. If it doesn't, copy the link the terminal prints into your browser.",
      "Choose Continue with Google or Continue with GitHub, and sign in.",
      "On the keळ page, press Allow.",
      "The browser says you can close it, and the terminal says you are signed in.",
    ],
  },
  {
    title: "Add the model key",
    body: "The cheap models need a key from General Compute. Paste it where it says <your key>; keep the quotes on Windows. Then close the terminal and open a new one. Never paste the key into a chat, an issue or a file in a repository.",
    code: "Windows:\nsetx GENERAL_COMPUTE_API_KEY \"<your key>\"\n\nmacOS / Linux (use ~/.bashrc if your shell is bash):\necho 'export GENERAL_COMPUTE_API_KEY=<your key>' >> ~/.zshrc",
    codeLabel: "Add the model key",
    note: "If your company gave you a keळ invite instead of a key, skip the key and run the command from the invite: kell join <invite> --url <...> --api <...>. Your company's account then pays for the models.",
  },
  {
    title: "Check again",
    code: "kell setup\nkell doctor",
    codeLabel: "Check again",
    body: "kell setup should now end with ready, and kell doctor should say the server answers. If an item is still missing, run the command it prints next to it.",
  },
];

export const CLAUDE_CODE_TRY = {
  intro: "Go to a project folder that has tests, run cd <your project folder>, start claude, and send these one at a time (replace the parts in angle brackets with real names from your project).",
  rows: [
    ["what does <a function name> in <a file> do?", "A short pause, then an answer marked as written by a cheap model and checked. Claude itself barely runs."],
    ["fix the failing test in <test file>", "A cheap model tries the fix in a separate copy and runs the tests. If they pass, the change is applied and a short report appears. If not, Claude takes over."],
    ["check that the home page still renders in a real browser", "Before answering, Claude gets a note from keळ naming a browser tool, either one you have or one to add, with the command to add it."],
    ["make me a logo image for this project", "keळ's own image tool makes a picture (up to 30 a day)."],
    ["thanks!", "Nothing extra: keळ stays out of small talk."],
  ] as Array<[string, string]>,
  outro: "Starting a request with !opus sends it straight to Claude, with no keळ.",
};

export const CLAUDE_CODE_UNDO = [
  "Undo the last change a cheap model applied: kell undo (kell undo --list shows them all).",
  "Pause keळ for one terminal: $env:STEALTHLAB_HOOK_ROUTING = \"off\" on Windows, export STEALTHLAB_HOOK_ROUTING=off on macOS/Linux, then start claude from that terminal.",
  "Remove it completely: kell uninstall. Your own Claude Code settings and hooks stay.",
];

export const CLAUDE_WEB_STEPS: Step[] = [
  {
    title: "Add the keळ connector",
    body: "Use a computer browser for these steps. The phone app uses the connector once it is added.",
    list: [
      "Go to claude.ai and sign in.",
      "In the left sidebar choose Customize, then Connectors.",
      "Choose Add, then Add custom connector.",
      "Name: kell",
      "Remote MCP server URL: the connector address above, ending in /mcp.",
      "Choose Continue. Leave Sign in now and Register automatically as they are (both say Detected), then choose Add.",
    ],
    note: "On a Team or Enterprise plan, an owner of your organization may need to add the connector first. Ask them to do the same steps in the organization settings.",
  },
  {
    title: "Connect and sign in",
    list: [
      "Next to keळ, choose Connect.",
      "A keळ page opens. Choose Continue with Google or Continue with GitHub, sign in, and press Allow.",
      "You are back on claude.ai, and keळ lists its tools: Find proven ways, Delegate to a cheap model, Report the tools a task used, Find more tools and Run a discovered tool.",
    ],
  },
  {
    title: "Let keळ's tools run without asking each time",
    body: "On the same keळ connector page, set the tool permissions to Always allow, for every group shown. Otherwise Claude stops and waits for your click on every keळ call.",
  },
  {
    title: "Tell Claude when to use keळ",
    list: ["Click your name (bottom left), then Settings.", "Under Account, find Instructions for Claude.", "Paste the text below exactly, then choose Save changes."],
    code: CLAUDE_WEB_PROMPT,
    codeLabel: "Instruction for Claude on the web",
  },
  {
    title: "Check that keळ is on in your chats",
    body: "Start a new chat. Choose + (next to the message box), then Connectors, and check that kell is switched on.",
  },
];

export const CLAUDE_WEB_TRY = {
  intro: "Send these in a new chat, one at a time.",
  rows: [
    ["What's the difference between OAuth access tokens and refresh tokens? Keep it short.", "Claude calls Find proven ways, then Delegate to a cheap model. The answer says it was delegated and checked."],
    ["Which open issues on omni-us/jsonargparse mention dataclasses?", "Claude answers as best it can, and offers a GitHub connector with its address to add."],
    ["Make me a hero banner image for my landing page: a photorealistic mountain lake at sunrise, wide format.", "Claude calls Run a discovered tool with keळ's image tool, and the picture appears in the chat."],
    ["Thanks!", "No keळ call."],
  ] as Array<[string, string]>,
  outro: "When keळ suggests a tool that it can run for you (marked \"through kell\"), the first use shows a one-time link. Open it, read what will be shared, and press Allow. After that the tool runs straight from the chat. keळ runs only tools that read public information, never ones that send, post or pay.",
};

export const TROUBLE: Array<[string, string]> = [
  ["kell is not recognized / command not found", "Open a new terminal. If it still fails, run the install step again."],
  ["404 Not Found while installing keळ", "The package is not published yet; tell the keळ team."],
  ["Every request in Claude Code pauses several seconds before anything happens", "keळ was set up through npx rather than npm install -g. Run npm install -g kell-mcp, then kell setup again."],
  ["kell setup says the key is not set, after adding it", "Open a new terminal (Windows reads a new key only in new terminals), then run kell setup again."],
  ["The browser sign-in page says \"Sign-in isn't connected\" or shows an error", "Send the keळ team a screenshot of that page."],
  ["claude.ai says it couldn't reach the server when adding the connector", "Check that the address ends in /mcp."],
  ["Claude never calls keळ on claude.ai", "Check the instruction was saved and keळ is switched on in the chat. Then send \"call the kell find_ways tool\" once."],
  ["Claude waits for a click on every keळ call", "Set the tool permissions to Always allow."],
  ["A keळ tool call on claude.ai is refused right after an update", "Customize, Connectors, kell, Disconnect, then Connect again, so claude.ai sees keळ's newest tools."],
  ["A cheap model's change in Claude Code looks wrong", "Run kell undo."],
];
