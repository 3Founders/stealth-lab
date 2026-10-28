// An error body that says the provider account itself is out (balance, credit, billing, a daily or monthly quota), not a
// per-minute limit: retrying cannot help, and a run that keeps retrying spends its whole time limit on nothing (z.ai's
// "Insufficient balance or no resource package. Please recharge" on 13 of 24 replay runs, 2026-10-27).
// Used by the step agent (lib/agent/step_agent.mjs) and the Claude Code bridge (lib/anthropic_bridge.mjs).
export const OUT_OF_CREDIT = /insufficient (balance|credit|funds|quota)|recharge|out of credit|credit balance|billing|payment required|exceeded your current quota|daily (token )?(limit|quota)|(tokens?|requests?) per day/i;

export const outOfCredit = (status, text) => [402, 403, 429].includes(Number(status)) && OUT_OF_CREDIT.test(String(text || ""));
