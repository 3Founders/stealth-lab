/** What each backend refusal reason code means, in plain terms. An unknown code is shown as-is, never hidden. */
const REASONS: Record<string, string> = {
  kill_switch: "The organisation's kill switch was on",
  no_policy: "The organisation has no policy yet (nothing is allowed)",
  provider_not_allowed: "Provider is not on the allow-list",
  model_not_allowed: "Model is not on the allow-list",
  tool_not_allowed: "Tool is not on the allow-list",
  data_class_not_allowed: "Data class is not on the allow-list",
  unpriced_unit: "The unit has no declared price, so its cost can't be bounded",
  monthly_budget_zero: "Monthly budget is $0 (no spend allowed)",
  user_daily_budget_zero: "Per-user daily budget is $0 (no spend allowed)",
  monthly_budget_exceeded: "The monthly budget would be exceeded",
  user_daily_budget_exceeded: "The user's daily budget would be exceeded",
};

export const reasonText = (code: string): string => REASONS[code] ?? code;
