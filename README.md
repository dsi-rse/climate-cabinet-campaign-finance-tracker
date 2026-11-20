# Climate Cabinet Campaign Finance Tracker

This package provides tools for collecting and processing state campaign finance data. Currently the package supports Arizona, Michigan, Minnesota, Pennsylvania, and Texas. To request another state, please open an issue. To add another state yourself, see [Adding a new state](./CONTRIBUTING.md#adding-a-new-state).

This package was developed at the Data Science Institute at the University of Chicago in partnership with Climate Cabinet. 

## Installation

### Docker (recommended)

For the most consistent installation, the pipeline components can be run using Docker. To install Docker, visit [the docker website](https://docs.docker.com/get-started/get-docker/) and follow the directions to get started. 

### Local

Local installs use [uv](https://docs.astral.sh/uv/getting-started/installation/). From the repository root:
```bash
uv sync
```
This creates a `.venv` with the Python version in `.python-version` and the exact package versions in `uv.lock`, and installs this package in editable mode. Run commands inside it with `uv run` (for example `uv run cft --help` or `uv run pytest`), or activate it with `source .venv/bin/activate`. To use `--slurm`, install the optional dependency with `uv sync --extra slurm`.

### Changing dependencies

Dependencies are declared in `pyproject.toml` and pinned in `uv.lock`. Use `uv add <package>` (or `uv add --dev <package>` for development tools) and `uv remove <package>` rather than editing the lists by hand, and commit the updated `uv.lock` together with `pyproject.toml`. CI installs with `uv sync --locked`, which fails if the lock is out of date. To upgrade pinned versions, run `uv lock --upgrade` and check that the tests still pass.

## Use

### Docker / Make

If you have set up with Docker, the quickest way to get started is to use make to interact with Docker. You can run one of the following commands:

```bash
make run-collect-pipeline
make run-standardize-pipeline
make run-normalize-pipeline
make run-clean-pipeline
make run-link-pipeline
make run-classify-pipeline
```
Each runs one step of the pipeline with default options. The normalize and clean steps use `--chunk-size 2000`.

If you would like to connect a jupyter lab to the docker container and use python notebooks in your browser:

```bash
make run-notebooks
```

If you would like to run the fully configurable [Command Line Interface](#local--command-line-interface), please run `make run-interactive` to connect your terminal to the docker container and use the commands below. To exit, type `exit`

### Local / Command Line Interface

If you have completed a local installation, run `cft` with `uv run cft`, or as plain `cft` after activating the environment with `source .venv/bin/activate`. This way will provide you with the most configuration options. 

```bash
cft collect
cft standardize
cft normalize
cft clean
cft link
cft classify
```
To see the full list of options for each command, add a `--help` argument to any of the above commands. To see all options, see [Command Line Options](#command-line-options). 

## Configuration

### DATA_DIR
By default, each pipeline step reads and saves data in a set structure under the data `DATA_DIR`. This `DATA_DIR` is an environment variable that can be set by adding a `.env` file to the repository root and setting `DATA_DIR=X` replacing `X` with the absolute path to your preferred directory. If unset, it will default to the `data` directory in the repository root. If `data-directory`, `input-directory`, or `output-directory` options are set for any step, they will ignore `DATA_DIR`. 

### Command Line Options
These options are shared across pipeline steps. To see per-step details and available options, run `cft <step> --help`, replacing `<step>` with your desired step.

#### --states
List of states on which to run the given pipeline step. The pipeline steps except link will process each state separately.

#### --chunk-size
Maximum number of rows to process at once. If left blank, all rows for a given state and step will be processed at once. This may not work if your computer has limited memory / RAM.

#### --start-year
Earliest year (in YYYY format) on which to process data. If none is given, the earliest available year will be included. 

#### --end-year
Latest year (in YYYY format) on which to process data. If none is given, the latest available year will be included. 

#### --data-directory
Path to the main data directory. If `--input-directory` or `--output-directory` are not set, steps use their default subdirectories under this base directory. The default base directory comes from the `DATA_DIR` environment variable.

#### --input-directory
Path to the input directory for this step. Defaults to the step's expected input subdirectory under `--data-directory`. Setting this overrides `--data-directory`.

#### --output-directory
Path to the output directory for this step. Defaults to the step's output subdirectory under `--data-directory`. Setting this overrides `--data-directory`.

#### --format
Desired file format (`csv` or `parquet`). If separate input and output formats are desired, use `--input-format` and `--output-format` instead. Default is `parquet`.

#### --input-format
Input file format (`csv` or `parquet`). Default is `parquet`.

#### --output-format
Output file format (`csv` or `parquet`). Default is `parquet`.

#### --schema
Path to data schema. Default: `src/utils/table.yaml`.

#### --slurm
Run the pipeline on an HPC cluster using SLURM.


#### --database-path
Path to the DuckDB database that link loads the cleaned data into and records its runs in. Default: `campaign_finance.duckdb` in the output directory (`linked/`). (link step)

#### --model-path
Path to record linkage model. If training, this is the path to save the model. (link step)

#### --threshold
Match probability threshold to consider two records a match. Default: `0.95`. (link step)

#### --table-name
Table to perform record linkage on. Default: `transactor_detailed_view`. (link step)

#### --train
Train a record linkage model. (link step)

#### --overwrite
Drop every table in the database first, including the history of earlier linkage runs. Without it, only the source tables are reloaded from the cleaned data (for the states in `--states`) and the linkage history is kept. (link step)

#### --resume-from-checkpoint
When training, resume from the checkpoint saved after the first training session instead of starting over. The checkpoint is saved next to the model as `<model name>_checkpoint.json` (for example, `link_model_checkpoint.json` for `--model-path link_model.json`). If no checkpoint exists, training starts from scratch. (link step)


## Pipeline

The full pipeline is broken down into several steps:

1. Collect: Gather key states' political campaign finance report data which should include recipient information, donor information, and transaction information.
2. Standardize: Define database schema for storing transaction and entity information and standardize column names and values.
3. Normalize: Normalize data into provided schema
4. Clean: Use heuristics to fill in missing information and make data consistent. 
5. Link: Perform probabilistic record linkage on cleaned data to identify duplicate records.
6. Classify: Label all entities as fossil fuel, clean energy, or other

Each step can be run as a command line tool by running `cft <step>` where `<step>` is replaced the by the desired step (ex: `cft clean`). To see a list of command line options for a particular step, run `cft <step> --help`. 



## Provenance

Each step can be traced back to what it ran on and what code ran it.

- **Run manifests.** Every `cft` step writes `_run_manifest.json` to its output directory (`<output>/<state>/` for steps that run per state). It records the step, start and finish times, whether it succeeded (and the error if not), the git commit and whether the working tree had uncommitted changes, the command line and parsed arguments, the Python and package versions, and the path, size and sha256 of every file in the step's input and output directories. Downloaded archives (`_archives/`), the download log and partial downloads are left out, since the download log already records them. Normalize's inputs also list the `id_mapping.tsv` it reads from its output directory. An output directory can hold files from earlier runs, so each output says whether this run wrote it (`written_by_run`). A step's input hashes match the output hashes in the previous step's manifest when it read that step's files. The DuckDB database is listed by size only, since its bytes change whenever it is opened. The manifest is written with status `running` when a step starts and again when it ends, so a run that was killed shows as `running`. Each run replaces the previous manifest in that directory.
- **Download records.** `cft collect` keeps each original archive (zip or 7z) in `<raw>/<state>/_archives/`, with the retrieval time in the file name, so a state that rebuilds its archive daily (TX) doesn't overwrite the copy a run used. These add up: the TX zip is about 1 GB per download, so clear out old ones you don't need. Each download is logged as one JSON line in `<raw>/<state>/_downloads.jsonl`: the URL, retrieval time, the server's ETag and Last-Modified, the file's size and sha256, and the size and sha256 of each file extracted from it. This covers TX, PA and MI finance data, and MN finance data and election results. Arizona is scraped from a paged web search rather than downloaded as files, so each search is logged instead: the category, cycle, dates, filer type and position it asked for, when it ran, how many records came back, and the size and sha256 of the file they were written to. Arizona's independent expenditures are fetched once for spending that supports a candidate and once for spending that opposes one, into `...-Support.csv` and `...-Oppose.csv`, since the search only returns one position at a time and the rows don't say which.
- **Failed downloads.** A collector that could not get what it was asked for raises, so `cft collect` exits non-zero and the state's run manifest records the failure. Collectors that fetch one file per year or dataset (PA, MI, MN) try every file first, then raise once naming each one that failed; a PA year the department has no file for yet is skipped. Arizona skips a committee the server rejects, but fails the run if more than 5% of a type's ids are rejected, so a rate limit can't leave a nearly empty `Committee.csv` that looks complete. When several states are requested, every state runs before the command fails (when running locally; with `--slurm` each job fails on its own and the submitting command still exits 0). Minnesota's server has answered a bad download id with an HTML page and status 200; a download whose first line isn't a CSV header is rejected and the previous file is kept.
- **Linkage runs.** `cft link` never changes the source tables. Each run reloads the cleaned data for the requested states, then appends to three tables in the database: `linkage_run` (when it ran, the table and threshold, the model path and sha256, the splink version, and the git commit), `linkage_prediction` (every pair of records scored at or above the threshold, with its match probability and weight) and `linkage_mapping_history` (each record's canonical id for that run, and whether Splink or a matching organization name linked it). The view `linkage_mapping` is the latest run's mapping, and for each table that refers to Transactor ids a view `<table>_canonical` has those ids replaced by canonical ones; `Transactor_canonical` has one row per canonical id. Queries that want merged entities read the `_canonical` views; the source tables keep the ids normalize wrote. `Transaction_deduplicated` is a table, rewritten by each run, holding `Transaction_canonical` with each transfer between two filers counted once: the payer reports it in its expenditures and the recipient in its contributions (`reported_by` says which report a row came from), and the payer's copy is left out when the recipient's copy has the same canonical donor and recipient, amount and state, and a date within seven days. Only linkage can pair the two copies, since each names the other party by name rather than id. Rows from cleaned data written before `reported_by` existed load with it empty and are kept unmatched; the run names each such state. The text columns linkage compares are lowercased and trimmed in `transactor_detailed_view`, so a model trained before that change should be retrained with `--train`. A database written by the old in-place linkage has its `linkage_mapping` table renamed `linkage_mapping_legacy` the first time the new linkage runs.
- **Seeded linkage training.** The training sample is picked by a hash of each transactor's id and `TRAINING_SEED` (in `utils/link/train.py`), and the random sampling used to estimate u is seeded with it, so training on the same data gives the same model, even after the database is rebuilt.
- **Superseded and amended reports.** Texas marks every row of a report that a corrected report replaced with `infoOnlyFlag` Y; standardize keeps only the current rows. The other states need no filter: Pennsylvania's export holds one version of each filing (with `AMMEND` flagging amendments), Michigan's amended statements add rows rather than replace them, and Arizona's `ModifiesTransactionID` links a payment or refund to the transaction it adjusts. Each state's config says what was checked.
- **Repeatable ids.** Normalize makes each id from the row's source id, or its values when it has none, so normalizing the same standardized data with the same `--chunk-size` and `--input-format` gives the same ids. A different chunk size can change some ids: a row without a source id takes the id of a sourced entity with the same attributes only when that entity is in the same chunk.
- **Re-runs.** `standardize`, `normalize` and `clean` write a state's output to a hidden sibling folder (`<output>/.<state>.staging`) and swap it in only when the step finishes. A failed or killed run leaves the previous output as it was, and a finished run leaves no files from earlier runs behind. Normalize reads the previous `id_mapping.tsv` from the state's folder and carries it over. `collect` extracts each archive into a hidden folder first (`.<folder>.extracting`), then removes the files earlier downloads extracted there that the new archive doesn't have (the download log says which), and moves the new files in; TX rebuilds its archive daily with numbered files, so this keeps a smaller rebuild from leaving stale files behind. Arizona writes each search to a hidden temporary file and replaces the earlier files for that search only when every page has arrived.

## Past Student Team Members
Thanks to all of the students who have contributed to this project, including MPCS Practicum student Yue Xu; Data Science Clinic students Aïcha Camara, Alan Kagiri, Nicolas Posner, Yuzhou Wang, Adil Kassim, Nayna Pashilkar, Kaya Lee, Bhavya Pandey, and Yangge Xu; TAs Avery Schoen and Sarah Walker; and Research Assistants Steph Trello and Sarah Walker.

# Documentation

## Schema

### Database Schema YAML File:

Each top level key is the name of a table that exists in the schema.  
Tables may have the following keys:
- child_tables (list of strings where each string is a table name): schema. These are types that inherit this table's properties. If the parent has a matching key, it is extended. If the parent has a matching key that maps to a dictionary and there are matching keys in that dictionary, the values of the child are kept. 
- parent_table (single string that is a table name): A name of another table defined within the schema that the current table inherits from. 
- required_attributes (list of strings): list of necessary attributes for a row of the table this schema block represents to be valid. (For example, without donor, recipient, and amount, a transaction is not useful). If a required attribute is a relation (it ends with id), it will be considered present if it exists as a token (TODO) without the _id suffix.
- attributes (list of strings): all attributes of the table (all columns)
- enum_columns (mapping where keys are attributes and values are lists of strings): has keys that are names of table attributes that maps to a list of valid values for that attribute
- forward_relations (mapping where keys are attributes and values are table names): has keys that are names of table attributes with '_id' suffix removed that map to TODO. 
- reverse_relations (mapping where keys are strings and values are table names): has keys that are names of table attributes that map to TODO. These columns do not have  
- reverse_relation_names (mapping where keys are strings in reverse_relations and values are strings in the forward_relations of the table this column refers to): every entry in reverse_relations must have an entry here. This is to disambiguate which columns refer to which reverse relations

*Note on inheritance: A given table may have its own attributes, any attributes of any parent types (and parents of parent types, etc), or attributes of children (and children of children, etc.).