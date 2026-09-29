"""SkillMD-138K (FayeZC/SkillMD-138K) -> candidate Procedures.

Plan (docs/ingestion_sources_plan.md, section B and "Order" 6; build prompt step 3):
  * a SKILL.md states intent, not a verified outcome: it enters as a CANDIDATE and earns trust only from runs;
  * the license is the file's own repository license -- decided per file, at the exact commit that holds the
    exact text (the dataset's CC-BY-4.0 covers its compilation, not the files);
  * exact duplicates by content hash, near duplicates by MinHash, both across runs and sources;
  * untrusted text is screened for prompt injection before a model sees it, and flagged content is recorded,
    never silently dropped (the core compiler does both);
  * credit travels with the content: the file's repository, path, commit and license, and the dataset.
"""
