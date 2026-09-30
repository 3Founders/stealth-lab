"""
Proving tests for Step 6's repository scoping of `BotDependencyPrSource`, and for
the step-6 pilot's per-repository license resolver.

GitHub's search endpoint caps every query at 1,000 results and a global
`author:app/dependabot is:merged` query has ~26.6M matches, so the global form
can never enumerate a corpus; `repos=` scopes one query per repository.

The companion change -- `workflow_knowledge.gate_license` admitting a CI
workflow item on a resolved *repository* license -- is not in the tree yet (it
conflicted with upstream and is parked in
.scratch/pull_conflict_backup_2026-09-29/); its tests land with it.

Offline: no sockets, no database. The GitHub client is a hand-rolled fake that
replays scripted payloads and records the URLs it was asked for.
"""
from __future__ import annotations

import base64
import os

import pytest

os.environ.pop("DATABASE_URL", None)

from app.services.ingestion_sources import bot_dependency_prs as botprs  # noqa: E402

MIT_BLOB = base64.b64encode(
    b"MIT License\n\nPermission is hereby granted, free of charge, to any person "
    b"obtaining a copy of this software and associated documentation files (the "
    b'"Software"), to deal in the Software without restriction.\n'
).decode("ascii")


# --------------------------------------------------------------------------- #
# repository scoping
# --------------------------------------------------------------------------- #


def test_search_url_is_repo_scoped_when_repos_given():
    source = botprs.BotDependencyPrSource(repos=("acme/widgets", "acme/gadgets"))
    url = source._search_url("app/dependabot", 1, repo="acme/widgets")
    assert "repo%3Aacme%2Fwidgets" in url or "repo:acme/widgets" in url
    assert "author%3Aapp%2Fdependabot" in url or "author:app/dependabot" in url


def test_search_url_without_repo_is_the_legacy_global_form():
    source = botprs.BotDependencyPrSource()
    assert "repo%3A" not in source._search_url("app/dependabot", 1)


class ScriptedClient:
    """A `_GitHubApiClient` stand-in that replays per-URL payloads."""

    def __init__(self, pages: dict[str, dict]):
        self.pages = pages
        self.urls: list[str] = []
        self.stats = {"requests": 0, "retries": 0, "rate_limited": 0}

    def get(self, url: str, *, is_search: bool = False):
        self.urls.append(url)
        self.stats["requests"] += 1
        for fragment, payload in self.pages.items():
            if fragment in url:
                return payload
        return {"items": []}


def _search_item(repo: str, number: int, ident: int) -> dict:
    return {
        "id": ident,
        "number": number,
        "title": f"Bump lodash from 4.17.20 to 4.17.21 ({repo})",
        "repository_url": f"https://api.github.com/repos/{repo}",
        "user": {"login": "dependabot[bot]"},
        "created_at": "2026-01-02T03:04:05Z",
        "pull_request": {"merged_at": "2026-01-03T00:00:00Z"},
        "html_url": f"https://github.com/{repo}/pull/{number}",
    }


def test_discover_queries_each_repository_separately():
    client = ScriptedClient({
        "acme%2Fwidgets": {"items": [_search_item("acme/widgets", 1, 11)]},
        "acme%2Fgadgets": {"items": [_search_item("acme/gadgets", 2, 22)]},
    })
    source = botprs.BotDependencyPrSource(
        client=client, repos=("acme/widgets", "acme/gadgets"), per_page=100, max_pages=1
    )
    found = {(c.repo, c.pr_number) for c in source.discover()}
    assert found == {("acme/widgets", 1), ("acme/gadgets", 2)}
    assert client.stats["requests"] == 2


def test_discover_defaults_to_one_global_query_when_unscoped():
    client = ScriptedClient({"search/issues": {"items": [_search_item("acme/widgets", 1, 11)]}})
    source = botprs.BotDependencyPrSource(client=client, per_page=100, max_pages=1)
    assert len(list(source.discover())) == 1
    assert client.stats["requests"] == 1
    assert "repo%3A" not in client.urls[0]


def test_discover_still_honours_the_resume_cursor():
    client = ScriptedClient({
        "search/issues": {
            "items": [_search_item("acme/widgets", 1, 11), _search_item("acme/widgets", 2, 22)]
        }
    })
    source = botprs.BotDependencyPrSource(client=client, per_page=100, max_pages=1)
    cursors = list(source.discover())
    assert len(cursors) == 2
    after_first = cursors[0].cursor
    assert all(c.cursor > after_first for c in source.discover(after=after_first))
    assert [c.cursor for c in source.discover(after=after_first)] == [cursors[1].cursor]


# --------------------------------------------------------------------------- #
# license resolver: the blob is the authority, not the API field
# --------------------------------------------------------------------------- #


def test_license_resolver_prefers_the_license_blob_over_the_api_field():
    """The project's 2026-09-28 correction: the API field is a hint, the LICENSE
    file is the license. `actions/starter-workflows` is the precedent -- API says
    NOASSERTION, the shipped file is MIT."""
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "step6_pilot", Path(__file__).resolve().parents[1] / "scripts" / "step6_pilot.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class FakeClient:
        stats = {"requests": 0}

        def get(self, url, *, is_search=False):
            return {
                "spdx_id": "NOASSERTION",
                "name": "other",
                "content": MIT_BLOB,
                "encoding": "base64",
            }

    resolve = module.build_license_resolver(FakeClient())
    spdx, note = resolve("acme/starter-workflows")
    assert spdx == "MIT"
    assert "blob" in note
    # cached: a second call costs no API request
    assert resolve("acme/starter-workflows")[0] == "MIT"


def test_license_resolver_falls_back_to_the_api_field():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "step6_pilot2", Path(__file__).resolve().parents[1] / "scripts" / "step6_pilot.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class FakeClient:
        stats = {"requests": 0}

        def get(self, url, *, is_search=False):
            return {"license": {"spdx_id": "Apache-2.0"}}

    resolve = module.build_license_resolver(FakeClient())
    assert resolve("acme/widgets")[0] == "Apache-2.0"


def test_license_resolver_treats_an_api_error_as_unresolved_not_permissive():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "step6_pilot3", Path(__file__).resolve().parents[1] / "scripts" / "step6_pilot.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Boom:
        stats = {"requests": 0}

        def get(self, url, *, is_search=False):
            raise RuntimeError("502")

    resolve = module.build_license_resolver(Boom())
    spdx, note = resolve("acme/widgets")
    assert spdx is None
    assert "api error" in note
