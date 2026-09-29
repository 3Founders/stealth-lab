"""
`nodejs/userland-migrations` as a `SourceAdapter` -- one Procedure per
recipe directory, each carrying a fixture-based check.

WHY A PINNED LOCAL CHECKOUT AND NOT A NETWORK CALL
    The catalog is a Git repo, not an npm registry. Every one of the 16
    `@nodejs/*` package names queried against npm returned 404 while a
    control package resolved, so there is no published artifact whose
    license could differ from the repository's and nothing to fetch at
    ingest time. The repo tree at a pinned commit is the whole source of
    truth, it is reproducible, and it makes the license decision
    re-derivable rather than remembered.

    Ingestion is therefore a directory read plus a subprocess. There is
    no HTTP client in this module at all, which is also why this module
    imports nothing from the screening path.

THE LICENSE GATE IS THE POINT OF THIS FILE
    One root MIT `LICENSE` governs all 40 recipes, and every recipe
    self-declares `license: MIT` in both `codemod.yaml` and
    `package.json`. The declaration is metadata somebody typed; the
    detected SPDX id comes from `identify_spdx_from_text` over the bytes
    of the governing blob, resolved by `decide_repo_license` through the
    shared policy in `repo_license_policy`. If that policy returns
    anything other than `ALLOW`, `fetch()` raises and emits nothing.

    That refusal is deliberately loud. The whole wider codemod registry
    is community-contributed and self-asserted, and Moderne-licensed
    OpenRewrite repos show exactly how a 43-byte pointer file defeats a
    naive root-license check -- a gate that quietly passes what it
    cannot identify is the failure this code exists to prevent.

HONEST SCOPE LIMITS
    - Gate A is the transform's own fixture suite. It establishes
      self-consistency, not correctness; see `codemod_checks` for why
      that distinction is carried in every payload.
    - Gate B parses the committed `expected` files. `.ts` and `.tsx`
      have no offline parser here and are reported as `no_parser` rather
      than passed.
    - The two version fields genuinely disagree in the catalog
      (`codemod.yaml` says 0.0.1, `package.json` says 1.0.1 for
      `v22-to-v24`). Both are carried, and their disagreement is an
      explicit provenance field rather than a choice made silently here.
"""
from __future__ import annotations

import json
import hashlib
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from app.services.ingestion_sources.base import (
    SourceArtifact,
    SourceRef,
    SourceResource,
    compute_content_hash,
)
from app.services.ingestion_sources.codemod_checks import (
    DEFAULT_CHECK_TIMEOUT_S,
    RUNNER_VERSION,
    CheckOutcome,
    build_check_payload,
    check_expected_wellformedness,
    discover_fixture_cases,
    run_jssg_fixture_check,
)
from app.services.repo_license_policy import (
    LicenseVerdict,
    decide_repo_license,
    identify_spdx_from_text,
    license_paths,
)

# The commit the license facts and the recipe count in this module's
# docstring were read at. Required of the constructor, never defaulted:
# an adapter that silently reads whatever HEAD happens to be cannot
# reproduce its own license verdict, which is the one thing here that
# has to be re-derivable.
DEFAULT_PINNED_COMMIT = "48b9b9a1d1385e7f1d2de8a8482d557447f512c0"

DEFAULT_REPO_URL = "https://github.com/nodejs/userland-migrations"

# Directory names under `recipes/`; the manifest that makes a directory
# a recipe is the presence of this file, nothing else.
RECIPES_DIRNAME = "recipes"
MANIFEST_FILENAME = "codemod.yaml"
TESTS_DIRNAME = "tests"
TRANSFORMS_DIRNAME = "src"

# Fixture bundling bounds. The catalog's largest recipe fixture set is
# small, but nothing here assumes that: past these bounds a file is
# recorded by path, size and sha256 with its bytes omitted, so a
# consumer can still tell exactly which file it was.
MAX_RESOURCE_FILES = 40
MAX_RESOURCE_FILE_BYTES = 32 * 1024


class CodemodLicenseBlocked(RuntimeError):
    """A recipe's governing license verdict was not ALLOW.

    Raised rather than returned so a caller cannot accidentally ingest a
    recipe by ignoring a status code. The verdict is on the exception
    because the reason is the whole point of refusing.
    """

    def __init__(self, message: str, *, verdict: LicenseVerdict) -> None:
        super().__init__(message)
        self.verdict = verdict


@dataclass(frozen=True)
class CheckoutLicensing:
    """The license picture of a checkout, resolved once per adapter.

    `tree` is the GitHub-tree shape `repo_license_policy` speaks
    (`{"type": "blob", "path": ...}` per entry), built from disk. Keeping
    it in one object means the tree passed to `decide_repo_license` and
    the index passed to it as `license_spdx_by_path` can never drift
    apart -- a drift there would let a governing blob be missing from
    the index, which is the one miss that turns into a confident verdict
    about the wrong license.
    """

    tree: list[dict[str, Any]]
    license_spdx_by_path: dict[str, str]
    license_blob_paths: tuple[str, ...]

    def verdict_for(self, path: str, *, allow: Sequence[str] | None = None) -> LicenseVerdict:
        return decide_repo_license(
            path=path,
            tree=self.tree,
            license_spdx_by_path=self.license_spdx_by_path,
            allow=allow,
        )


_SKIP_DIRNAMES = frozenset({".git", "node_modules", ".pnpm-store", "__pycache__"})


def checkout_tree(checkout: str | Path) -> list[dict[str, Any]]:
    """Every tracked-looking file under `checkout`, GitHub-tree shaped.

    `node_modules` and `.git` are skipped: this adapter reads a source
    checkout, not an install, and a 100k-entry install tree would make
    the license tree both slow and misleading (a dependency's LICENSE
    must never be mistaken for the repo's).
    """
    root = Path(checkout)
    if not root.is_dir():
        raise FileNotFoundError(f"checkout does not exist: {root}")
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in _SKIP_DIRNAMES for part in relative.parts):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        entries.append({"type": "blob", "path": relative.as_posix(), "size": size})
    return entries


def resolve_checkout_licensing(
    checkout: str | Path,
    *,
    tree: Sequence[Mapping[str, Any]] | None = None,
) -> CheckoutLicensing:
    """Read every license blob in the checkout and identify its SPDX id.

    An unreadable or unidentifiable blob is indexed as the empty string,
    not omitted. `decide_repo_license` treats a missing index entry as
    "no id", so both spellings quarantine -- but keeping the key means
    `license_paths` and the index stay the same set, which is the
    property that makes a miss auditable.
    """
    root = Path(checkout)
    entries = list(tree) if tree is not None else checkout_tree(root)
    blobs = license_paths(entries)
    by_path: dict[str, str] = {}
    for blob in blobs:
        try:
            text = (root / blob).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        by_path[blob] = identify_spdx_from_text(text) or ""
    return CheckoutLicensing(
        tree=[dict(entry) for entry in entries],
        license_spdx_by_path=by_path,
        license_blob_paths=tuple(blobs),
    )


def _load_yaml(content: str) -> Any:
    import yaml  # lazy import, same discipline app/services/ingestion_sources/repo_procedural.py uses

    return yaml.safe_load(content)


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _read_json(path: Path) -> Any:
    raw = _read_text(path)
    if raw is None:
        return None
    import json

    try:
        return json.loads(raw)
    except ValueError:
        return None


def _as_str(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, (int, float)):
        return str(value)
    return None


def _str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    if isinstance(value, str):
        return [value]
    return []


def _capability_args(capabilities: Sequence[str]) -> tuple[str, ...]:
    """Sandbox escalations, derived from what the manifest DECLARES.

    jssg runs transforms in a sandbox with `fs` sandboxed by default and
    `--allow-fs` / `--allow-child-process` as the escalation. Passing an
    escalation the recipe did not declare would be us widening its own
    capability grant, so nothing is added here that is not in
    `capabilities`.
    """
    declared = {c.strip().lower() for c in capabilities}
    args: list[str] = []
    if "fs" in declared:
        args.append("--allow-fs")
    if "child_process" in declared:
        args.append("--allow-child-process")
    if declared:
        args.extend(["--strictness", "cst"])
    return tuple(args)


@dataclass(frozen=True)
class _DeclaredCommand:
    """One `codemod jssg test` invocation, as the recipe writes it."""

    transform: str
    test_dir: str
    filter: str | None
    language: str | None
    extra: tuple[str, ...] = ()


def _transform_key(transform: str) -> str:
    return Path(transform).name


_VALUED_ARGS = frozenset(
    {"--strictness", "--filter", "--max-threads", "--timeout", "--context-lines", "--expect-errors"}
)


def _merge_args(*groups: Sequence[str]) -> tuple[str, ...]:
    """Union argument lists, keeping first occurrence order.

    The manifest's `capabilities` and the recipe's own test script both
    name `--allow-fs` / `--allow-child-process` / `--strictness cst`, and
    the two sources are not required to agree or to stay in sync. Passing
    the same flag twice makes clap exit 2 before any test runs, which the
    reader reports as "the tool produced no JSON" -- a misconfiguration
    indistinguishable from a broken transform. Deduping here keeps the
    union, and the FIRST value wins so the manifest remains authoritative
    for an escalation and the recipe's own value wins for a strictness
    the manifest never states.
    """
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        index = 0
        tokens = list(group)
        while index < len(tokens):
            token = tokens[index]
            if token.startswith("--"):
                arity = 2 if token in _VALUED_ARGS else 1
                pair = tuple(tokens[index : index + arity])
                if pair[0] not in seen:
                    seen.add(pair[0])
                    merged.extend(pair)
                index += arity
                continue
            merged.append(token)
            index += 1
    return tuple(merged)


def _declared_test_commands(package: Mapping[str, Any]) -> dict[str, _DeclaredCommand]:
    """Parse the recipe's OWN `codemod jssg test` invocations out of
    `package.json`.

    The recipe's test script is the authority on how its fixtures are meant
    to be run, and in a multi-transform recipe it is the only place the
    per-transform `--filter` exists. Guessing that scoping is what made a
    green catalog read as 3/15.

    Parsed with `shlex` and matched on argv POSITION rather than on
    substring, because a transform path can contain a dash and a filter can
    look like a path; positional parsing is what the tool itself does.
    Anything unparseable is skipped rather than guessed, and a transform
    with no entry here falls back to the unfiltered directory call.
    """
    scripts = package.get("scripts")
    if not isinstance(scripts, Mapping):
        return {}
    found: dict[str, _DeclaredCommand] = {}
    for raw in scripts.values():
        if not isinstance(raw, str) or "jssg test" not in raw:
            continue
        for segment in _shell_segments(raw):
            try:
                argv = shlex.split(segment, posix=True)
            except ValueError:
                continue
            if "jssg" not in argv or "test" not in argv:
                continue
            try:
                tail = argv[argv.index("test") + 1:]
            except ValueError:
                continue
            language = None
            if "-l" in tail:
                idx = tail.index("-l")
                if idx + 1 < len(tail):
                    language = tail[idx + 1]
            positional = [
                a
                for i, a in enumerate(tail)
                if not a.startswith("-")
                and (i == 0 or tail[i - 1] not in ("-l", "--language", "--filter", "--reporter", "--strictness", "--max-threads", "--timeout", "--context-lines"))
            ]
            if not positional:
                continue
            transform = positional[0].lstrip("./")
            test_dir = positional[1] if len(positional) > 1 else TESTS_DIRNAME
            test_filter = None
            for flag in ("--filter",):
                if flag in tail:
                    idx = tail.index(flag)
                    if idx + 1 < len(tail):
                        test_filter = tail[idx + 1]
            # Flags the recipe itself passed, minus the ones this runner
            # supplies itself (`--reporter`) and minus the two already
            # parsed above. Kept because `--strictness` changes what
            # "equal output" means and `--allow-*` is the sandbox
            # escalation a `package.json` transform genuinely needs; the
            # manifest's `capabilities` list cannot express strictness.
            # Flags the recipe itself passed, minus the ones this runner
            # supplies itself (`--reporter`) and minus the two already
            # parsed above. Kept because `--strictness` changes what
            # "equal output" means and `--allow-*` is the sandbox
            # escalation a `package.json` transform genuinely needs; the
            # manifest's `capabilities` list cannot express strictness.
            # Valued flags carry their value too: `--strictness cst` with
            # the value dropped parses as a missing argument and the tool
            # exits before running anything.
            _CONSUMED = {"--filter", "--reporter", "--language", "-l"}
            _VALUED = {
                "--strictness",
                "--max-threads",
                "--timeout",
                "--context-lines",
                "--expect-errors",
            }
            extra: list[str] = []
            index = 0
            while index < len(tail):
                token = tail[index]
                if token in _CONSUMED:
                    # every consumed flag here takes exactly one value
                    index += 2
                    continue
                if token.startswith("--"):
                    extra.append(token)
                    if token in _VALUED and index + 1 < len(tail):
                        extra.append(tail[index + 1])
                        index += 1
                index += 1
            found[_transform_key(transform)] = _DeclaredCommand(
                transform=transform,
                test_dir=test_dir.rstrip("/") or TESTS_DIRNAME,
                filter=test_filter,
                language=language,
                extra=tuple(extra),
            )
    return found


def _shell_segments(command: str) -> list[str]:
    """Split `a && b` / `a; b` into individual invocations.

    A recipe composes its suite from `node --run x && node --run y`, and
    each of those expands to a `jssg test` call, so the outer command holds
    several invocations and only the `npx codemod ...` halves are ours.
    """
    parts = re.split(r"&&|\|\||;", command)
    return [p for p in (segment.strip() for segment in parts) if "jssg test" in p]


def _bundle_fixtures(tests_dir: Path | None, inventory_paths: Sequence[str]) -> tuple[SourceResource, ...]:
    """Fixture files as bounded `SourceResource`s.

    Bytes above `MAX_RESOURCE_FILE_BYTES`, or files past
    `MAX_RESOURCE_FILES`, are recorded with their sha256 and size and
    their content omitted. Never inline megabytes into a document that
    ends up in an embedding.
    """
    if tests_dir is None:
        return ()
    resources: list[SourceResource] = []
    for rel in list(inventory_paths)[:MAX_RESOURCE_FILES]:
        path = tests_dir / rel
        try:
            data = path.read_bytes()
        except OSError:
            continue
        digest = hashlib.sha256(data).hexdigest()
        segments = rel.split("/")
        is_input = "input" in segments or (len(segments) > 1 and segments[-1].startswith("input."))
        side = "input" if is_input else "expected"
        if len(data) <= MAX_RESOURCE_FILE_BYTES:
            resources.append(
                SourceResource(
                    path=rel,
                    kind=f"fixture-{side}",
                    sha256=digest,
                    size=len(data),
                    content=data,
                )
            )
        else:
            resources.append(
                SourceResource(
                    path=rel,
                    kind=f"fixture-{side}-content-omitted",
                    sha256=digest,
                    size=len(data),
                )
            )
    return tuple(resources)


def _composed_document(
    *,
    recipe_name: str,
    manifest: Mapping[str, Any],
    package: Mapping[str, Any],
    transforms: Sequence[str],
    test_command: str,
    inventory: Any,
    check: Mapping[str, Any],
) -> str:
    """A plain markdown retrieval document.

    Composed here rather than taken from the shared builder on purpose:
    the shared recipe normalises for semantic search, and this document
    has to stay a faithful, auditable restatement of one upstream
    directory. Delegating would buy consistency at the cost of the
    version/attribution fields that make the provenance checkable.
    """
    description = _as_str(manifest.get("description")) or "No description declared."
    capabilities = _str_list(manifest.get("capabilities"))
    targets = manifest.get("targets")
    languages = _str_list(targets.get("languages")) if isinstance(targets, Mapping) else []
    keywords = _str_list(manifest.get("keywords"))

    lines = [
        "---",
        # JSON strings are valid YAML double-quoted scalars. Unquoted, `name: @nodejs/x` is invalid YAML ('@' is a
        # reserved indicator) and every generated document failed to parse (39 of 39 on the first --apply);
        # a description with ': ' or a newline breaks a plain scalar the same way.
        f"name: {json.dumps(recipe_name)}",
        # The description becomes the procedure's goal, and the goal gate refuses literal code syntax ("...
        # `url.parse` to `new URL()`" was rejected), so backticks become quotes here. The body keeps the original text.
        f"description: {json.dumps(' '.join(description.replace(chr(96), chr(39)).split()))}",
        "---",
        "",
        f"# {recipe_name}",
        "",
        "A source-to-source migration codemod published by the Node.js project as "
        "`nodejs/userland-migrations`. It is a deterministic transform, not a model "
        "call, and it ships its own before/after fixtures.",
        "",
        "## What it changes",
        "",
        description,
        "",
        "## When to apply",
        "",
        f"- Target language: {', '.join(languages) or 'not declared in codemod.yaml'}.",
        f"- Declared capabilities: {', '.join(capabilities) or 'none'}.",
        f"- Transform entrypoints: {', '.join(transforms) or 'none found under src/'}.",
        f"- Keywords: {', '.join(keywords) or 'none declared'}.",
        "",
        # The parser takes steps ONLY from a Steps/Procedure/Workflow section when there is one (numbered items under
        # "When to apply" are deliberately not steps), so the runnable procedure lives here.
        "## Steps",
        "",
        f"1. Confirm the project targets {', '.join(languages) or 'the language this codemod declares'} and uses the "
        "API described above.",
        f"2. Run the codemod: `npx codemod {recipe_name}`. Offline, against a local checkout, without the hosted "
        f"registry: `codemod workflow run -w ./{RECIPES_DIRNAME}/{recipe_name.split('/', 1)[-1]}/workflow.yaml`.",
        "3. Review the diff. The transform is a deterministic source-to-source rewrite, not a model call.",
        f"4. Re-run the project's own tests: `{test_command or 'not declared'}`.",
        "",
        "## Check (self-consistency, not correctness)",
        "",
        f"1. Gate A, fixture self-consistency: {check.get('case_count', 0)} case(s) reported, "
        f"verdict `{check.get('gates', {}).get('jssg_fixture_suite', 'not_run')}`.",
        f"2. Fixture layout `{inventory.layout}` with "
        f"{inventory.negative_case_count} negative (no-op) case(s) out of "
        f"{inventory.case_count}.",
        f"3. Claim: {check.get('claim', 'no claim recorded')}.",
        "",
        "A passing fixture suite proves the transform reproduces its own committed "
        "output. It does not prove the output is correct: the fixture and the transform "
        "share an author, and this catalog contains a recipe whose committed output "
        "throws at runtime while its fixture test passes.",
    ]
    return "\n".join(lines) + "\n"


class NodeUserlandMigrationsSource:
    """Every `recipes/<name>/` directory holding a `codemod.yaml`.

    `commit` is required, not defaulted, and is stamped onto every
    artifact: the license verdict recorded here is a fact about one
    commit, and a provenance record that does not name the commit is
    not a provenance record.
    """

    source_type = "codemod_node"

    def __init__(
        self,
        checkout: str | Path,
        *,
        commit: str,
        repo_url: str = DEFAULT_REPO_URL,
        node_bin: str = "codemod",
        node_exec: str = "node",
        check_timeout_s: int = DEFAULT_CHECK_TIMEOUT_S,
        run_checks: bool = True,
        license_allow: Sequence[str] | None = None,
    ) -> None:
        self._root = Path(checkout)
        if not commit:
            raise ValueError("commit is required: a codemod artifact without one cannot be re-derived")
        self._commit = commit
        self._repo_url = repo_url
        self._node_bin = node_bin
        self._node_exec = node_exec
        self._check_timeout_s = check_timeout_s
        self._run_checks = run_checks
        self._license_allow = license_allow
        self._licensing: CheckoutLicensing | None = None

    @property
    def commit(self) -> str:
        return self._commit

    @property
    def slug(self) -> str:
        """`owner/repo`, matching `skill_md.GitHubSkillSource.slug` so the
        compiler cannot tell the two adapters apart by repository field."""
        segments = [segment for segment in self._repo_url.rstrip("/").split("/") if segment]
        return "/".join(segments[-2:]) if len(segments) >= 2 else self._repo_url

    def licensing(self) -> CheckoutLicensing:
        if self._licensing is None:
            self._licensing = resolve_checkout_licensing(self._root)
        return self._licensing

    def license_verdict(self, recipe_relpath: str) -> LicenseVerdict:
        """The verdict governing one recipe directory, at the pinned commit."""
        return self.licensing().verdict_for(recipe_relpath, allow=self._license_allow)

    def _recipe_dirs(self) -> list[Path]:
        recipes = self._root / RECIPES_DIRNAME
        if not recipes.is_dir():
            return []
        return sorted(
            entry
            for entry in recipes.iterdir()
            if entry.is_dir() and (entry / MANIFEST_FILENAME).is_file()
        )

    def discover(self) -> Iterator[SourceRef]:
        for directory in self._recipe_dirs():
            rel = directory.relative_to(self._root).as_posix()
            yield SourceRef(
                uri=f"{self._repo_url}/tree/{self._commit}/{rel}",
                repository=self.slug,
                path=rel,
                commit=self._commit,
                source_id=directory.name,
            )

    def fetch(self, ref: SourceRef) -> SourceArtifact:
        verdict = self.license_verdict(ref.path or "")
        if verdict.decision != "ALLOW":
            raise CodemodLicenseBlocked(
                f"refusing to emit {ref.path}: license verdict is "
                f"{verdict.decision} ({verdict.reason})",
                verdict=verdict,
            )

        directory = self._root / (ref.path or "")
        manifest = _load_yaml(_read_text(directory / MANIFEST_FILENAME) or "") or {}
        if not isinstance(manifest, Mapping):
            manifest = {}
        package = _read_json(directory / "package.json")
        if not isinstance(package, Mapping):
            package = {}
        workflow = _load_yaml(_read_text(directory / "workflow.yaml") or "") or {}
        if not isinstance(workflow, Mapping):
            workflow = {}

        recipe_name = _as_str(manifest.get("name")) or f"@nodejs/{directory.name}"
        src = directory / TRANSFORMS_DIRNAME
        transforms = (
            sorted(p.relative_to(directory).as_posix() for p in src.rglob("*.ts") if p.is_file())
            if src.is_dir()
            else []
        )
        tests_dir = directory / TESTS_DIRNAME
        inventory = discover_fixture_cases(tests_dir=tests_dir if tests_dir.is_dir() else None)

        outcome = self._run_gates(
            directory=directory,
            transforms=transforms,
            manifest=manifest,
            package=package,
            tests_dir=tests_dir if tests_dir.is_dir() else None,
            inventory=inventory,
        )
        check = build_check_payload(
            outcome=outcome,
            inventory=inventory,
            recipe_id=recipe_name,
            runner_version=RUNNER_VERSION,
        )
        content = _composed_document(
            recipe_name=recipe_name,
            manifest=manifest,
            package=package,
            transforms=transforms,
            test_command=_as_str(package.get("scripts", {}).get("test")) if isinstance(package.get("scripts"), Mapping) else None,
            inventory=inventory,
            check=check,
        )

        version_yaml = _as_str(manifest.get("version"))
        version_pkg = _as_str(package.get("version"))
        license_metadata: dict[str, Any] = {
            "repo_url": self._repo_url,
            "commit": self._commit,
            "path": ref.path,
            "recipe_name": recipe_name,
            "version_codemod_yaml": version_yaml,
            "version_package_json": version_pkg,
            "versions_disagree": bool(
                version_yaml and version_pkg and version_yaml != version_pkg
            ),
            "declared_license": _as_str(manifest.get("license")) or _as_str(package.get("license")),
            "detected_spdx": verdict.spdx_id,
            "license_source_path": verdict.source_path,
            "license_blob_paths": list(self.licensing().license_blob_paths),
            "allowlist_version": verdict.allowlist_version,
            "license_verdict": verdict.as_dict(),
            "author": _as_str(manifest.get("author")),
            "schema_version": _as_str(manifest.get("schema_version")),
            "category": _as_str(manifest.get("category")),
            "capabilities": _str_list(manifest.get("capabilities")),
            "node_engine": _as_str(package.get("engines", {}).get("node"))
            if isinstance(package.get("engines"), Mapping)
            else None,
            "workflow_path": "workflow.yaml" if (directory / "workflow.yaml").is_file() else None,
            "transform_entrypoints": transforms,
            "fixture_layout": inventory.layout,
            "fixture_case_count": inventory.case_count,
            "fixture_negative_case_count": inventory.negative_case_count,
            "check": check,
            "extractor": "codemod_node@1",
        }
        resources = _bundle_fixtures(
            tests_dir if tests_dir.is_dir() else None,
            list(inventory.input_paths) + list(inventory.expected_paths),
        )
        return SourceArtifact(
            source_type=self.source_type,
            uri=ref.uri,
            content=content,
            content_hash=compute_content_hash(content),
            repository=self.slug,
            path=ref.path,
            commit=self._commit,
            license_metadata=license_metadata,
            resources=resources,
        )

    def _run_gates(
        self,
        *,
        directory: Path,
        transforms: Sequence[str],
        manifest: Mapping[str, Any],
        package: Mapping[str, Any],
        tests_dir: Path | None,
        inventory: Any,
    ) -> CheckOutcome:
        """Gate A per transform, then Gate B once, combined into one outcome.

        Gate B is deliberately not folded into Gate A's pass condition
        for a *transform*: a malformed `expected` file is a property of
        the committed fixture set, so it is its own gate in `gates` and
        its own reason, and `build_check_payload` carries both.
        """
        if not self._run_checks:
            return CheckOutcome(
                passed=False,
                tier="executable",
                semantics="self-consistency",
                case_count=0,
                negative_case_count=inventory.negative_case_count,
                failures=("checks were not executed (run_checks=False)",),
                gates={"jssg_fixture_suite": "not_run", "output_wellformedness": "not_run"},
                detail={"reason": "checks_disabled"},
            )

        targets = manifest.get("targets")
        languages = _str_list(targets.get("languages")) if isinstance(targets, Mapping) else []
        language = languages[0] if languages else "typescript"
        capability_args = _capability_args(_str_list(manifest.get("capabilities")))
        declared = _declared_test_commands(package)
        # A `.ts` file under src/ is not automatically a transform.
        # `fs-access-mode-constants` keeps its own harness at
        # `src/workflow.test.ts` and declares `src/workflow.ts` as the
        # transform; running the test file as a transform resolves
        # `node:assert/strict` in the sandbox and fails all eight of its
        # cases. Undeclared src/ entries are therefore not invoked -- the
        # recipe's own script is the authority on what runs.
        runnable = [t for t in transforms if _transform_key(t) in declared] or transforms

        failures: list[str] = []
        gates: dict[str, str] = {}
        case_count = 0
        per_transform: list[dict[str, Any]] = []
        if not transforms:
            gates["jssg_fixture_suite"] = "not_run"
            failures.append("no transform entrypoint found under src/")
        for transform in runnable:
            spec = declared.get(_transform_key(transform))
            # A multi-transform recipe shares ONE tests/ directory, and each
            # transform owns a SUBSET of it. Invoking a single transform
            # against the whole directory makes it read every other
            # transform's fixtures as its own and fail on all of them --
            # `timers-deprecations` reports 3/15 passing on a catalog whose
            # own `npm test` is green. So the recipe's own `--filter` is
            # load-bearing and is read from the test script it declares,
            # never reconstructed. A transform with no declared filter gets
            # the whole directory, which is right for a single-transform
            # recipe and wrong for a multi-transform one; that case is
            # recorded rather than silently trusted.
            scoped = bool(spec and spec.filter)
            # Language comes from the recipe's own command when it declares
            # one, not from the manifest. `chalk-to-util-styletext` runs its
            # dependency-removal transform as `-l json` while the manifest
            # lists `javascript`/`typescript`; forcing the manifest value
            # makes the tool emit nothing at all, which reads as a broken
            # catalog rather than a mis-declared language.
            result = run_jssg_fixture_check(
                recipe_dir=directory,
                transform=transform,
                language=(spec.language if spec and spec.language else language),
                test_dir=spec.test_dir if spec else TESTS_DIRNAME,
                node_bin=self._node_bin,
                timeout_s=self._check_timeout_s,
                extra_args=_merge_args(
                    capability_args,
                    spec.extra if spec else (),
                    ("--filter", spec.filter) if spec and spec.filter else (),
                ),
            )
            per_transform.append(
                {
                    "transform": transform,
                    "filter": spec.filter if spec else None,
                    "test_dir_scoped": scoped,
                    **result.as_dict(),
                }
            )
            case_count += result.case_count
            failures.extend(f"{transform}: {message}" for message in result.failures)
        if runnable:
            gates["jssg_fixture_suite"] = "passed" if not failures else "failed"
            # A multi-transform recipe where no transform carries its own
            # filter cannot be scoped, so the "suite" that passed is the
            # union of every fixture and is not a per-transform assertion.
            # Surfaced as its own gate so a reader cannot mistake a
            # directory-wide pass for N scoped passes.
            unscoped = [t["transform"] for t in per_transform if not t["filter"]]
            if len(runnable) > 1 and unscoped:
                gates["per_transform_scoping"] = f"unfiltered:{','.join(unscoped)}"
            elif len(runnable) > 1:
                gates["per_transform_scoping"] = "declared"
        # src/ files that no declared command runs are recorded rather than
        # dropped: their presence is why a reader can tell "one transform,
        # one check" from "one transform plus an unverified test file".
        skipped = [t for t in transforms if _transform_key(t) not in declared]
        if skipped:
            gates["undeclared_src_entries"] = ",".join(skipped)

        expected_paths = [tests_dir / rel for rel in inventory.expected_paths] if tests_dir else []
        wellformed, detail_text = check_expected_wellformedness(
            expected_paths=expected_paths, node_exec=self._node_exec
        )
        gates["output_wellformedness"] = wellformed
        if wellformed == "malformed":
            failures.append(f"output well-formedness: {detail_text}")

        # Gate B can only DISQUALIFY, and only on `malformed`. The other
        # verdicts are statements about our ability to parse, not about the
        # transform: `no_parser` is a fixture type with no offline parser,
        # `duplicate_declaration` and `inherited_unparseable` are committed
        # outputs whose defect (if any) demonstrably predates the transform.
        # A genuine `malformed` is different in kind -- a JSON fixture that
        # does not parse, or a `.js` output that is not valid syntax while
        # its paired input is -- and only that one blocks.
        passed = gates.get("jssg_fixture_suite") == "passed" and wellformed not in (
            "malformed",
        )
        return CheckOutcome(
            passed=passed,
            tier="executable",
            semantics="self-consistency",
            case_count=case_count,
            negative_case_count=inventory.negative_case_count,
            failures=tuple(failures),
            gates=gates,
            detail={
                "runner_version": RUNNER_VERSION,
                "language": language,
                "capability_args": list(capability_args),
                "declared_commands": {
                    key: {"test_dir": spec.test_dir, "filter": spec.filter, "language": spec.language}
                    for key, spec in declared.items()
                },
                "wellformedness_detail": detail_text,
                "per_transform": per_transform,
            },
        )

    def fingerprint(self, artifact: SourceArtifact) -> str:
        return artifact.content_hash


def select_ingestible(
    *,
    artifacts: Sequence[SourceArtifact],
    verdicts: Mapping[str, str | LicenseVerdict],
) -> tuple[list[SourceArtifact], dict[str, int]]:
    """Split artifacts into accepted and rejected, with a reason histogram.

    `verdicts` is keyed by `uri`, `path` or `content_hash` -- a caller
    that keyed it some other way gets every artifact rejected, which is
    the safe direction to be wrong in. The histogram key is the verdict
    DECISION, not its free-text reason: `QUARANTINE` and `REJECT` are
    the two things a caller acts on, and splitting them further by
    reason text would make the counts incomparable across runs.
    """
    accepted: list[SourceArtifact] = []
    histogram: dict[str, int] = {}
    for artifact in artifacts:
        raw = None
        for key in (artifact.uri, artifact.path, artifact.content_hash):
            if key and key in verdicts:
                raw = verdicts[key]
                break
        if raw is None:
            reason = "MISSING_VERDICT"
        elif isinstance(raw, LicenseVerdict):
            reason = raw.decision
        else:
            reason = str(raw)
        if reason == "ALLOW":
            accepted.append(artifact)
        else:
            histogram[reason] = histogram.get(reason, 0) + 1
    return accepted, histogram
