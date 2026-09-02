# Stealth Lab — frontendv1

Minimal Next.js frontend for the Stealth Lab procedural knowledge backend.

## Stack

- Next.js 16 (App Router, Turbopack) + React 19 + TypeScript
- Tailwind CSS 4, Inter, shadcn-style primitives (button, input, badge, card, skeleton, separator)

## Run

```bash
npm install
cp .env.local.example .env.local   # point NEXT_PUBLIC_API_URL at the FastAPI backend
npm run dev
```

Backend contract: see `../.scratch/frontend_backend_contract.md`.
