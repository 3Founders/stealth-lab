---
goal: update the phone field in the salesforce contact record with the new value from the email.
uses: 2
successes: 0
recorded_at: 2026-09-13T11:10:06.967087+00:00
---

tool_calls:
  - {"name": "api_search", "arguments": {"query": "salesforce contact update", "top_k": 5}}
  - {"name": "api_search", "arguments": {"query": "salesforce contact search by email", "top_k": 5}}
