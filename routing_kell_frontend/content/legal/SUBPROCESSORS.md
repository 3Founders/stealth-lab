# Subprocessors

Last updated: October 2026.

These are the outside companies that can handle your data when you use Kell, and what each is for. We list a provider here before it starts receiving your data.

## In the data path

| Provider | What it does for Kell | What it can receive |
|---|---|---|
| **Neon** | Hosts our database (United States) | Everything Kell stores about you and your team |
| **Supabase** | Sign-in | Your email, user id and session tokens |
| **Google Cloud (Vertex AI)** | Embeddings and judging of text, to find relevant knowledge | The task description and knowledge text sent for matching |
| **Cloudflare (R2)** | Object storage for large records | Records that Kell stores as files, with secret-like strings removed |
| **Our hosting provider** | Runs the Kell service | Requests in transit, and service logs |

## Model providers for your tasks

When a task is routed, the task and the files it needs go to the model providers configured for your workspace. For a team, the administrator chooses them and connects the company's own key, and that provider then acts for the company. For an individual, the providers are the ones you set up in the Kell console. Requests to Anthropic or another provider you already use go through your own login with that provider, not through Kell.

## Optional

These receive data only if we turn them on:

| Provider | What it does | What it can receive |
|---|---|---|
| **Sentry** | Error reports | The error, its stack trace, browser and operating system, the page path without its query string |
| **Plausible** | Cookieless page analytics | Page views and a few coarse events; no personal profile |
| **Fallback embedding and judging providers** | Used only if the first provider fails | The same text the first provider would have received |

## What this page does not say

- It does not say where every provider processes data. Several operate in the United States and in other countries.
- It does not say what contract each provider has signed with us. Their terms govern what they do with the data they receive.

## Changes

We will update this page before a new provider starts receiving customer data, and we will tell account holders when a provider that receives source code is added.
