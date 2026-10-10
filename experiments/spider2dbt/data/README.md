# Spider 2.0-DBT data (WIP -- nothing downloaded here yet)

Source: https://github.com/xlang-ai/Spider2, folder `spider2-dbt/` (license: not yet checked -- check the repo's
LICENSE before redistributing anything).

- `spider2-dbt/examples/spider2-dbt.jsonl` (16 KB): 68 task instructions, one per example project.
- `spider2-dbt/examples/<id>/` : the dbt projects (19,241 files / 204 MB with `dbt_packages`; 2,226 files / 49 MB
  without). The DuckDB start databases are NOT in git.
- `spider2-dbt/evaluation_suite/gold/spider2_eval.jsonl` (19 KB): per task `duckdb_match` on named tables/columns.
- `spider2-dbt/evaluation_suite/{evaluate.py,eval_utils.py}`: the official grader.
- Databases (Google Drive, via gdown; sizes not yet measured):
  - start DBs `DBT_start_db.zip`: https://drive.google.com/uc?id=1N3f7BSWC4foj-V-1C9n8M2XmgV7FOcqL
  - gold DBs `dbt_gold.zip`: https://drive.google.com/uc?id=1s0USV_iQLo4oe05QqAMnhGGp5jeejCzp
  - `spider2-dbt/setup.py` moves each `*.duckdb` from the zips into `examples/<id>/` and `evaluation_suite/gold/<id>/`.
- Runtime: the official Docker image is `python:3.11` + unpinned `dbt-duckdb`; locally a Python 3.11 venv with
  `dbt-duckdb` installs on Windows without Docker (dbt-core 1.12.5, dbt-duckdb 1.11.0); no project built yet.

## Series candidates (same database, several tasks)

The dataset has one task per example, but several examples share a project/database (family by name prefix):
f1001-003, quickbooks001-003, xero001 / xero_new001-002, shopify001-002, tickit001-002, airbnb001-002,
app_reporting001-002, google_play001-002, recharge001-002, workday001-002, mrr001-002, playbook001-002,
tpch001-002. Next step: diff each family's projects (they likely differ only in which models are removed) and
decide between running them in order in one workspace or building a union project with all target models removed.
