# Alerts and notifications for a web assistant like kell (research, 2026-11-02)

For the founder. It answers "what should notify a person, through which channel, when, and what should page US when the
machinery breaks" - for the /chat rooms (mentions, @kell, in-tab agents, relay tasks), the wallet, and the hosted
gateway. Sources: vendor and platform documentation plus practitioner design guides (S), a little primary product
documentation (V), and the OpenAlex literature list at the bottom. Not legal or security advice.

## The question, split in two

- **Product notifications** go to the person using kell: a mention, an agent's answer to a long task, an approval
  request, a payment confirmation, a wallet running low, a sign-in from a new device.
- **Operational alerts** go to US: the gateway failing, provider errors, spend escaping enforcement, tasks stuck
  mid-flight. Different audience, different rules (SRE discipline, not UX polish).

## What the pattern consensus says (S)

1. **Match the channel to the urgency, and keep each channel's job distinct.** The common four-way split: the in-app
   inbox holds what is worth keeping; toasts confirm an action just taken; badges are glanceable counts; push is for
   genuinely urgent, time-boxed things. In-app needs no opt-in and works only while engaged; push and email reach the
   person when they have left (OneSignal channel guide; Courier notification-center guide).
2. **Batching is the highest-impact fix for notification fatigue.** One "10 new messages in #general" beats ten
   separate notifications of identical information value. People scan, they do not read: front-load who, what and why
   so a notification makes sense in about two seconds (Courier; Reuters Institute 2025 survey: 79% say they feel
   overwhelmed by mobile notifications of all kinds, and OS vendors now summarise or silence on our behalf - so volume
   we push is volume an OS may bury).
3. **Ask for push permission in context, two-step, never on arrival.** First a dialog we control that says what the
   person gets ("tell me when my agent finishes"), then the browser's own prompt. Asking on landing is the documented
   anti-pattern and gets the site silenced (web.dev permission UX; ChatGPT's scheduled tasks do exactly this: Push,
   Email, or both, per task, with the browser permission flow explained).
4. **Real preferences, per type and per channel, plus quiet hours in the person's own time zone.** Store the IANA zone
   (Asia/Kolkata), not a fixed offset; send digests at a local hour; never wake someone at 3am for a non-urgent digest
   (Courier; Oricato local-time guide). Slack's defaults are the reference model: notify for DMs, thread replies,
   direct mentions and keywords; show mere channel activity as bold/badge only.
5. **An inbox people can triage beats a feed they must clear.** GitHub's model: Done, Saved, Unsubscribe, the reason
   each notification exists shown as a label ("participating" vs "watching"), grouping and filters. "Why am I getting
   this?" must be answerable on the notification itself.
6. **Errors are notifications too, and they carry at most one action.** Surface a structured failure - what kind,
   retryable or not - with cause-specific copy; offer Retry only when retrying can actually succeed (an auth failure
   gets no retry button); otherwise offer a check-status action (Threadplane AgentError pattern; this is also already
   our own rule in the chat: model refusals are shown verbatim, @kell's 403 names kell as the refuser).
7. **Money alerts speak the customer's unit.** Threshold notifications (credit below a percentage or a floor),
   system notifications (plan starts/ends), and offset notifications (N days before expiry) are the standard trio
   (Metronome). For kell this plugs into the decision already made: balances and budgets are shown in kell credits,
   never in $.
8. **Webhook/alert delivery is at-least-once, so receivers must be idempotent.** Retries with exponential backoff and
   jitter, a stable JSON shape, and duplicates expected (Metronome). The same rule our own wallet webhook already
   follows (payment_ref as the idempotency key).
9. **Operational alerts page on symptoms the customer feels, not on causes.** Google SRE's rule: alert on significant
   error-budget burn (fast-burn pages, slow-burn tickets); precision and recall of alerts are the metrics. Applied to
   an LLM gateway (LangWatch's rule set): page on customer impact now (5xx rate, an open circuit) and on spend escaping
   enforcement (missing stream usage, dropped ledger rows); lead with indicators (fallback rate, failing control-plane
   calls); deliberately do NOT alert on provider 429s (fallback absorbs them), raw provider latency (it is the
   provider's share), or per-request cost (spend is read from the ledger).
10. **Proactive assistants live or die on trust, and trust is damaged most by badly-timed proactivity.** The literature
    on proactive conversational assistants ties trust to the assistant taking the initiative only when it is clearly
    welcome and explainable (OpenAlex: The Role of Trust in Proactive Conversational Assistants, 2021, 52 citations).
    Practically: every proactive notification states why it exists, and can be turned off without giving up the product.

## Agent-specific patterns (S, I)

- **Notify when ready, let the person leave.** The documented pattern for long-running agent work (Restate's
  notify-when-ready; ChatGPT's scheduled tasks): the task resolves a durable promise, and a notification handler sends
  when it resolves - a late subscriber still gets the resolved result. For kell this is the relay task lifecycle we
  already have (needs_approval -> queued -> claimed -> done/failed), minus the "notify me" step: today a person must
  keep the tab open and watch the pulse.
- **Approval requests are the top of the urgency ladder.** A task waiting for the person's approval blocks an agent -
  that is a push/email case, not a badge. Failed tasks are second: the person asked for something and it did not
  happen.
- **Digests are the answer to ambient activity.** Room activity the person is not mentioned in should never ping;
  it belongs in an optional daily digest (email or inbox), sent at a local morning hour. The micro-randomized trial
  literature (OpenAlex: the Drink Less study, 2023) shows notification effects decay and must be tuned by experiment -
  so ship digests behind a flag and measure opens before making them louder.

## Security notifications (S)

The Google model: alert on important account actions (sign-in from a new device), suspicious activity, and blocked
sensitive actions; the alert itself carries device, time and location, plus what to do next ("was this you?"). For
kell: new-device sign-ins and (when we ever hold anything sensitive server-side) key or connection changes. The BYOK
vault is client-side, so key events there can only be surfaced in-app, honestly ("a script on this device could...").

## Map to kell: what exists, what is missing

Exists today:

- In-app bell with per-room preferences, OS notifications, chime, badge, quiet-ish controls (components/chat/
  NotificationBell.tsx, NotificationSettings.tsx, lib/chat/notify.ts) - the Slack-style mention model, already
  close to the consensus default.
- Model-refusal surfacing in chat (the model's own sentence, @kell's wallet 403 named as kell's refusal).
- Sync state shown in the profile widget (Syncing / Offline / Not syncing) rather than notified.
- Relay task lifecycle in the room with Approve/Cancel, copy and retry actions on messages (added 2026-11-02).

Gaps, in build order:

1. **Notify on agent outcomes** (the notify-when-ready step): when a relay task finishes or fails, and when a task
   waits for approval, tell the person through the bell + OS notification, and email if they are not in the app.
2. **Email as the second channel** for out-of-app moments: agent finished/failed, approval needed, payment confirmed,
   wallet low. Needs SPF/DKIM/DMARC alignment from day one and a strict transactional-only policy (never marketing
   through the same stream).
3. **Wallet threshold + plan expiry (offset) notifications, in kell credits** ("your balance dropped under 5 kell
   credits"; "your plan ends in 3 days"). Server-side, idempotent, at most one per threshold crossing per day.
4. **Push permission, in context**: offer web push at the moment a person hands a long task to an agent ("tell me when
   this finishes?"), via our own dialog first. Not on landing.
5. **Security alerts**: new-device sign-in email; later, unusual API usage on a session token.
6. **A daily digest** (optional, off by default): unread room activity, finished tasks, a credits line.
7. **Ops lane** (separate audience - us): gateway 5xx rate, open circuit, fallback rate, ledger-write gaps, tasks
   claimed-but-not-updated for over N minutes (stuck agent), provider status changes. Symptom-first, SLO-shaped; the
   429/latency rules stay out on purpose.
8. **Triage**: once volume exists, give the bell Done/Saved and a "why am I getting this" reason, per the GitHub model.

## Anti-patterns we are explicitly avoiding (S, I)

- A notification per message (batch instead), or per keystroke of agent progress.
- Push permission on landing; pre-checked notification boxes; an opt-out that means "leave".
- $ figures in any notification (kell credits only, per the 2026-11-02 decision).
- Alerting on provider 429s or provider latency in the ops lane; alerting on things a retry or fallback already
  handles.
- Burying an approval request in a badge.

## OpenAlex (S; a thin field - this is mostly engineering practice, not research)

- The Role of Trust in Proactive Conversational Assistants (2021, 52 citations) - proactivity must be welcome and
  explainable or it costs trust.
- The cost of email interruption (2001, 161 citations) and Investigating the impact of IT-mediated information
  interruption on emotional exhaustion in the workplace (2020, 57) - the classical grounding for batching and quiet
  hours.
- Human Interruption Management in Workplace Environments: An Overview (2020).
- Understanding Push Notification Fatigue: User Tolerance and Management Behavior in Mobile Applications (2026);
  Leveraging User Activity Insights to Enhance Notifications Overload in a Knowledge-Sharing Platform (2025).
- How notifications affect engagement with a behaviour-change app: a micro-randomized trial (2023) - notification
  effects decay and should be tuned experimentally.
- Echoes of the Day: daily contexts and smartphone push experiences (2024).

## Decisions this doc asks for

1. Confirm the channel ladder: in-app always; email for out-of-app agent outcomes and money; web push only via an
   in-context offer, per task.
2. Confirm digests OFF by default, and that room-ambient activity never pings.
3. Pick the wallet thresholds (as kell credits) and the expiry offset (suggest 3 days).
4. Confirm the ops alert list above is ours to build next, distinct from customer notifications.
