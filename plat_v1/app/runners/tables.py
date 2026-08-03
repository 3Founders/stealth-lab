"""
Deterministic implementations for the PDF -> Excel reference workflow.

Six stages at 97% accuracy each is 83% end to end. The chain only holds
because most stages are exact, so every stage here is written to be
deterministic first and a model implementation is added only where the work
genuinely needs reasoning. Stages 4 and 6 have no model implementation at
all, and that is the design, not a gap to fill later.

Each function takes `(inputs, ctx)` and returns a dict of outputs, matching
the contract in `python_fn.py`. Raising is how a stage says "escalate" -- the
executor records the failure and moves to the next implementation in cost
order.

pdfplumber and openpyxl are imported inside the functions: the offline test
suite exercises the pure logic (type inference, template matching) and must
not require a PDF stack to be installed.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.runners.base import RunContext, RunnerError

# Characters of extractable text per page below which a PDF is treated as
# scanned. A born-digital page of tabular text runs into the thousands; a
# scanned page yields whatever OCR artefacts the producer left behind, which
# is typically zero or a stray header.
SCANNED_DENSITY_THRESHOLD = 120


# ---------------------------------------------------------------------------
# 1. classify_document
# ---------------------------------------------------------------------------


def classify_document(inputs: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    import pdfplumber

    path = _require_path(inputs, "pdf_path")
    total_chars = 0
    with pdfplumber.open(path) as pdf:
        page_count = len(pdf.pages)
        for page in pdf.pages:
            total_chars += len(page.extract_text() or "")

    if page_count == 0:
        raise RunnerError(f"{path} has no pages")

    density = total_chars / page_count
    return {
        "doc_type": "digital_table" if density >= SCANNED_DENSITY_THRESHOLD else "scanned",
        "page_count": page_count,
        "text_density": round(density, 2),
    }


# ---------------------------------------------------------------------------
# 2. detect_table_regions
# ---------------------------------------------------------------------------


def detect_table_regions(inputs: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    """Ruled-line detection. Falls back to whitespace alignment on the same page."""
    import pdfplumber

    path = _require_path(inputs, "pdf_path")
    regions: list[dict[str, Any]] = []

    with pdfplumber.open(path) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            found = page.find_tables({"vertical_strategy": "lines",
                                      "horizontal_strategy": "lines"}) or []
            if not found:
                found = page.find_tables({"vertical_strategy": "text",
                                          "horizontal_strategy": "text"}) or []
            for table in found:
                regions.append({"page": index, "bbox": [float(v) for v in table.bbox]})

    if not regions:
        raise RunnerError(f"no table regions found in {path}")
    return {"regions": regions}


# ---------------------------------------------------------------------------
# 3. extract_cell_structure
# ---------------------------------------------------------------------------


def extract_cell_structure(inputs: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    import pdfplumber

    path = _require_path(inputs, "pdf_path")
    regions = inputs.get("regions") or []
    if not regions:
        raise RunnerError("extract_cell_structure was given no regions")

    header: list[str] = []
    grid: list[list[str]] = []

    with pdfplumber.open(path) as pdf:
        for region in regions:
            page_number = int(region.get("page", 1))
            if page_number < 1 or page_number > len(pdf.pages):
                raise RunnerError(f"region names page {page_number}, which does not exist")
            page = pdf.pages[page_number - 1]
            cropped = page.crop(tuple(region["bbox"])) if region.get("bbox") else page
            rows = cropped.extract_table() or []
            if not rows:
                continue

            cleaned = [[_clean(cell) for cell in row] for row in rows]
            if not header:
                # The first region's first row is the header. Subsequent
                # regions are continuations of the same table across pages,
                # so their repeated header row is dropped rather than
                # becoming a data row that fails type inference.
                header, cleaned = cleaned[0], cleaned[1:]
            elif cleaned and cleaned[0] == header:
                cleaned = cleaned[1:]
            grid.extend(cleaned)

    if not header:
        raise RunnerError("no header row could be extracted from any region")
    if not grid:
        raise RunnerError("regions produced a header but no data rows")

    return {"header": header, "grid": grid}


# ---------------------------------------------------------------------------
# 4. validate_types -- deterministic, no model implementation
# ---------------------------------------------------------------------------

_INT = re.compile(r"^-?\d+$")
_NUM = re.compile(r"^-?[\d,]*\.?\d+$")
_CURRENCY = re.compile(r"^[-(]?\s*[$£€]\s*[\d,]*\.?\d+\s*\)?$")
_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%b-%Y", "%b %d, %Y", "%d %B %Y")


def _parse_number(text: str) -> float | int | None:
    stripped = text.strip()
    if _CURRENCY.match(stripped):
        negative = stripped.startswith("(") or stripped.startswith("-")
        digits = re.sub(r"[^\d.]", "", stripped)
        if not digits:
            return None
        value = float(digits)
        return -value if negative else value
    cleaned = stripped.replace(",", "")
    if _INT.match(cleaned):
        return int(cleaned)
    if _NUM.match(cleaned):
        return float(cleaned)
    return None


def _parse_date(text: str) -> date | None:
    stripped = text.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(stripped, fmt).date()
        except ValueError:
            continue
    return None


def _cell_type(text: str) -> str:
    if not text or not text.strip():
        return "empty"
    number = _parse_number(text)
    if number is not None:
        return "integer" if isinstance(number, int) else "number"
    if _parse_date(text) is not None:
        return "date"
    return "string"


# Widening lattice: a column takes the narrowest type every one of its
# non-empty values satisfies. Inferring per column rather than per cell is
# what makes coercion total -- there is no "this cell wouldn't convert"
# failure mode, because the column type was chosen to fit every cell.
_WIDER = {"integer": ("integer", "number", "string"),
          "number": ("number", "string"),
          "date": ("date", "string"),
          "string": ("string",)}


def _column_type(values: list[str]) -> str:
    observed = {_cell_type(v) for v in values} - {"empty"}
    if not observed:
        return "string"
    for candidate in ("integer", "number", "date", "string"):
        if all(candidate in _WIDER[o] for o in observed):
            return candidate
    return "string"


def _coerce(text: str, column_type: str) -> Any:
    if not text or not text.strip():
        return None
    if column_type in ("integer", "number"):
        return _parse_number(text)
    if column_type == "date":
        parsed = _parse_date(text)
        return parsed.isoformat() if parsed else None
    return text.strip()


def validate_types(inputs: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    header = list(inputs.get("header") or [])
    grid = inputs.get("grid") or []
    if not header:
        raise RunnerError("validate_types was given no header")

    width = len(header)
    errors: list[str] = []

    # Ragged rows are the one genuine validation failure here. Reported and
    # excluded rather than padded: a row with the wrong number of cells means
    # the extraction misread the table, and silently padding it would carry
    # that misreading into the mapping stage as plausible-looking data.
    rectangular: list[list[str]] = []
    for index, row in enumerate(grid):
        if len(row) != width:
            errors.append(
                f"row {index + 1} has {len(row)} cells, header declares {width}"
            )
            continue
        rectangular.append([_clean(c) for c in row])

    columns = []
    for position, name in enumerate(header):
        values = [row[position] for row in rectangular]
        columns.append({"name": name or f"column_{position + 1}", "type": _column_type(values)})

    typed_grid = [
        [_coerce(row[position], columns[position]["type"]) for position in range(width)]
        for row in rectangular
    ]

    return {"typed_grid": typed_grid, "columns": columns, "errors": errors}


# ---------------------------------------------------------------------------
# 5. map_to_schema -- python template match; the model implementation is the
#    fallback, and it is the one stage that genuinely needs reasoning
# ---------------------------------------------------------------------------


def _normalise(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")


def _target_fields(target_schema: Any) -> tuple[list[str], set[str]]:
    """Accepts a JSON Schema object or a bare list of field names."""
    if isinstance(target_schema, list):
        return [str(f) for f in target_schema], {str(f) for f in target_schema}
    props = (target_schema or {}).get("properties") or {}
    fields = list(props.keys())
    required = set((target_schema or {}).get("required") or fields)
    return fields, required


def apply_column_mapping(inputs: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    """
    Turn a model-produced column mapping into rows.

    The postprocess half of the model implementation of map_to_schema. The
    model decides which source column is which target field -- genuinely a
    judgement call on an unfamiliar layout -- and this moves the values,
    which is not. Splitting it that way also sidesteps the fact that
    structured outputs cannot describe an object whose keys the caller chose.
    """
    mapping = inputs.get("mapping") or []
    typed_grid = inputs.get("typed_grid") or []
    columns = inputs.get("columns") or []
    _, required = _target_fields(inputs.get("target_schema"))

    resolved: dict[str, int] = {}
    for entry in mapping:
        field_name = str(entry.get("target_field") or "").strip()
        index = entry.get("source_column")
        if not field_name or index is None:
            continue
        index = int(index)
        if not 0 <= index < len(columns):
            raise RunnerError(
                f"mapping points '{field_name}' at column {index}, but the grid has "
                f"{len(columns)} columns"
            )
        resolved[field_name] = index

    missing = sorted(required - set(resolved))
    if missing:
        raise RunnerError(f"mapping omits required field(s): {', '.join(missing)}")

    rows = [
        {name: row[index] for name, index in resolved.items()}
        for row in typed_grid
        if len(row) == len(columns)
    ]
    return {"rows": rows}


def apply_cached_mapping(inputs: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    """
    Replay the mapping a previous run worked out for this layout.

    This is where the cache actually collapses marginal cost. The first
    document of a given layout pays for a model call to decide which column
    is which; the mapping it produced is stored as the cache entry's params,
    and every later document with the same layout replays it for free. Two
    invoices from the same vendor cost one model call between them, not two,
    which only works because the fingerprint is a layout and not a hash of
    the content.

    Raises when there are no cached params -- that is the first-run path, and
    a fast failure here costs microseconds before the router escalates.
    """
    mapping = ctx.params.get("mapping")
    if not mapping:
        raise RunnerError("no cached mapping for this layout")
    return apply_column_mapping({**inputs, "mapping": mapping}, ctx)


def map_to_schema_template(inputs: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    """
    Column-name template match.

    Cheap and exact when the layout is one we have seen -- which, on a cache
    hit, it is by definition. Raises when it cannot match every required
    target field, so the router escalates to the model implementation rather
    than emitting rows with holes in them.
    """
    columns = inputs.get("columns") or []
    typed_grid = inputs.get("typed_grid") or []
    fields, required = _target_fields(inputs.get("target_schema"))
    if not fields:
        raise RunnerError("map_to_schema was given no target schema")

    by_normalised = {_normalise(c["name"]): index for index, c in enumerate(columns)}
    mapping: dict[str, int] = {}
    for field_name in fields:
        index = by_normalised.get(_normalise(field_name))
        if index is not None:
            mapping[field_name] = index

    missing = sorted(required - set(mapping))
    if missing:
        raise RunnerError(
            f"template match could not place required field(s) {', '.join(missing)}; "
            f"columns present: {', '.join(c['name'] for c in columns)}"
        )

    rows = [
        {name: row[index] for name, index in mapping.items()}
        for row in typed_grid
        if len(row) == len(columns)
    ]
    return {"rows": rows}


# ---------------------------------------------------------------------------
# 6. write_xlsx -- deterministic, no model implementation
# ---------------------------------------------------------------------------


def write_xlsx(inputs: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    from openpyxl import Workbook

    rows = inputs.get("rows") or []
    if not rows:
        raise RunnerError("write_xlsx was given no rows")

    # Column order from the first row, then any field later rows introduce.
    # Deterministic, and stable across runs of the same data -- a spreadsheet
    # whose columns reorder between runs is not diffable.
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "extracted"
    sheet.append(columns)
    for row in rows:
        sheet.append([row.get(name) for name in columns])

    destination = Path(ctx.workdir) / f"{ctx.node_ref or 'output'}.xlsx"
    destination.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(destination)

    return {"path": str(destination.resolve()), "row_count": len(rows)}


# ---------------------------------------------------------------------------


def _clean(cell: Any) -> str:
    if cell is None:
        return ""
    # pdfplumber emits embedded newlines for wrapped cell text.
    return re.sub(r"\s+", " ", str(cell)).strip()


def _require_path(inputs: dict[str, Any], key: str) -> str:
    path = inputs.get(key)
    if not path:
        raise RunnerError(f"missing required input '{key}'")
    if not Path(str(path)).exists():
        raise RunnerError(f"{key} points at {path}, which does not exist")
    return str(path)
