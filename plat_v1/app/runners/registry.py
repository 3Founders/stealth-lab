"""
The explicit registry of Python implementations.

This dict is the entire allowlist. `python_fn.py` resolves a database `ref`
against it and nothing else -- there is no importlib path, no entry-point
scanning, no dotted-name resolution. Registering an implementation is a code
change that goes through review, which is the property worth having.
"""
from __future__ import annotations

from typing import Any, Callable

from app.runners import tables
from app.runners.base import RunContext

PythonImplementation = Callable[[dict[str, Any], RunContext], dict[str, Any]]

REGISTRY: dict[str, PythonImplementation] = {
    "tables:classify_document": tables.classify_document,
    "tables:detect_table_regions": tables.detect_table_regions,
    "tables:extract_cell_structure": tables.extract_cell_structure,
    "tables:validate_types": tables.validate_types,
    "tables:map_to_schema_template": tables.map_to_schema_template,
    # Postprocess half of the model implementation of map_to_schema.
    "tables:apply_column_mapping": tables.apply_column_mapping,
    # Free replay of a mapping a previous run paid a model to work out.
    "tables:apply_cached_mapping": tables.apply_cached_mapping,
    "tables:write_xlsx": tables.write_xlsx,
}
