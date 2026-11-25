# Working in this repository as a coding agent

This file is for coding agents (and people in a hurry). It says how to run things, what the rules are, and where the traps are. The longer documents are the source of truth: `README.md` for installing and running the pipeline, `CONTRIBUTING.md` for adding a state (its config schema, the checks a config must pass, and the definition of done), and `DataPolicy.md` for what may be done with the data.

## What this is

A pipeline that turns state campaign-finance filings into one linked database: `cft collect` downloads a state's raw files, `cft standardize` maps them to one schema through a per-state YAML config, `cft normalize` splits them into the tables in `src/utils/table.yaml`, `cft clean` fills gaps and fixes types, `cft link` finds the same person or organization across records, and `cft classify` will label entities (it is a stub today). Five states are supported: Arizona, Michigan, Minnesota, Pennsylvania, Texas. Adding a state means writing a collector and a config, not pipeline code.

## Setup and commands

- Install with `uv sync` from the repository root. Run everything through `uv run`: `uv run cft <step> --help`, `uv run pytest -q`, `uv run pre-commit run --all-files` (ruff lint and format, the same lint CI runs). CI also runs `pytest --doctest-modules` under `coverage` and fails below 55% line coverage of `src/`, so a large module with no tests, such as a new collector, can fail CI while a local `pytest -q` is green. Python comes from `.python-version` (3.11); CI also runs 3.10 and 3.14.
- Dependencies are pinned in `uv.lock`. Change them with `uv add` or `uv remove`, never by editing the lock, and commit `pyproject.toml` and `uv.lock` together.
- Data lives under `DATA_DIR` (default `data/`, which is gitignored): `raw/<state>/`, `standardized/<state>/`, `normalized/<state>/`, `cleaned/<state>/`, `linked/`. Pass `--data-directory` to work somewhere else. Never commit real data, and treat any shared data folder you are pointed at as read-only unless told otherwise.
- `local-notes/` is gitignored scratch space for review notes and plans.

## Where things are

- `src/utils/collect/finance/<state name>.py`: one module per state, registered with `@register_special_state_collector("<code>")`. Every module in the folder is imported automatically.
- `src/utils/standardize/finance/config/<code>.yaml`: the state's config. `src/utils/standardize/config.py` loads and checks it; `source.py` applies it.
- `src/utils/table.yaml`: the schema every standard column name must resolve against, including the allowed values of enum columns.
- `tests/data/standardize/raw/<code>/`: synthetic fixtures, three rows per file, written by the `make_*_fixtures.py` scripts in `tests/data/standardize/`. `tests/test_state_configs.py` loads every form of every config and checks each config has fixtures; `tests/test_state_golden_rows.py` checks who paid whom, the amount, the date and the type for each form.

## Rules

1. **Config before code.** A new state should need only a collector module and a config. If the config cannot express something the state does, open an issue describing the state's data and the feature that would cover it, and leave the gap documented in the config, rather than adding Python for one state or changing shared code (`source.py`, normalize, clean, `table.yaml`) to suit it. For now, do not use the Python-backed form that CONTRIBUTING's "Special Cases" describes; the point of the experiment is to find what the config lacks. A new enum value in `table.yaml` needs a reason in the PR.
2. **Let the checks tell you.** A config is validated when it loads: `uv run cft standardize --states <code> --data-directory <dir>` stops with a `ConfigError` that lists every problem in the first form that fails, with the file and form; later forms report on the next run. Read the whole list, fix the causes, run again until it loads. `uv run pytest -q tests/test_state_configs.py tests/test_state_golden_rows.py` runs the same checks plus the fixtures.
3. **Look at the real files before mapping them.** Which party filed each file, which column is the date of the transaction (not the filing), how amendments or superseded rows are marked, whether a row id is unique, how direction is signaled. Record what you found and the numbers behind it in comments in the config, the way `tx.yaml` and `pa.yaml` do. Guesses that look like facts are the most expensive mistake here.
4. **Fixtures and golden rows are part of the state, not an afterthought.** Copy real headers and value formats, invent the values. Every form needs a file; every form needs a golden row. A row count alone will not catch a swapped donor and recipient.
5. **Verify by running, then read the reports.** After standardize, read `standardized/<code>/reports/coerced_values.csv`: every row is a raw value that became null, and most mean a mapping is missing a value. Read the run's warnings: a form that matched no file, a file missing a mapped column, a path that gave no year. Run normalize and clean on the fixtures too.
6. **Tests must be able to fail.** When you add a check or a mapping, add the test that fails without it. Prefer one small test per property, named for what it proves.
7. **Commits and pull requests say why.** One commit per problem, an imperative title, a body that explains the cause and what was checked. The PR description follows the pattern of recent merged PRs: what changed, what was verified by running (with numbers), and a "things to know" list of consequences and open decisions. Say plainly what was not run.
8. **Do not weaken a check to get green.** If a test or a config check rejects your change, the usual reason is that the change is wrong or the fixture is. Ask or open an issue before loosening either.

## Traps

- By default a form reads columns by position and each file's header is checked against `column_order`, so a column added or moved in a later year stops the run on purpose. With `include_column_order: False` columns are read by name and the header is not checked; a file that lacks a mapped column gets a warning instead.
- A block that exists only to be inherited from needs `meta: True`, or the load fails for want of `table_name`; a form that describes real files must not be `meta`, or its files are silently skipped. A form must end up with `table_name`, `state_code` and `path_pattern`, inherited or its own. Only `column_details` merge under inheritance; every other key a child sets replaces the parent's whole.
- `enum_mapper` and `null_values` entries must name columns the form produces; an entry for a column the form does not have is rejected because it would never apply.
- `table_name` is `Transaction` or `Transactor`, with that capitalization. State codes are lowercase everywhere.
- `year_filter_filepath_regex` is matched against the path inside the state's folder, so `^(\d{4})/` is the usual form, and it must capture the year.
- `transaction_direction` is for files that list money both ways; map the columns as for money paid to the filer and mark the reversed rows `reverse`. Every Transaction form sets `reported_by`; the checks do not require it, so the state's golden-row test is where it is pinned.
- Map `source_identifier` only when the state's row id is unique per row; several states' ids repeat.
- A special (Python) pipeline module must be named exactly `<code>.py` under `src/utils/standardize/finance/`; any other name is silently never imported.

## Reporting your work

When you finish a state, or stop, write down: what was added (collector, config forms, fixtures, golden rows); the commands you ran and what they printed (test counts, the `coerced_values.csv` summary, warnings); what in the state's data you could not map and why, with a link to the issue you opened; and the figures you compared against something the state publishes. If you could not finish a step, say which and why, rather than leaving it half done.
