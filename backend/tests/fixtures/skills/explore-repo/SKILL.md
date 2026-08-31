---
name: explore-unfamiliar-repository
description: Systematically explore an unfamiliar code repository to build a working mental model before making changes.
---

Use this when working in a repository you have not touched before, or one where you do not yet know where a given feature lives.

1. Read the top-level README and any CONTRIBUTING/ARCHITECTURE docs first.
2. List the top-level directory structure and identify the primary language/framework.
3. Locate the entrypoint (main module, server startup file, or CLI dispatcher).
4. Search for the feature or symbol you actually need, starting from the entrypoint outward.
5. Run the existing test suite once, unmodified, to confirm the baseline passes before changing anything.
