"""
The OpenRewrite documentation catalog as a `SourceAdapter` -- STATIC CHECK
TIER ONLY.

WHY TIER 1 IS STATIC AND WILL STAY STATIC AT THIS BUDGET
    Asserting that a recipe actually transforms before -> after means
    running `RewriteTest`, which needs a JDK (21; a JRE is explicitly
    insufficient because OpenRewrite uses compiler internals), Gradle or
    Maven, network for dependency resolution, resolution of the recipe
    under test's real third-party classpath, and a Code Genome Project
    token for the build plugins. Several version-resolving recipes fetch
    from `downloads.gradle.org` at run time, so the run is not hermetic
    even with every credential in hand. That is a standing infrastructure
    dependency per recipe, and it is not compatible with a $2/day budget
    or with an offline CI.

    So a pass here means: the recipe's declaration parses, its id is
    well formed, and its module is Apache-2.0 and not archived. It never
    means the recipe transforms correctly, and `check_tier` is `"static"`
    on every payload so no downstream surface can round that up.

THE LICENSE REVERSAL THIS MODULE EXISTS TO ENFORCE
    The official 52-row module table is 30 Apache-2.0 and 22 Moderne
    Source Available. The Moderne text forbids making the functionality
    available to third parties *as a service* and names Sourcegraph,
    Amazon Q Code Transformer and Broadcom Application Advisor as
    prohibited third parties. Serving a Procedure that IS such a recipe
    to coding agents over an API is the prohibited use, so all 22 are
    rejected here, and the rejection reason is recorded per page so the
    count is auditable rather than asserted.

    The structural trap: Moderne-licensed repos do not carry an
    SPDX-detectable root license at all. They carry a 43-byte pointer
    file whose entire content is `LICENSE/moderne-source-available-license.md`,
    which is why GitHub reports `NOASSERTION`. A gate that reads the root
    license text and treats an unreadable id as permissive admits all 22.
    This module does not read license text at all for this source -- it
    gates on the per-module table, and the table's own derivation is
    recorded on every artifact -- but the reason is stated here because
    the trap is the reason the table exists and the next person will look
    for it.

    Apache-2.0 and "actively maintained" are INDEPENDENT axes. Five
    Apache-2.0 modules (docker, gradle, kotlin, and others) have
    archived upstream repositories; an archived module is rejected and
    says so, separately from its license.

THIRD PARTY, NOT OPENREWRITE
    `picnic/errorprone` is the largest namespace in the catalog by a wide
    margin. It is `tech.picnic:error-prone-support`, referenced from
    `rewrite-migrate-java` as a runtime-only dependency. It is not an
    OpenRewrite repository, it is absent from the 52-row table, and its
    license was never verified. It maps to no module and is therefore
    rejected by the same "no ruling has been made" rule that quarantines
    an unknown license.

UNVERIFIED, MARKED AS SUCH
    The table is a snapshot of the documentation page as read on
    2026-09-28, with versions 8.90.1 / 3.37.0. Two known
    documentation inconsistencies are carried as data rather than
    resolved here: `rewrite-go` appears in a newer table under Moderne
    and is absent from this one, and the prose calls Kotlin
    source-available while this table lists it Apache-2.0. Both are
    handled by failing closed -- an absent module is rejected.
"""
from __future__ import annotations

import html as html_module
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Mapping, Sequence

from app.services.ingestion_sources.base import (
    SourceArtifact,
    SourceRef,
    compute_content_hash,
)
from app.services.ingestion_sources.codemod_checks import (
    CHECK_SEMANTICS,
    CheckOutcome,
    FixtureInventory,
    build_check_payload,
)

SITEMAP_URL = "https://docs.openrewrite.org/sitemap.xml"
DOCS_ORIGIN = "https://docs.openrewrite.org"

# Pinned on every payload so a stored static check is attributable to the
# table version that produced it.
STATIC_RUNNER_VERSION = "openrewrite-static-check@v1"

# The documentation table this gate encodes, and the date it was read.
LICENSE_TABLE_SOURCE = "docs.openrewrite.org/reference/latest-versions-of-every-openrewrite-module"
LICENSE_TABLE_SNAPSHOT = "2026-09-28"

APACHE2 = "apache2"
MODERNE_SOURCE_AVAILABLE = "moderne-sa"
MODERNE_PROPRIETARY = "moderne-proprietary"
THIRD_PARTY_UNVERIFIED = "third-party-unverified"

# The only tier this source will ingest. An allowlist of one, deliberately:
# extending it is a ruling about a license nobody has judged yet, which
# is a founder decision and not a code change.
INGESTIBLE_TIERS: frozenset[str] = frozenset({APACHE2})

# `NOASSERTION` rather than a fabricated id for the non-Apache rows. The
# docs table reports a human-readable license NAME for these, not an SPDX
# id, and GitHub reports `NOASSERTION` on the repos themselves.
_SPDX_BY_TIER: dict[str, str] = {
    APACHE2: "Apache-2.0",
    MODERNE_SOURCE_AVAILABLE: "NOASSERTION",
    MODERNE_PROPRIETARY: "NOASSERTION",
    THIRD_PARTY_UNVERIFIED: "NOASSERTION",
}


@dataclass(frozen=True)
class ModuleEntry:
    """One row of the verified per-module license table."""

    maven_group: str
    artifact_id: str
    version: str
    license_tier: str
    repo: str
    archived: bool = False
    is_plugin_or_bom: bool = False
    aliases: tuple[str, ...] = ()

    @property
    def module_id(self) -> str:
        return f"{self.maven_group}:{self.artifact_id}"

    @property
    def spdx(self) -> str:
        return _SPDX_BY_TIER.get(self.license_tier, "NOASSERTION")

    def as_dict(self) -> dict[str, Any]:
        return {
            "maven_group": self.maven_group,
            "artifact_id": self.artifact_id,
            "version": self.version,
            "module_id": self.module_id,
            "spdx": self.spdx,
            "license_tier": self.license_tier,
            "repo": self.repo,
            "archived": self.archived,
            "is_plugin_or_bom": self.is_plugin_or_bom,
        }


def _core_rows() -> list[ModuleEntry]:
    """The 30 Apache-2.0 rows of the official 52-row table."""
    return [
        ModuleEntry("org.openrewrite", "rewrite-bom", "8.90.1", APACHE2, "openrewrite/rewrite", is_plugin_or_bom=True),
        ModuleEntry("org.openrewrite", "rewrite-maven-plugin", "6.46.1", APACHE2, "openrewrite/rewrite-maven-plugin", is_plugin_or_bom=True),
        ModuleEntry("org.openrewrite", "rewrite-gradle-plugin", "7.39.0", APACHE2, "openrewrite/rewrite-gradle-plugin", is_plugin_or_bom=True),
        ModuleEntry("org.openrewrite.recipe", "rewrite-recipe-bom", "3.37.0", APACHE2, "openrewrite/rewrite", is_plugin_or_bom=True),
        ModuleEntry("org.openrewrite", "rewrite-core", "8.90.1", APACHE2, "openrewrite/rewrite"),
        ModuleEntry("org.openrewrite", "rewrite-docker", "8.90.1", APACHE2, "openrewrite/rewrite-docker", archived=True),
        ModuleEntry("org.openrewrite", "rewrite-gradle", "8.90.1", APACHE2, "openrewrite/rewrite-gradle", archived=True),
        ModuleEntry("org.openrewrite", "rewrite-groovy", "8.90.1", APACHE2, "openrewrite/rewrite-groovy"),
        ModuleEntry("org.openrewrite", "rewrite-hcl", "8.90.1", APACHE2, "openrewrite/rewrite-hcl"),
        ModuleEntry("org.openrewrite", "rewrite-java", "8.90.1", APACHE2, "openrewrite/rewrite-java"),
        ModuleEntry("org.openrewrite", "rewrite-json", "8.90.1", APACHE2, "openrewrite/rewrite-json"),
        ModuleEntry("org.openrewrite", "rewrite-kotlin", "8.90.1", APACHE2, "openrewrite/rewrite-kotlin", archived=True),
        ModuleEntry("org.openrewrite", "rewrite-maven", "8.90.1", APACHE2, "openrewrite/rewrite-maven"),
        ModuleEntry("org.openrewrite", "rewrite-polyglot", "2.11.1", APACHE2, "openrewrite/rewrite-polyglot"),
        ModuleEntry("org.openrewrite", "rewrite-properties", "8.90.1", APACHE2, "openrewrite/rewrite-properties"),
        ModuleEntry("org.openrewrite", "rewrite-protobuf", "8.90.1", APACHE2, "openrewrite/rewrite-protobuf"),
        ModuleEntry("org.openrewrite", "rewrite-templating", "1.44.0", APACHE2, "openrewrite/rewrite-templating"),
        ModuleEntry("org.openrewrite", "rewrite-toml", "8.90.1", APACHE2, "openrewrite/rewrite-toml"),
        ModuleEntry("org.openrewrite", "rewrite-xml", "8.90.1", APACHE2, "openrewrite/rewrite-xml"),
        ModuleEntry("org.openrewrite", "rewrite-yaml", "8.90.1", APACHE2, "openrewrite/rewrite-yaml"),
        ModuleEntry("org.openrewrite.meta", "rewrite-analysis", "2.37.1", APACHE2, "openrewrite/rewrite-analysis"),
        ModuleEntry("org.openrewrite.recipe", "rewrite-all", "1.28.1", APACHE2, "openrewrite/rewrite-all"),
        ModuleEntry("org.openrewrite.recipe", "rewrite-jackson", "1.29.0", APACHE2, "openrewrite/rewrite-jackson"),
        ModuleEntry("org.openrewrite.recipe", "rewrite-java-dependencies", "1.60.2", APACHE2, "openrewrite/rewrite-java-dependencies"),
        ModuleEntry("org.openrewrite.recipe", "rewrite-liberty", "1.26.1", APACHE2, "openrewrite/rewrite-liberty"),
        ModuleEntry("org.openrewrite.recipe", "rewrite-micronaut", "2.36.0", APACHE2, "openrewrite/rewrite-micronaut"),
        ModuleEntry("org.openrewrite.recipe", "rewrite-netty", "0.11.1", APACHE2, "openrewrite/rewrite-netty"),
        ModuleEntry("org.openrewrite.recipe", "rewrite-openapi", "0.33.1", APACHE2, "openrewrite/rewrite-openapi"),
        ModuleEntry("org.openrewrite.recipe", "rewrite-quarkus", "2.34.1", APACHE2, "openrewrite/rewrite-quarkus"),
        ModuleEntry("org.openrewrite.recipe", "rewrite-third-party", "0.45.0", APACHE2, "openrewrite/rewrite-third-party"),
    ]


def _moderne_rows() -> list[ModuleEntry]:
    """The 22 Moderne Source Available rows of the same table."""
    return [
        ModuleEntry("org.openrewrite", "rewrite-cobol", "2.22.0", MODERNE_SOURCE_AVAILABLE, "openrewrite/rewrite-cobol", archived=True),
        ModuleEntry("org.openrewrite", "rewrite-csharp", "8.90.1", MODERNE_SOURCE_AVAILABLE, "openrewrite/rewrite-csharp", archived=True),
        ModuleEntry("org.openrewrite", "rewrite-javascript", "8.90.1", MODERNE_SOURCE_AVAILABLE, "openrewrite/rewrite-javascript", archived=True),
        ModuleEntry("org.openrewrite", "rewrite-python", "8.90.1", MODERNE_SOURCE_AVAILABLE, "openrewrite/rewrite-python", archived=True),
        ModuleEntry("org.openrewrite.recipe", "rewrite-apache", "2.30.0", MODERNE_SOURCE_AVAILABLE, "openrewrite/rewrite-apache"),
        ModuleEntry("org.openrewrite.recipe", "rewrite-cucumber-jvm", "2.15.0", MODERNE_SOURCE_AVAILABLE, "openrewrite/rewrite-cucumber-jvm"),
        ModuleEntry("org.openrewrite.recipe", "rewrite-feature-flags", "1.23.1", MODERNE_SOURCE_AVAILABLE, "openrewrite/rewrite-feature-flags"),
        ModuleEntry("org.openrewrite.recipe", "rewrite-github-actions", "3.29.0", MODERNE_SOURCE_AVAILABLE, "openrewrite/rewrite-github-actions"),
        ModuleEntry("org.openrewrite.recipe", "rewrite-gitlab", "0.24.1", MODERNE_SOURCE_AVAILABLE, "openrewrite/rewrite-gitlab"),
        ModuleEntry("org.openrewrite.recipe", "rewrite-hibernate", "2.25.0", MODERNE_SOURCE_AVAILABLE, "openrewrite/rewrite-hibernate"),
        ModuleEntry("org.openrewrite.recipe", "rewrite-jenkins", "0.37.1", MODERNE_SOURCE_AVAILABLE, "openrewrite/rewrite-jenkins"),
        ModuleEntry("org.openrewrite.recipe", "rewrite-joda", "0.10.1", MODERNE_SOURCE_AVAILABLE, "openrewrite/rewrite-joda"),
        ModuleEntry("org.openrewrite.recipe", "rewrite-logging-frameworks", "3.32.0", MODERNE_SOURCE_AVAILABLE, "openrewrite/rewrite-logging-frameworks"),
        ModuleEntry("org.openrewrite.recipe", "rewrite-micrometer", "0.30.1", MODERNE_SOURCE_AVAILABLE, "openrewrite/rewrite-micrometer"),
        ModuleEntry("org.openrewrite.recipe", "rewrite-migrate-java", "3.42.0", MODERNE_SOURCE_AVAILABLE, "openrewrite/rewrite-migrate-java"),
        ModuleEntry("org.openrewrite.recipe", "rewrite-okhttp", "0.24.1", MODERNE_SOURCE_AVAILABLE, "openrewrite/rewrite-okhttp"),
        ModuleEntry("org.openrewrite.recipe", "rewrite-prethink", "1.2.0", MODERNE_SOURCE_AVAILABLE, "openrewrite/rewrite-prethink"),
        ModuleEntry("org.openrewrite.recipe", "rewrite-rewrite", "0.30.0", MODERNE_SOURCE_AVAILABLE, "openrewrite/rewrite-rewrite"),
        ModuleEntry("org.openrewrite.recipe", "rewrite-spring", "6.37.0", MODERNE_SOURCE_AVAILABLE, "openrewrite/rewrite-spring"),
        ModuleEntry("org.openrewrite.recipe", "rewrite-spring-to-quarkus", "0.11.1", MODERNE_SOURCE_AVAILABLE, "openrewrite/rewrite-spring-to-quarkus"),
        ModuleEntry("org.openrewrite.recipe", "rewrite-static-analysis", "2.41.0", MODERNE_SOURCE_AVAILABLE, "openrewrite/rewrite-static-analysis"),
        ModuleEntry("org.openrewrite.recipe", "rewrite-testing-frameworks", "3.44.0", MODERNE_SOURCE_AVAILABLE, "openrewrite/rewrite-testing-frameworks"),
    ]


# Not an OpenRewrite module at all. Present so the largest namespace in
# the catalog is rejected by name with a reason, rather than falling
# through the namespace resolver and looking like a bug.
PICNIC_MODULE = ModuleEntry(
    maven_group="tech.picnic.error-prone-support",
    artifact_id="error-prone-support",
    version="latest.release",
    license_tier=THIRD_PARTY_UNVERIFIED,
    repo="picnic/errorprone",
    aliases=(
        "tech.picnic:error-prone-support",
        "tech.picnic.error-prone-support:error-prone-contrib",
    ),
)

MODULES: dict[str, ModuleEntry] = {}
for _entry in (*_core_rows(), *_moderne_rows(), PICNIC_MODULE):
    MODULES[_entry.module_id] = _entry
    for _alias in _entry.aliases:
        MODULES[_alias] = _entry

MODULE_TABLE_SIZE = len(_core_rows()) + len(_moderne_rows())

# NAMESPACES TOO BIG TO NAME. Apache-2.0 and archived are independent
# axes, so an archived module is rejected with `module_archived`, not
# with a license reason, and a reader can tell the two apart. This list
# is the 15 repositories named in the research out of 23 archived; the
# 8 unnamed ones are NOT silently assumed live -- an unnamed repo is
# simply not in this set, and the set is documented as partial.
ARCHIVED_REPOS: frozenset[str] = frozenset({
    "openrewrite/rewrite-csharp",
    "openrewrite/rewrite-javascript",
    "openrewrite/rewrite-python",
    "openrewrite/rewrite-docker",
    "openrewrite/rewrite-gradle",
    "openrewrite/rewrite-kotlin",
    "openrewrite/rewrite-houston-jug",
    "openrewrite/rewrite-sandbox",
    "openrewrite/rewrite-generative-ai",
    "openrewrite/rewrite-checkstyle",
    "openrewrite/rewrite-java-8",
    "openrewrite/rewrite-testcontainers",
    "openrewrite/rewrite-jhipster",
    "openrewrite/rewrite-recommendations",
    "openrewrite/rewrite-cloud-suitability-analyzer",
})


@dataclass(frozen=True)
class ModuleGate:
    """The per-module admission decision, with the reason attached.

    `reason_code` is a stable token and `reason` is the prose. The token
    is what a rejection histogram counts: prose changes when the
    explanation improves, and a count keyed on it would silently become
    incomparable between runs.
    """

    module_id: str
    allowed: bool
    reason_code: str
    reason: str
    license_tier: str
    spdx: str
    repo: str | None
    version: str | None
    archived: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "module_id": self.module_id,
            "allowed": self.allowed,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "license_tier": self.license_tier,
            "spdx": self.spdx,
            "repo": self.repo,
            "version": self.version,
            "archived": self.archived,
        }


def gate_module(module_id: str) -> ModuleGate:
    """Admit or refuse one Maven module, with the reason.

    Order matters. A Moderne module is refused for its license even
    though it is also archived, because the license is the durable
    reason: an un-archived fork of `rewrite-spring` would still be
    forbidden. Archival is checked second, for the Apache modules it is
    the only thing that refuses them.
    """
    entry = MODULES.get(module_id)
    if entry is None:
        return ModuleGate(
            module_id=module_id,
            allowed=False,
            reason_code="no_ruling",
            reason=(
                "no license ruling exists for this module: it is not in the verified "
                f"per-module table ({LICENSE_TABLE_SOURCE}, read {LICENSE_TABLE_SNAPSHOT}), "
                "and an unverified module is refused rather than assumed permissive"
            ),
            license_tier=THIRD_PARTY_UNVERIFIED,
            spdx="NOASSERTION",
            repo=None,
            version=None,
            archived=False,
        )

    archived = entry.archived or entry.repo in ARCHIVED_REPOS
    if entry.license_tier not in INGESTIBLE_TIERS:
        if entry.license_tier == THIRD_PARTY_UNVERIFIED:
            code = "third_party_unverified"
            reason = (
                "third-party module, not an OpenRewrite project, and its license was "
                "never verified; the catalog's largest namespace resolves here and is "
                "refused by default"
            )
        elif entry.license_tier == MODERNE_PROPRIETARY:
            code = "moderne_proprietary"
            reason = "Moderne Proprietary License: redistribution is not permitted at all"
        else:
            code = "moderne_source_available"
            reason = (
                "Moderne Source Available License forbids making the functionality "
                "available to third parties as a service, which is what serving a "
                "Procedure to coding agents over an API is"
            )
        return ModuleGate(
            module_id=entry.module_id,
            allowed=False,
            reason_code=code,
            reason=reason,
            license_tier=entry.license_tier,
            spdx=entry.spdx,
            repo=entry.repo,
            version=entry.version,
            archived=archived,
        )

    if archived:
        return ModuleGate(
            module_id=entry.module_id,
            allowed=False,
            reason_code="module_archived",
            reason=(
                f"module is Apache-2.0 but its upstream repository {entry.repo} is "
                "archived; license and maintenance are independent axes and both must pass"
            ),
            license_tier=entry.license_tier,
            spdx=entry.spdx,
            repo=entry.repo,
            version=entry.version,
            archived=True,
        )

    if entry.is_plugin_or_bom:
        return ModuleGate(
            module_id=entry.module_id,
            allowed=False,
            reason_code="bom_or_plugin",
            reason="BOM / build plugin: it declares dependencies rather than containing recipes",
            license_tier=entry.license_tier,
            spdx=entry.spdx,
            repo=entry.repo,
            version=entry.version,
            archived=False,
        )

    return ModuleGate(
        module_id=entry.module_id,
        allowed=True,
        reason_code="allowed",
        reason=(
            f"Apache-2.0 per the verified per-module table and its repository is not "
            f"archived ({LICENSE_TABLE_SNAPSHOT})"
        ),
        license_tier=entry.license_tier,
        spdx=entry.spdx,
        repo=entry.repo,
        version=entry.version,
        archived=False,
    )


# ---------------------------------------------------------------------------
# Namespace -> module attribution
# ---------------------------------------------------------------------------
#
# The default rule is mechanical and stated once: for a documentation
# URL under `/recipes/<parts...>/<slug>`, the package is
# `org.openrewrite.<parts[0]>` and the module is `rewrite-<parts[0]>`.
# That covers `quarkus/*` -> rewrite-quarkus, `maven/*` -> rewrite-maven,
# `gradle/*` -> rewrite-gradle, `cobol/*` -> rewrite-cobol and the rest.
#
# The table below is the set of places where the mechanical guess is
# WRONG, each one read off the namespace attribution in the research.
# Matched longest-prefix-first, so `org.openrewrite.java.dependencies`
# beats `org.openrewrite.java`. Getting this wrong is a rejection either
# way (a wrong guess that lands on an Apache module would be the
# dangerous direction), which is why the entries below are the verified
# attributions and nothing is extrapolated beyond them.

NAMESPACE_MODULES: dict[str, str] = {
    # rewrite-third-party carries the vendored-library recipes; the
    # package segments are the library, not the module.
    "org.openrewrite.apache": "rewrite-third-party",
    "org.openrewrite.oracle": "rewrite-third-party",
    "org.openrewrite.amazon": "rewrite-third-party",
    "org.openrewrite.codehaus": "rewrite-third-party",
    "org.openrewrite.timefold": "rewrite-third-party",
    "org.openrewrite.sh": "rewrite-third-party",
    "org.openrewrite.hibernate": "rewrite-hibernate",
    "org.openrewrite.hibernate.validator": "rewrite-third-party",
    "org.openrewrite.axonframework": "rewrite-third-party",
    "org.openrewrite.google": "rewrite-third-party",
    "org.openrewrite.java.springdoc": "rewrite-third-party",
    "org.openrewrite.java.flyway": "rewrite-third-party",
    "org.openrewrite.java.camel": "rewrite-third-party",
    # Apache core / java split.
    "org.openrewrite.java.search": "rewrite-core",
    "org.openrewrite.java.recipes": "rewrite-core",
    "org.openrewrite.java.format": "rewrite-java",
    "org.openrewrite.java.jspecify": "rewrite-java",
    "org.openrewrite.java.ai": "rewrite-java",
    "org.openrewrite.java.dependencies": "rewrite-java-dependencies",
    "org.openrewrite.java.netty": "rewrite-netty",
    "org.openrewrite.java.liberty": "rewrite-liberty",
    # Moderne-licensed namespaces that do not share the module's name.
    "org.openrewrite.java.migrate": "rewrite-migrate-java",
    "org.openrewrite.java.spring": "rewrite-spring",
    "org.openrewrite.java.logging": "rewrite-logging-frameworks",
    "org.openrewrite.java.testing": "rewrite-testing-frameworks",
    "org.openrewrite.java.joda": "rewrite-joda",
    "org.openrewrite.quarkus.spring": "rewrite-spring-to-quarkus",
    "org.openrewrite.quarkus.quarkus2": "rewrite-quarkus",
    "org.openrewrite.quarkus.migrate": "rewrite-quarkus",
    "org.openrewrite.quarkus.search": "rewrite-quarkus",
    "org.openrewrite.cucumber": "rewrite-cucumber-jvm",
    "org.openrewrite.github": "rewrite-github-actions",
    "org.openrewrite.featureflags": "rewrite-feature-flags",
    "org.openrewrite.micrometer": "rewrite-micrometer",
    "org.openrewrite.jenkins": "rewrite-jenkins",
    "org.openrewrite.gitlab": "rewrite-gitlab",
    "org.openrewrite.prethink": "rewrite-prethink",
    "org.openrewrite.staticanalysis": "rewrite-static-analysis",
    "org.openrewrite.recipes.rewrite": "rewrite-rewrite",
    "org.openrewrite.javascript": "rewrite-javascript",
    "org.openrewrite.scala.migrate": "rewrite-migrate-java",
    # The one doc-site name that is not a package at all.
    "org.openrewrite.xml.liberty": "rewrite-liberty",
    "org.openrewrite.maven.liberty": "rewrite-liberty",
    "org.openrewrite.properties.search": "rewrite-properties",
    "org.openrewrite.groovy.format": "rewrite-groovy",
    "org.openrewrite.docker.search": "rewrite-docker",
    "org.openrewrite.openapi.swagger": "rewrite-openapi",
    "org.openrewrite.analysis": "rewrite-analysis",
    "org.openrewrite.all": "rewrite-all",
}

# URL-path prefixes that must not go through package resolution at all.
SLUG_PREFIX_MODULES: dict[str, str] = {
    # Largest namespace in the catalog, third-party, never verified.
    "picnic/errorprone": PICNIC_MODULE.module_id,
}

# Mechanical fallbacks for the three package roots the curated table does
# not name, because they are module NAMES rather than documentation
# namespaces. Keyed by package, valued by artifact.
_PACKAGE_DEFAULT_ARTIFACTS: dict[str, str] = {
    "org.openrewrite.core": "rewrite-core",
    "org.openrewrite.java": "rewrite-java",
    "org.openrewrite.analysis": "rewrite-analysis",
}

# artifact_id -> the table's own `group:artifact` for it. The Maven
# group differs per module family (core language modules under
# `org.openrewrite`, recipe modules under `org.openrewrite.recipe`, the
# analysis module under its own), so it is read off the table rather
# than constructed from a naming convention.
_MODULE_ID_BY_ARTIFACT: dict[str, str] = {}
for _entry in (*_core_rows(), *_moderne_rows(), PICNIC_MODULE):
    _MODULE_ID_BY_ARTIFACT.setdefault(_entry.artifact_id, _entry.module_id)


def _module_id_for_artifact(artifact_id: str) -> str:
    known = _MODULE_ID_BY_ARTIFACT.get(artifact_id)
    if known is not None:
        return known
    return f"org.openrewrite.recipe:{artifact_id}"


def resolve_namespace_module(namespace: str) -> str | None:
    """The `group:artifact` a documentation namespace belongs to.

    `namespace` is the URL path under `/recipes/`, WITHOUT the trailing
    recipe slug: `quarkus/updates`, `java/migrate`. Every prefix of the
    namespace is tried longest-first, so `java/migrate` resolves through
    `org.openrewrite.java.migrate` (Moderne-licensed) and never through
    the shorter `org.openrewrite.java` (Apache-2.0) -- a shorter match
    winning here would be a license failure in the dangerous direction.
    The curated table is consulted first; only if no prefix is in it does
    the mechanical rule (`org.openrewrite.<first segment>` ->
    `rewrite-<first segment>`) apply.

    Returns None when nothing claims the namespace, which the gate then
    refuses: the correct outcome for something nobody ruled on.
    """
    slug = namespace.strip("/")
    if not slug:
        return None
    segments = slug.split("/")
    if len(segments) > 1:
        prefix_key = "/".join(segments[:2])
        if prefix_key in SLUG_PREFIX_MODULES:
            return SLUG_PREFIX_MODULES[prefix_key]
    for depth in range(len(segments), 0, -1):
        package = "org.openrewrite." + ".".join(segments[:depth])
        artifact = NAMESPACE_MODULES.get(package)
        if artifact is not None:
            return _module_id_for_artifact(artifact)
    mechanical = "org.openrewrite." + segments[0]
    if mechanical in _PACKAGE_DEFAULT_ARTIFACTS:
        return _module_id_for_artifact(_PACKAGE_DEFAULT_ARTIFACTS[mechanical])
    return _module_id_for_artifact(f"rewrite-{segments[0].replace('_', '-')}")


def gate_namespace(namespace: str) -> ModuleGate:
    """Resolve a documentation namespace and gate it in one step."""
    module_id = resolve_namespace_module(namespace)
    if module_id is None:
        return gate_module("")
    return gate_module(module_id)


# ---------------------------------------------------------------------------
# Doc-page parsing
# ---------------------------------------------------------------------------

_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)

# A Java fully qualified name in the SHAPE OpenRewrite recipe ids take:
# lowercase-starting package segments, then a type name. Deliberately
# NOT `org\.openrewrite\..*` -- the catalog's packages are not all under
# that root. The vendored third-party recipes ship as
# `software.amazon.awssdk.v2migration.AddCommentToMethod` and similar,
# and a regex anchored on `org.openrewrite` silently misses every one of
# them.
_JAVA_FQCN_RE = re.compile(r"^[a-z][A-Za-z0-9_]*(?:\.[a-z][A-Za-z0-9_]*){2,}\.[A-Z][A-Za-z0-9_]*$")

_TAG_RE = re.compile(r"<[^>]+>")
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_TAG_LINK_RE = re.compile(r"/recipes/tags/([A-Za-z0-9._-]+)")
_TEST_METHOD_RE = re.compile(r"\b([A-Z][A-Za-z0-9_]*Test)#([A-Za-z0-9_]+)")
_TEST_CLASS_RE = re.compile(r"\b([A-Z][A-Za-z0-9_]*Test)\b")

# The page states module coordinates and versions in its "activate this
# recipe" instructions, and also in its dependency list. Every one is
# collected; only the one matching the resolved module is treated as this
# recipe's own.
_MODULE_COORD_RE = re.compile(
    r"\b(org\.openrewrite(?:\.[a-z][a-z0-9]*)*):(rewrite[a-z0-9-]+):(\d[\w.+-]*)"
)

# A page's own recipe id is quoted repeatedly: the description meta tag,
# the bold line under the title, the GitHub code-search link, the
# activation snippet, the hosted-run link. Site chrome is quoted once, in
# a footer. The live pilot showed a whole-page scan returning
# `org.openrewrite.table.SourcesFileResults` for a page whose real id was
# `software.amazon.awssdk.v2migration.AddCommentToMethod`, so an id must
# now be corroborated by more than one mention.
_MIN_ID_OCCURRENCES = 2

# The documentation site's own package. It is not a recipe, it appears
# on every page in the catalog as the results-table model classes, and
# it is the one false positive the occurrence rule above was observed to
# miss. Named rather than pattern-matched: a general "looks like site
# chrome" heuristic would be a guess about markup this module cannot see.
_SITE_CHROME_PACKAGES: frozenset[str] = frozenset({"org.openrewrite.table"})

_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(r"([\w:-]+)\s*=\s*(?:\"([^\"]*)\"|([^\s>]+))")

# The rendered page's own content region, and where it ends. The site's
# results-table footer lives outside it and is full of qualified names
# that look exactly like recipe ids, so the scan has to stop at a real
# boundary rather than at the end of the document.
_MAIN_REGION_RE = re.compile(r'<div class="theme-doc-markdown markdown">', re.IGNORECASE)
_CONTENT_END_TAGS: tuple[str, ...] = (
    "<footer",
    "</main>",
    "</article>",
    "theme-doc-footer",
    "pagination-nav",
)

_DECLARATION_LINE_RE = _JAVA_FQCN_RE  # the shape a bare id line must have

# A well-formed recipe id: at least three dotted package segments under a
# Java package convention, then a type name. No empty segment, no
# trailing dot. Independent of which root the package sits under, because
# the roots genuinely differ across the catalog.
_WELL_FORMED_ID_RE = re.compile(r"^[a-z][A-Za-z0-9_]*(?:\.[a-z][A-Za-z0-9_]*){2,}\.[A-Z][A-Za-z0-9_]*$")

_MAX_DESCRIPTION_CHARS = 600


class OpenRewriteIdUnavailable(Exception):
    """A recipe doc page yielded no unambiguous recipe FQCN.

    Raised instead of deriving one. The URL slug is NOT the FQCN
    (`.../changerepositorygroupid_artifactid` is
    `org.openrewrite.quarkus.ChangeRepositoryGroupId`), and inventing an
    id from a slug would mint a plausible, unopenable Procedure.
    """


class OpenRewritePageUnavailable(Exception):
    """A sitemap URL did not return 200.

    Kept distinct from `OpenRewriteIdUnavailable` because the two are
    different catalog-health findings and lumping them would hide one:
    a 200 page with no readable id is a parsing problem, and a 404 in a
    sitemap is a stale entry. The sitemap does contain stale entries --
    `.../quarkus/updates/addquarkusproperty` returns 404 while a sibling
    page in the same namespace resolves -- so the rate is worth
    measuring rather than assuming.
    """


def _html_to_lines(page: str) -> list[str]:
    text = _TAG_RE.sub("\n", page)
    text = html_module.unescape(text)
    return [line.strip() for line in text.split("\n") if line.strip()]


def _meta_content(page: str, names: frozenset[str]) -> str | None:
    """A `<meta>` tag's `content`, for the named `name`/`property` keys.

    Attribute values are read one by one rather than by a single regex
    over the tag, because the rendered pages are minified and the
    minifier drops the quotes: `<meta name=description content=com.Foo>`
    is what actually comes back, and a quoted-value-only pattern reads
    nothing at all from it.
    """
    for match in _META_TAG_RE.finditer(page):
        attrs: dict[str, str] = {}
        for attribute in _ATTR_RE.finditer(match.group(0)):
            value = attribute.group(2) if attribute.group(2) is not None else attribute.group(3)
            attrs[attribute.group(1).lower()] = value or ""
        key = (attrs.get("name") or attrs.get("property") or "").lower()
        if key in names and attrs.get("content"):
            return attrs["content"].strip()
    return None


def _content_region(page: str) -> str:
    """The page's recipe body, bounded.

    Preferred boundary is the rendered content container; otherwise the
    region starts after the `<h1>`. Either way it is cut at the first
    footer, so the site-wide results table -- whose classes are named
    `org.openrewrite.table.*` and match the recipe-id shape exactly --
    can never be read as one.
    """
    region_match = _MAIN_REGION_RE.search(page)
    if region_match is not None:
        body = page[region_match.end() :]
    else:
        heading = _H1_RE.search(page)
        body = page[heading.end() :] if heading else page
    end = min(
        (body.find(tag) for tag in _CONTENT_END_TAGS if body.find(tag) != -1),
        default=-1,
    )
    return body[:end] if end != -1 else body


def _is_plausible_id(page: str, candidate: str) -> bool:
    """Is this string plausibly THIS page's recipe id?

    Three conditions, and each one exists because the live pilot
    produced a failure without it:

    1. Well formed as a Java FQCN.
    2. Not the documentation site's own package, which is on every page.
    3. Mentioned at least twice. A page quotes its own id in the meta
       tag, the bold line under the title, the source link, the
       activation snippet and the hosted-run link; chrome is mentioned
       once. Without this, a footer class name reads as the recipe.
    """
    if not _WELL_FORMED_ID_RE.match(candidate):
        return False
    package = candidate.rsplit(".", 1)[0]
    if package in _SITE_CHROME_PACKAGES:
        return False
    return page.count(candidate) >= _MIN_ID_OCCURRENCES


def extract_recipe_id(page: str) -> str:
    """The page's own recipe FQCN, or raise `OpenRewriteIdUnavailable`.

    Two strategies, strongest first, and the order is load-bearing:

    1. The page's own `og:description` / `description` meta tag, which
       on the real rendered pages carries exactly the recipe id.
    2. The first line that is nothing but a recipe id, searching only
       inside the page's content region (see `_content_region`).

    Both go through `_is_plausible_id`. The region bound stops the scan
    reaching the footer; the occurrence count and the chrome exclusion
    stop a footer value that sits inside the region, or a page whose
    content region this module failed to locate, from being returned as
    a recipe id.

    The URL slug is never used to build an id. The slug
    `changerepositorygroupid_artifactid` is not the FQCN, and deriving
    one from it would produce an id that resolves to nothing.
    """
    for name in frozenset({"og:description", "description"}):
        value = _meta_content(page, {name})
        if value and _is_plausible_id(page, value):
            return value

    for line in _html_to_lines(_content_region(page)):
        if _is_plausible_id(page, line):
            return line

    raise OpenRewriteIdUnavailable(
        "no recipe id on the page: neither the description meta tag nor a bare "
        "id line inside the page's content region carried one that is "
        "corroborated by a second mention"
    )


def extract_declared_modules(page: str) -> dict[str, str]:
    """Every `group:artifact -> version` the page names, as a mapping.

    ALL of them, not the first: a recipe page names its own module and
    also its dependencies, and taking the first match made a
    dependency-only page look like a namespace disagreement. The caller
    selects the coordinate matching the module it already resolved.
    """
    return {
        f"{group}:{artifact}": version
        for group, artifact, version in _MODULE_COORD_RE.findall(page)
    }


def extract_declared_module(page: str) -> tuple[str, str] | None:
    """Deprecated single-coordinate form. Prefer `extract_declared_modules`.

    Kept because it is the shape a caller reading one page by hand
    expects, and it is honest about not knowing WHICH of the page's
    coordinates is the recipe's own.
    """
    modules = extract_declared_modules(page)
    if not modules:
        return None
    module_id, version = sorted(modules.items())[0]
    return module_id, version


def extract_display_name(page: str) -> str | None:
    match = _H1_RE.search(page)
    if not match:
        return None
    text = html_module.unescape(_TAG_RE.sub("", match.group(1))).strip()
    return text or None


def extract_description(page: str, *, limit: int = _MAX_DESCRIPTION_CHARS) -> str | None:
    lines = _html_to_lines(_content_region(page))
    for line in lines:
        if _JAVA_FQCN_RE.match(line) or len(line) < 30:
            continue
        return line[:limit]
    return None


def extract_tags(page: str) -> tuple[str, ...]:
    return tuple(sorted({match.group(1).lower() for match in _TAG_LINK_RE.finditer(page)}))


def extract_test_evidence(page: str) -> dict[str, tuple[str, ...]]:
    """Test method / class names the page lists.

    EVIDENCE THAT A TEST EXISTS, NOTHING MORE. The page is rendered
    documentation; reading `Quarkus1to113MigrationTest#foo` off it
    proves the recipe has a named test, and proves nothing about whether
    that test passes, was ever run, or still compiles. The key name
    says so.
    """
    methods = tuple(sorted({f"{cls}#{name}" for cls, name in _TEST_METHOD_RE.findall(page)}))
    classes = tuple(sorted(set(_TEST_CLASS_RE.findall(page))))
    return {"test_methods": methods, "test_classes": classes}


def parse_sitemap(sitemap: str) -> tuple[str, ...]:
    """Every `<loc>` in a sitemap, in document order, deduplicated."""
    seen: list[str] = []
    for match in _LOC_RE.finditer(sitemap or ""):
        url = match.group(1)
        if url not in seen:
            seen.append(url)
    return tuple(seen)


def is_namespace_index(url: str) -> bool:
    """True for a `/recipes/<namespace>` landing page.

    The sitemap carries 44 of these. They are namespace index pages, not
    recipes, and they have no package to attribute a module from, so they
    are counted separately rather than lumped in with non-recipe URLs --
    the difference is 44 pages, which is not nothing when the number of
    recipe pages is the number anyone will quote.
    """
    prefix = f"{DOCS_ORIGIN}/recipes/"
    if not url.startswith(prefix):
        return False
    tail = url[len(prefix) :].split("?", 1)[0].split("#", 1)[0].strip("/")
    return bool(tail) and "/" not in tail


def recipe_namespace(url: str) -> str | None:
    """`quarkus/updates` from a recipe doc URL, or None if not one."""
    prefix = f"{DOCS_ORIGIN}/recipes/"
    if not url.startswith(prefix):
        return None
    tail = url[len(prefix) :].split("?", 1)[0].split("#", 1)[0].strip("/")
    if not tail or "/" not in tail:
        return None
    return "/".join(tail.split("/")[:-1])


# httpx GET returning (status_code, text). Injected in tests; the default
# lazily imports httpx so a missing install surfaces only when a real
# network fetch is attempted.
HttpGet = Callable[[str], "tuple[int, str]"]


def _default_http_get(url: str) -> tuple[int, str]:
    import httpx

    from app.services.screening import assert_safe_locator

    assert_safe_locator(url)
    response = httpx.get(url, timeout=30, follow_redirects=True)
    return response.status_code, response.text


def _composed_document(
    *,
    recipe_id: str,
    display_name: str | None,
    description: str | None,
    tags: Sequence[str],
    module: ModuleGate,
    doc_url: str,
    check: Mapping[str, Any],
    test_evidence: Mapping[str, Sequence[str]],
) -> str:
    lines = [
        "---",
        f"name: {recipe_id.rsplit('.', 1)[-1]}",
        f"description: {description or display_name or recipe_id}",
        "---",
        "",
        f"# {display_name or recipe_id}",
        "",
        f"OpenRewrite recipe `{recipe_id}`, published by the module "
        f"`{module.module_id}` (version {module.version}, license {module.spdx}).",
        "",
        "## What it changes",
        "",
        description or "No description is rendered on the documentation page.",
        "",
        "## When to apply",
        "",
        f"1. Module: `{module.module_id}`, declared Apache-2.0 and not archived.",
        f"2. Tags: {', '.join(tags) or 'none rendered on the page'}.",
        f"3. Documentation: {doc_url}",
        "",
        "## How to run it",
        "",
        "This procedure is a source of the recipe's identity and provenance. It is "
        "NOT an executable procedure: running it requires JDK 21, Gradle, network "
        "dependency resolution and a Code Genome Project token, none of which this "
        "substrate provides.",
        "",
        "## Check (static only)",
        "",
        f"1. Declaration parsed and id well formed: `{check.get('gates', {}).get('declaration_parsed', 'unknown')}`.",
        f"2. Module Apache-2.0: `{check.get('gates', {}).get('module_apache2', 'unknown')}`; "
        f"not archived: `{check.get('gates', {}).get('module_not_archived', 'unknown')}`.",
        f"3. Test evidence: {len(test_evidence.get('test_methods', ()))} named test method(s) "
        f"and {len(test_evidence.get('test_classes', ()))} test class name(s) appear on the "
        "documentation page. This is evidence a test EXISTS. No assertion was executed.",
        f"4. Claim: {check.get('claim', 'no claim recorded')}",
    ]
    return "\n".join(lines) + "\n"


class OpenRewriteCatalogSource:
    """Every recipe documentation page whose module is Apache-2.0 and live.

    The network layer is a constructor argument, not a module global, so
    a test can enumerate and fetch a synthetic catalog without a socket
    and without a monkeypatch. Nothing here touches the network in
    `__init__`.
    """

    source_type = "openrewrite"

    def __init__(
        self,
        *,
        fetcher: HttpGet | None = None,
        sitemap_url: str = SITEMAP_URL,
        max_pages: int | None = None,
    ) -> None:
        self._fetch = fetcher or _default_http_get
        self._sitemap_url = sitemap_url
        self._max_pages = max_pages
        # Rejection counts observed during the most recent `discover()`.
        # A property of the enumeration, not of any one artifact, so it
        # lives on the adapter and the CLI reads it after iterating.
        self.discovery_stats: dict[str, int] = {}

    def _fetch_text(self, url: str) -> str:
        status, body = self._fetch(url)
        if status != 200:
            raise OpenRewritePageUnavailable(f"fetch {url} returned {status}")
        return body

    def discover(self) -> Iterator[SourceRef]:
        """Enumerate the recipe doc pages, gating on the module first.

        Gating before the page fetch is not an optimisation with a
        correctness cost: the module attribution is derived from the URL
        namespace either way, and a page whose namespace resolves to a
        refused module would be refused on its content too. It turns a
        ~4,500-page crawl into a crawl of the Apache-2.0 subset, which
        is the difference between this being runnable and not.
        """
        self.discovery_stats = {"pages_seen": 0, "pages_offered": 0}
        urls = parse_sitemap(self._fetch_text(self._sitemap_url))
        offered = 0
        for url in urls:
            namespace = recipe_namespace(url)
            if namespace is None:
                bucket = "namespace_index_page" if is_namespace_index(url) else "not_a_recipe_page"
                self.discovery_stats[bucket] = self.discovery_stats.get(bucket, 0) + 1
                continue
            self.discovery_stats["pages_seen"] += 1
            gate = gate_namespace(namespace)
            if not gate.allowed:
                key = f"rejected:{gate.reason_code}"
                self.discovery_stats[key] = self.discovery_stats.get(key, 0) + 1
                continue
            if self._max_pages is not None and offered >= self._max_pages:
                self.discovery_stats["truncated_at_max_pages"] = self._max_pages
                break
            offered += 1
            yield SourceRef(
                uri=url,
                repository=gate.repo,
                path=namespace,
                source_id=gate.module_id,
            )
        self.discovery_stats["pages_offered"] = offered

    def fetch(self, ref: SourceRef) -> SourceArtifact:
        page = self._fetch_text(ref.uri)
        recipe_id = extract_recipe_id(page)
        if not _WELL_FORMED_ID_RE.match(recipe_id):
            raise OpenRewriteIdUnavailable(
                f"recipe id {recipe_id!r} read off {ref.uri} is not well formed"
            )

        module_id = resolve_namespace_module(ref.path or "") or ""
        gate = gate_module(module_id)
        if not gate.allowed:
            # Belt and braces: the namespace gate already refused these,
            # so reaching here means the table and the URL disagreed.
            raise RuntimeError(f"namespace gate admitted {ref.uri} but module {module_id} is refused")

        # The page's own "activate this recipe" instructions name module
        # coordinates. ALL of them are collected -- the page also names
        # its dependencies -- and only the coordinate matching the
        # resolved module is treated as this recipe's own. A page that
        # names exactly one module and it is not ours is a real
        # disagreement and is refused; a page that names several and
        # none is ours is recorded as unconfirmed rather than guessed.
        declared_modules = extract_declared_modules(page)
        declared_version = declared_modules.get(module_id)
        if module_id in declared_modules:
            declared_module = module_id
        elif len(declared_modules) == 1:
            only = sorted(declared_modules)[0]
            raise RuntimeError(
                f"page {ref.uri} names only module {only}, but its URL namespace "
                f"resolves to {module_id}; refusing to guess which is right"
            )
        elif declared_modules:
            declared_module = None
        else:
            declared_module = None
        test_evidence = extract_test_evidence(page)
        gates = {
            "declaration_parsed": "passed",
            "recipe_id_wellformed": "passed",
            "module_apache2": "passed",
            "module_not_archived": "passed",
            "test_evidence_only": "recorded_not_executed",
        }
        outcome = CheckOutcome(
            passed=True,
            tier="static",
            semantics=CHECK_SEMANTICS,
            case_count=0,
            negative_case_count=0,
            failures=(),
            gates=gates,
            detail={
                "runner_version": STATIC_RUNNER_VERSION,
                "recipe_id": recipe_id,
                "module": gate.as_dict(),
                "test_evidence": {key: list(value) for key, value in test_evidence.items()},
                "executed_assertion": False,
            },
        )
        check = build_check_payload(
            outcome=outcome,
            # No fixture tree exists for this source; an empty inventory is
            # the honest value and the payload says so rather than
            # inventing a case count.
            inventory=FixtureInventory(layout="none", case_count=0, negative_case_count=0),
            recipe_id=recipe_id,
            runner_version=STATIC_RUNNER_VERSION,
        )

        display_name = extract_display_name(page)
        description = extract_description(page)
        tags = extract_tags(page)
        content = _composed_document(
            recipe_id=recipe_id,
            display_name=display_name,
            description=description,
            tags=tags,
            module=gate,
            doc_url=ref.uri,
            check=check,
            test_evidence=test_evidence,
        )
        license_metadata: dict[str, Any] = {
            "repo_url": f"https://github.com/{gate.repo}" if gate.repo else None,
            "commit": None,
            "path": ref.path,
            "recipe_id": recipe_id,
            "maven_group_id": module_id.split(":", 1)[0] if module_id else None,
            "maven_artifact_id": module_id.split(":", 1)[1] if ":" in module_id else None,
            "maven_version": declared_version or gate.version,
            "maven_version_from_table": gate.version,
            "maven_version_declared_on_page": declared_version,
            "module_declared_on_page": declared_module,
            "modules_named_on_page": sorted(declared_modules),
            "module": gate.module_id,
            "module_spdx": gate.spdx,
            "module_license_source": LICENSE_TABLE_SOURCE,
            "module_license_snapshot": LICENSE_TABLE_SNAPSHOT,
            "module_repo_archived": gate.archived,
            "source_availability_tier": gate.license_tier,
            "declared_license": gate.spdx,
            "detected_spdx": gate.spdx,
            "license_verdict": {
                "decision": "ALLOW" if gate.allowed else "REJECT",
                "reason": gate.reason,
                "spdx_id": gate.spdx,
                "source_path": LICENSE_TABLE_SOURCE,
                "allowlist_version": f"openrewrite-module-table@{LICENSE_TABLE_SNAPSHOT}",
            },
            "allowlist_version": f"openrewrite-module-table@{LICENSE_TABLE_SNAPSHOT}",
            "doc_url": ref.uri,
            "display_name": display_name,
            "description": description,
            "tags": list(tags),
            "test_classes": list(test_evidence.get("test_classes", ())),
            "test_methods": list(test_evidence.get("test_methods", ())),
            "test_evidence_meaning": (
                "test method names read off the documentation page: evidence that a "
                "test exists, never an executed assertion"
            ),
            "check_tier": "static",
            "check": check,
            "provenance_limits": [
                "the module license is read from the documentation table, not from the "
                "repository's own LICENSE file; a pointer-file root (the Moderne shape) "
                "would not be detectable from the repo at all",
                "no repository commit was fetched, so the license verdict is a statement "
                "about the table snapshot date and not about a pinned tree",
                "the exact leaf-recipe count for the Apache-2.0 subset is UNVERIFIED; a "
                "documentation page is a good proxy for a recipe and is not identical to one",
            ] + (
                []
                if declared_module
                else [
                    "this page does not name the module its URL namespace resolved to, "
                    f"so the module is attributed from the namespace alone; it names "
                    f"{len(declared_modules) or 'no'} module coordinate(s) in total"
                ]
            ) + (
                []
                if (declared_version is None or declared_version == gate.version)
                else [
                    f"the page names module version {declared_version} while the "
                    f"license table records {gate.version}; the table snapshot is "
                    f"{LICENSE_TABLE_SNAPSHOT} and this version is the newer of the two"
                ]
            ),
            "extractor": "openrewrite_static@1",
        }
        return SourceArtifact(
            source_type=self.source_type,
            uri=ref.uri,
            content=content,
            content_hash=compute_content_hash(content),
            repository=gate.repo,
            path=ref.path,
            commit=None,
            license_metadata=license_metadata,
        )

    def fingerprint(self, artifact: SourceArtifact) -> str:
        return artifact.content_hash
