# Kell Privacy Policy

Last updated: October 2026.

This explains what Kell collects, why, who else handles it, and what you can do about it.

## 1. What we collect

| What | Why we have it |
|---|---|
| **Account details**: email, a user id, how you sign in, and session tokens | To sign you in and keep you signed in |
| **Task lookups**: the prompt text your tool sends (in Cursor, the first 1,500 characters) and the facts file for your repository | To find relevant knowledge and decide where a task should go. We store a hash of the query, not the text |
| **Routed tasks**: the task and the files it needs, sent on to a model provider | To have a cheaper model attempt the task. Prompts and replies from these calls are not stored in our database |
| **Records of outcomes**: whether a check passed, which model was used, cost and timing | To show you results and to improve what Kell recalls for you |
| **For teams**: the company's provider key (encrypted), budgets, who made each call and when, and an audit log of administrative actions | To enforce budgets and show the company its usage |
| **Technical data**: standard request logs and, if turned on, error reports | To keep the service running and fix faults |

We do not collect payment cards, government identity numbers or biometric data.

## 2. What stays on your machine

The `.kell` folder in your repository, your tool settings and any provider keys you keep locally stay on your machine. Kell uploads from them only what is described above.

## 3. Sensitive material

Kell removes strings that look like secrets (cloud keys, bearer tokens, private-key blocks) from trace text before it is stored. This is pattern-based and can miss things. Do not rely on it to make confidential material safe to send. Choose carefully which repositories and prompts you use Kell with.

## 4. Who else handles your data

Kell uses the providers on the [Subprocessors](/docs/legal/subprocessors) page for hosting, sign-in, storage, and for the models that read your tasks. We do not sell your data and we do not use it to advertise to you.

## 5. Training

We do not use your private content to train models. If that ever changes, it will be a separate option you switch on, never a silent default.

## 6. Keeping it

- Account details are kept while your account is open.
- Records of outcomes and team usage are kept while your account or team is active.
- Backups are kept by our database provider for a limited time, then expire.

When you ask us to delete your data (see below) we delete your account details and your private records. We may keep what the law requires us to keep, and what we need to keep the service secure.

## 7. Your choices

You can ask us to:

- tell you what we hold about you,
- correct it,
- export it, or
- delete it.

Write to us using the contact details at the bottom of this page. We will answer within a reasonable time. If you are in a place whose law gives you further rights, those apply as well, and you can raise a complaint with your local regulator.

## 8. Children

Kell is a developer tool and is not directed at children. If you believe a child has given us personal data, tell us and we will delete it.

## 9. Moving data between countries

Our database is hosted in the United States, and our other providers process data in the United States and in other countries where they operate. By using Kell you accept that your data is handled there.

## 10. Changes

When we change this policy we will update the date above and, if the change matters, tell account holders before it takes effect.
