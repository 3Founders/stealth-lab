"""Public-interface tests for manifest-driven structured skill ingestion."""
from __future__ import annotations

from pathlib import Path
import json
import base64
import hashlib

from app.services.ingestion_sources.manifest import load_source_manifest
from app.services.ingestion_sources.github_corpus import GitHubSkillCorpusSource
from app.services.skill_ingestion import normalize_skill_package, parse_skill_md


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_wave1_manifest_loads_the_seven_pinned_source_specs():
    manifest = load_source_manifest(REPO_ROOT / "config" / "skill_sources.yaml")

    assert [source.id for source in manifest.sources] == [
        "addy-agent-skills",
        "obra-superpowers",
        "github-awesome-copilot",
        "microsoft-skills",
        "openai-plugins",
        "openai-agents-python-skills",
        "agent-skills-spec",
    ]
    assert [source.priority for source in manifest.sources] == list(range(1, 8))
    subtree = manifest.by_id("openai-agents-python-skills")
    assert subtree.type == "github_subtree"
    assert subtree.path == ".agents/skills"
    assert all(source.repo.startswith("https://github.com/") for source in manifest.sources)


class FakeGitHub:
    commit = "a" * 40

    def __init__(self):
        self.calls: list[str] = []
        self.files = {
            "skills/debug/SKILL.md": (
                b"---\nname: debug-method\ndescription: Debug failures systematically.\n---\n"
                b"\nSee [details](references/details.md), `../shared/tool.toml`, and "
                b"[planning](../nested/plan/SKILL.md).\n"
                b"1. Reproduce the failure.\n2. Test one hypothesis.\n"
            ),
            "skills/debug/scripts/check.py": b"print('check')\n",
            "skills/debug/references/details.md": b"# Details\n",
            "skills/shared/tool.toml": b"command = 'check'\n",
            "skills/debug/assets/logo.png": b"\x89PNG\r\n\x1a\n",
            "skills/debug/node_modules/junk.js": b"junk\n",
            "skills/nested/plan/SKILL.md": b"# Plan\n\n1. Break work down.\n",
        }

    def __call__(self, url: str) -> tuple[int, bytes]:
        self.calls.append(url)
        if "/commits/" in url:
            return 200, json.dumps({"sha": self.commit}).encode()
        if url.endswith("/license"):
            return 200, json.dumps({
                "license": {"spdx_id": "Apache-2.0", "name": "Apache License 2.0"},
                "html_url": "https://github.com/acme/skills/blob/LICENSE",
                "path": "LICENSE",
            }).encode()
        if "/git/trees/" in url:
            tree = [
                {"type": "blob", "path": path, "size": len(data), "mode": "100644",
                 "sha": hashlib.sha1(path.encode()).hexdigest()}
                for path, data in self.files.items()
            ]
            tree.append({"type": "blob", "path": "skills/debug/link", "size": 5, "mode": "120000"})
            return 200, json.dumps({"sha": "b" * 40, "truncated": False, "tree": tree}).encode()
        if "/git/blobs/" in url:
            sha = url.rsplit("/", 1)[-1]
            for path, data in self.files.items():
                if hashlib.sha1(path.encode()).hexdigest() == sha:
                    return 200, json.dumps({
                        "encoding": "base64", "content": base64.b64encode(data).decode(),
                    }).encode()
        marker = f"/{self.commit}/"
        if marker in url:
            path = url.split(marker, 1)[1]
            if path in self.files:
                return 200, self.files[path]
        return 404, b""


def test_github_corpus_resolves_commit_and_packages_bounded_skill_resources():
    from app.services.ingestion_sources.manifest import CorpusSourceSpec

    source_spec = CorpusSourceSpec(
        id="acme", priority=1, type="github", repo="https://github.com/acme/skills",
        expected_format="skill_repository",
    )
    http = FakeGitHub()
    adapter = GitHubSkillCorpusSource(source_spec, http_get=http)

    refs = list(adapter.discover())
    assert [ref.path for ref in refs] == [
        "skills/debug/SKILL.md",
        "skills/nested/plan/SKILL.md",
    ]
    assert all(ref.commit == FakeGitHub.commit for ref in refs)

    artifact = adapter.fetch(refs[0])
    assert artifact.source_id == "acme"
    assert artifact.commit == FakeGitHub.commit
    assert artifact.license_metadata["spdx_id"] == "Apache-2.0"
    assert artifact.bundle_hash and len(artifact.bundle_hash) == 64
    assert [(r.path, r.kind) for r in artifact.resources] == [
        ("skills/debug/assets/logo.png", "asset"),
        ("skills/debug/references/details.md", "reference"),
        ("skills/debug/scripts/check.py", "script"),
        ("skills/shared/tool.toml", "config"),
    ]
    assert not any("node_modules" in r.path for r in artifact.resources)
    assert not any(r.path.endswith("/link") for r in artifact.resources)


def test_github_package_hash_is_stable_for_the_same_exact_revision():
    from app.services.ingestion_sources.manifest import CorpusSourceSpec

    spec = CorpusSourceSpec(
        id="acme", priority=1, type="github", repo="https://github.com/acme/skills",
        expected_format="skill_repository",
    )
    adapter = GitHubSkillCorpusSource(spec, http_get=FakeGitHub())
    ref = next(adapter.discover())
    assert adapter.fetch(ref).bundle_hash == adapter.fetch(ref).bundle_hash


def test_frontmatter_preserves_unknown_fields_and_normalizes_tool_spellings():
    parsed = parse_skill_md("""---
name: careful-debugging
description: Debug a failure by testing one hypothesis at a time.
license: Apache-2.0
compatibility: Requires Python 3.11.
allowed-tools:
  - rg
  - pytest
metadata:
  maturity: experimental
x-upstream-field:
  nested: true
---

1. Preserve the failing evidence.
2. Reproduce before changing code.
""")

    assert parsed.license == "Apache-2.0"
    assert parsed.compatibility == "Requires Python 3.11."
    assert parsed.allowed_tools == ["rg", "pytest"]
    assert parsed.frontmatter["x-upstream-field"] == {"nested": True}
    assert parsed.metadata == {"maturity": "experimental"}
    assert "Preserve the failing evidence" in parsed.instructions


def test_step_headings_win_over_checklists_and_examples():
    parsed = parse_skill_md("""---
name: review
description: Review a change systematically.
---
## Review Process
### Step 1: Understand the context
- Read the ticket
- Read every changed file
### Step 2: Review the tests
- [ ] Tests pass
- [ ] Build passes
## Red Flags
- Never rubber-stamp a change
""")
    assert parsed.steps == ["Understand the context", "Review the tests"]


def test_normalized_package_keeps_dependencies_resources_and_requirements():
    from app.services.ingestion_sources.manifest import CorpusSourceSpec

    adapter = GitHubSkillCorpusSource(CorpusSourceSpec(
        id="acme", priority=1, type="github", repo="https://github.com/acme/skills",
        expected_format="skill_repository",
    ), http_get=FakeGitHub())
    artifact = adapter.fetch(next(adapter.discover()))
    package = normalize_skill_package(artifact)

    assert package.source["commit"] == FakeGitHub.commit
    assert package.skill_path == "skills/debug/SKILL.md"
    assert package.resources[0].sha256
    assert package.dependencies[0].reference == "../nested/plan/SKILL.md"
    assert package.dependencies[0].resolution == "resolved"
    assert package.dependencies[0].target_skill_path == "skills/nested/plan/SKILL.md"
    assert package.instructions.startswith("See [details]")
