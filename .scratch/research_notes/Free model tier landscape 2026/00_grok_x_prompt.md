# Grok / X prompt — free-tier landscape 2026

Ready to paste into Grok (xAI) as-is. It is written for a model with live X search.
No fabrication clause is included: no invented URLs, IDs, quotes, or numbers.

```text
TASK
You have live X/Twitter search. Find posts ANNOUNCING, CHANGING, RAISING, CUTTING, or KILLING
FREE TIER OFFERINGS (free API quota, free credits, free GPU hours, free coding-agent usage,
free inference endpoints, free trial compute) across three groups of companies.

OUTPUT: a structured TABLE, not prose. Run 40-60 real X queries. Report the actual query
strings you ran at the end so coverage is auditable.

TIME WINDOW
Primary: 2026-03-01 through today (roughly the last 3-6 months). Use since:/until:.
If a post is OLDER than that but the offer it describes is still materially current
(a free tier that is still live, or a cut whose cut-off date has not passed), include it
and tag the row OLDER-BUT-CURRENT. If a free tier was REMOVED inside the window, that is
in-window even if the announcement predates it — follow the thread.

X OPERATORS — USE THEM EXPLICITLY, NOT KEYWORD SEARCH ALONE
  from:handle          restrict to one account
  min_faves:50         reach filter (lower to 5 for small labs)
  min_retweets:10      propagation filter
  since:2026-03-01  until:2026-09-28
  filter:links          posts that link out (docs/pricing posts)
  "exact phrase"       quoted phrase
Combine, e.g.:
  from:OpenAI (free OR trial OR credits OR "free tier" OR pricing OR deprecated OR sunset) filter:links
  (from:AnthropicAI OR from:xai OR from:GoogleDeepMind) ("free tier" OR "free credits" OR sunset) min_faves:50
  (from:runpod OR from:coreweave OR from:crusoe OR from:nebius) (free OR credits OR "trial credit") since:2026-03-01
  (from:AnthropicAI OR from:OpenAI) (deprecating OR sunset OR "end of" OR discontinu*) min_retweets:10
  (from:modal_labs OR from:baseten OR from:replicate OR from:fal_ai) (free OR "$0" OR "no credit card") min_faves:20

GROUP 1 — MODEL PROVIDERS / LABS (verify each handle; correct me if wrong)
OpenAI @OpenAI | Anthropic @AnthropicAI | Google DeepMind @GoogleDeepMind, Google AI @GoogleAI |
xAI @xai | Mistral @MistralAI | Cohere @cohere | AI21 @AI21Labs | Meta AI @AIatMeta |
DeepSeek @deepseek_ai | Moonshot/Kimi @Kimi_Moonshot | Zhipu/GLM @Zai_org |
Alibaba/Qwen @Alibaba_Qwen | Tencent @tencentcloud | MiniMax @MiniMax__AI |
NVIDIA @nvidia @NVIDIAAI | Amazon @awscloud @AmazonScience | Databricks @Databricks |
IBM @IBM @IBMCloud | Groq @GroqAI | Cerebras @CerebrasSystems | SambaNova @SambaNovaSystems |
Perplexity @perplexity_ai | Writer @Writer | Reka @RekaAI | Liquid AI @LiquidAI |
AI Labs @AILabs | Sakana AI @SakanaAI | Prime Intellect @PrimeIntellect |
Nous Research @NousResearch | Hugging Face @huggingface | Replicate @replicate |
Together AI @TogetherAI | Fireworks @FireworksAI | OpenRouter @OpenRouterAI |
Cloudflare @Cloudflare | GitHub @github | GitLab @gitlabhq

GROUP 2 — HYPERSCALERS + GENERAL CLOUDS
Google Cloud @GoogleCloud | AWS @awscloud | Microsoft Azure @Azure | Oracle Cloud @OracleCloud |
IBM Cloud @IBMCloud | Alibaba Cloud @Alibaba_Cloud | Tencent Cloud @tencentcloud |
Cloudflare @Cloudflare | DigitalOcean @DigitalOcean | Hetzner @Hetzner_Online | Vultr @Vultr |
Contabo @contabo | Scaleway @scaleway | OVH @ovhcloud | Fly.io @flydotio | Railway @railwayapp |
Render @Render | Koyeb @Koyeb | Vercel @vercel | Netlify @Netlify | Heroku @heroku

GROUP 3 — NEOCLOUDS / AI-FIRST GPU + INFERENCE HOSTS
Lambda Labs @lambdalabs | RunPod @runpod | Vast.ai @vast_ai | CoreWeave @coreweave |
Crusoe @crusoe | Nebius @nebius | DataCrunch @DataCrunch | Paperspace @paperspace |
Baseten @baseten @BasetenHQ | Modal @modal_labs | Replicate @replicate | fal.ai @fal_ai |
Segmind @segmind_ai | DeepInfra @DeepInfra | Novita @novita | Hyperbolic @hyperbolic |
Together AI @TogetherAI | Fireworks @FireworksAI | Anyscale @Anyscale | Predibase @Predibase |
Weights & Biases @WeightsandBiases | Lepton @LeptonAI | Groq @GroqAI | Cerebras @CerebrasSystems |
Lightning AI @LightningAI | Colab @GoogleAI | Kaggle @Kaggle

DISCOVER THE REST
The lists above are not exhaustive. Also search for hiring/funding-signal accounts and
regional or new-entrant clouds (e.g. "neocloud" "GPU cloud" free credit announcement)
and add any account you surface that has a free-tier change. Name each account you added
and why.

REQUIRED COLUMNS FOR EVERY ROW
| # | Company | Account (@handle) | Post date (exact, YYYY-MM-DD) | Post URL or tweet ID |
| What changed (new tier / raise / cut / cut-off date / sunset) | Effective limits if stated | Terms flag | Official pricing/docs link | Verified? |
- <=25-word VERBATIM quote per post, in its own column or directly under the row.
- "Post date" must be the post's own date, not a thread-parent or reply date.
- If you cannot get a URL/ID, write "not found" — never construct one.
- "Official pricing/docs link" must be the vendor's own page, not a blog aggregator.

TERMS FLAGS — mark each explicitly where stated
  CREDIT CARD required to start | EXPIRY (free credit burns off after N days/months) |
  ONE ACCOUNT ONLY / sharing or multi-account banned | OUTPUT MAY NOT BE USED TO TRAIN MODELS |
  REGION-LIMITED | RATE-LIMITED (requests/min, TPM) | VERIFICATION / ID required

SECTION A — NEW TIER, RAISES, AND IMPROVEMENTS
Table of everything that got bigger or newly free in the window. One row per change.

SECTION B — CUTS AND SUNSETS  (do not compress this section)
The highest-value signal is what was REMOVED. A stale blog list is worse than useless, so
lead with removal and deadline data. One row per cut, with the announced cut-off date and
whether that date has already passed. Explicitly note if a vendor removed the free tier
without announcement, only via docs edit — flag it as DOCS-ONLY CHANGE, UNANNOUNCED.

SECTION C — UNVERIFIED / RUMOR
Claims that exist only on X: reposts, screenshots of a pricing page, "someone says",
unattributed threads, screenshots of Discord/Slack messages, engagement-bait threads.
Split into RUMOR (a change is claimed, no official confirmation found) and
SCREENSHOT-ONLY (an image shows a number, no text source). For every row, state what
official page you checked and what it said. If you found no official confirmation, say
so — do not promote a rumor into Section A.

SECTION D — TOP 10 ACTIONABLE NOW
Ranked. For each: the offer, the account and post it came from, and one sentence on
WHY IT IS WORTH THE 5 MINUTES TO VERIFY on the official page (e.g. "claims $20 free
credits with no card — if true that is 400 requests at $0.05/1k; check the signup page
because the same account cut this in February"). Rank by (no card required x generous
limit x still live), not by hype.

NON-NEGOTIABLES
- Do NOT fabricate. No invented tweet URLs, tweet IDs, dates, quotes, handles, prices,
  rates, or limits. If a fact is not found, write "not found". A short honest table
  beats a long invented one.
- Quote AT MOST 25 words per post. Longer is a copyright problem and a hallucination
  risk. Paraphrase everything else and say you paraphrased.
- Do not treat a model's own claim about its limits as the vendor's; attribute the claim.
- Handle changes and rebrandings: if a rebrand hides a real change, note both names.
- Minimum depth: at least 3 rows per group per section where they exist; "not found" is
  an acceptable and expected answer for any cell.

FINISH
Return the COMPLETE table for Sections A-D, then a COVERAGE block stating: (1) the exact
list of X queries you ran and roughly how many, (2) which target accounts you could not
access, were rate-limited, shadow-banned, or had no indexable results, (3) which of the
three groups came back thinnest and therefore least trustworthy, and (4) any account you
believe exists but could not verify a handle for.
```
