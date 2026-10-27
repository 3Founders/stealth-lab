# Kell Security Overview

Last updated: October 2026.

This is a plain summary of how Kell protects what you give it, and where it does not. It leaves out detail that would help an attacker, and it does not claim anything we do not have.

## Sign-in and access

- Sign-in is handled by an identity provider. Every request to the Kell service carries a signed token, which is checked for a valid signature, issuer, audience and expiry before anything is returned. Expired or malformed tokens are rejected.
- Access to stored data is checked on every request against who you are and which team you belong to. Private records are visible to their owner and, for a team, to the team's administrators as the team's settings allow.
- Team administrators can set budgets, turn Kell off for the whole organisation, and see an audit log of administrative actions.

## Secrets

- A team's model-provider key is stored encrypted, is never shown again after it is saved, and is used only to make the calls you ask for.
- Keys you keep on your own machine stay there. Kell refers to them by name and does not upload them.
- Before trace text is stored, strings that look like secrets are removed. This is pattern-based and is a safety net, not a guarantee. Do not rely on it to make confidential material safe to send.

## What leaves your machine

Kell is not a zero-egress product. When a task is looked up or routed, the prompt text and facts about your repository go to the Kell service, and the task and the files it needs go to the model providers configured for your workspace. The full list is on the [Subprocessors](/docs/legal/subprocessors) page.

## Your checks

Kell runs your tests before a routed change reaches your code. A passing check shows that the change passed the checks you set. It does not prove the change is correct or secure, so review changes in sensitive code.

## What we do not have

- No SOC 2, ISO 27001, HIPAA or PCI-DSS certification.
- No third-party penetration test.
- No published uptime history. The service is pre-release and runs without a service-level guarantee.

## Reporting a problem

If you find a security problem, tell us using the contact details at the bottom of this page, and give us reasonable time to fix it before you make it public. Please do not access other people's data, and do not disrupt the service while you investigate.
