# `.stealth/` editor (React)

A minimal React frontend for hand-editing `.stealth/*.md` projection files
(claims.md / procedures.md / implementations.md / goals.md / run.md /
exploration.md). Same functionality as `backend/scripts/stealth_md_editor.py`'s
plain-HTML version -- this is a React client for the exact same backend API,
not a second server.

## Run

```bash
# terminal 1 -- the backend (from backend/)
python scripts/stealth_md_editor.py --port 8767

# terminal 2 -- this app
npm install
npm run dev
# open http://localhost:5173/?repo_path=C:/path/to/your/repo
```

Every save goes through the real backend, which writes the file
(`atomic_write`), logs a real audit entry (`record_stealth_edit`), and
regenerates `.stealth/ledger.md` -- no logic duplicated here, this is a
thin client.

Plain-text editing, not structured: there is no real parser for these
pipe-format content pages today (only `goal_run.md` has one), so a
structured form editor here would misrepresent what's actually parseable.

---

<details>
<summary>Vite/React template notes (scaffolding boilerplate)</summary>

This template provides a minimal setup to get React working in Vite with HMR and some Oxlint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Oxc](https://oxc.rs)
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/)

</details>
