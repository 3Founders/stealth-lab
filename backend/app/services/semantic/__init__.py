"""
Shared, provider-neutral semantic-judge layer (JEV -> Gemini -> Gemma).

Deliberately import-light: applicability_judge.py imports `semantic.errors`
at module load, so nothing here may import applicability_judge at import time.

    errors.py       ErrorKind / ProviderError / SemanticJudgmentUnavailable
    policy.py       RetryPolicy (+ backoff/jitter) and SemanticMetrics
    prompts.py      retention / summary prompts + strict parsers
    providers.py    JEVProvider, OpenAICompatProvider (Gemini, Gemma), factory
    chain.py        SemanticJudge -- ordered fallback, retries, metrics
    applicability.py  ChainedApplicabilityJudge (ApplicabilityJudge protocol)
    jobs.py         requeue of pending judgments on the ingestion_jobs queue
"""
