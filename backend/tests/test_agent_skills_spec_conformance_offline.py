"""Representative parser conformance fixtures for the Agent Skills format."""
from app.services.skill_ingestion import parse_skill_md


def test_spec_style_frontmatter_fields_and_unknown_metadata_round_trip():
    parsed = parse_skill_md("""---
name: pdf-processing
description: Extract and validate content from PDF documents.
license: Apache-2.0
compatibility: Requires poppler utilities.
allowed-tools: "pdftotext, pdfinfo"
metadata:
  author: example
  version: 1
custom:
  deployment: local
---

## Workflow
### Step 1: Inspect the document
Read metadata before extraction.
### Step 2: Extract and validate
Check the extracted content against the source.
""")

    assert parsed.name == "pdf-processing"
    assert parsed.license == "Apache-2.0"
    assert parsed.compatibility == "Requires poppler utilities."
    assert parsed.allowed_tools == ["pdftotext", "pdfinfo"]
    assert parsed.metadata["version"] == 1
    assert parsed.frontmatter["custom"] == {"deployment": "local"}
    assert parsed.steps == ["Inspect the document", "Extract and validate"]


def test_spec_style_document_without_frontmatter_remains_parseable():
    parsed = parse_skill_md("""# A useful procedure

Use this when the repository provides no metadata block.

1. Gather the relevant evidence.
2. Verify the result.
""", fallback_name="useful-procedure")
    assert parsed.name == "useful-procedure"
    assert parsed.steps == ["Gather the relevant evidence.", "Verify the result."]
