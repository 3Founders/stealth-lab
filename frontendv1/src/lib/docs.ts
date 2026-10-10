/**
 * Guide documentation — authored explainers of real, existing product
 * mechanics (scope badges, verification bands, the contribute flow). Not
 * backend data: this is static prose, same category as the About page copy.
 * Legal/policy documents remain their own thing, served from /legal — see
 * lib/legal-docs.ts — and are only cross-linked from here.
 */
export interface GuideDoc {
  slug: string;
  title: string;
  summary: string;
  body: string[]; // paragraphs; a leading "## " marks a subheading
}

export const GUIDE_DOCS: GuideDoc[] = [
  {
    slug: "how-it-works",
    title: "How keळ works",
    summary: "The product loop, end to end: Problem → Procedure → Evidence → Verification → Contribution.",
    body: [
      "keळ exists to answer one question well: what's the best way to do something? Everything in the product — search, problems, procedures, rankings — exists in service of that question.",
      "## Problem",
      "A Problem names something worth solving well — not a search keyword, a real recurring situation. Problems can carry a benchmark: a frozen, repeatable way to evaluate candidate solutions against each other fairly.",
      "## Procedure",
      "A Procedure is a concrete, checkable claim: do these steps, under these conditions, and this is what happens. Procedures start private to their author, are versioned, and only become part of the public commons when explicitly published.",
      "## Evidence",
      "Every time a procedure actually runs, the outcome — success, failure, cost, latency — is recorded as evidence, whether the run went well or badly. Evidence is never deleted to make a procedure look better.",
      "## Verification",
      "A procedure's rank comes from a Wilson lower bound on its verified success rate — a statistically conservative estimate that penalizes small sample sizes, not a raw average and not the author's confidence. Problem pages group solutions into three states: Best verified (enough independent evidence to lead), Promising (some evidence, not yet enough to lead), and Insufficient evidence (too few runs to say anything yet).",
      "## Contribution",
      "The current best-known way is always visible, and never overwrites history — when a better procedure emerges, the previous one stays as a comparison point, not a deletion.",
    ],
  },
  {
    slug: "scope-and-visibility",
    title: "Scope: private, organization, and the global commons",
    summary: "What PRIVATE, ORGANIZATION, GLOBAL CANDIDATE, and GLOBAL VERIFIED actually mean.",
    body: [
      "Every procedure and claim in keळ carries exactly one scope label, and it always reflects the backend's real visibility and verification state — never a similarity score standing in for authorization.",
      "## Private",
      "Only you can see it. Nothing you add is public until you take a separate, explicit action to publish it.",
      "## Organization",
      "Visible to authorized members of your organization only.",
      "## Global candidate",
      "Published to the public commons, but not yet independently verified. Its own author's execution history stays private — a candidate starts with zero independent verification and earns it from other people's runs, not its author's.",
      "## Global verified",
      "In the public commons, with independent execution evidence behind it.",
      "Publishing to the commons runs a gate that checks provenance, license, and privacy/secret exposure before anything becomes public — publication is never automatic.",
    ],
  },
  {
    slug: "contributing",
    title: "Adding a procedure",
    summary: "Quick add (private, instant) vs. proposing to the commons (reviewed).",
    body: [
      "There are two ways to add a procedure, and they have very different consequences.",
      "## Quick add — private",
      "Add a procedure to your own private library, either by filling in name / goal / steps / applicability, or by pasting an existing SKILL.md-style document. It saves instantly, is indexed for your own search, and stays private — nothing becomes public until you separately choose to publish it.",
      "## Propose to the commons — reviewed",
      "Describe a problem or method in plain language and the backend drafts a structured proposal from it. The proposal is quarantined — it never enters the shared commons automatically — until a human approves it through the backend's review flow.",
      "In both cases, keळ never fabricates the outcome: a quick-add procedure is a candidate with no evidence until it's actually run, and a proposal's feasibility assessment comes from the backend's own decomposition check, not a guess.",
    ],
  },
  {
    slug: "agents-and-mcp",
    title: "Using keळ from an agent",
    summary: "keळ exposes an MCP tool surface so coding agents can look up and report on procedures directly.",
    body: [
      "keळ isn't only a website — it's also reachable as an MCP (Model Context Protocol) server, so an agent working in your editor or terminal can look up the best known way to do something, check whether a procedure applies to its current situation, and report execution outcomes back as evidence, without a human copying results back and forth manually.",
      "This site also exposes a WebMCP tool surface directly in the browser tab for supporting agentic browsers — see the live status of what's registered on the Setup page.",
      "The MCP connection is authenticated and rate/cost-governed the same way the rest of the backend is; it does not bypass scope or publication rules.",
    ],
  },
];

export function getGuideDoc(slug: string): GuideDoc | undefined {
  return GUIDE_DOCS.find((d) => d.slug === slug);
}
