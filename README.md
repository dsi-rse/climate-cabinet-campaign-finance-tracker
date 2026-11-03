# Climate Cabinet Campaign Finance Tracker

This package provides tools for collecting and processing state campaign finance data. Currently the package supports Arizona, Michigan, Minnesota, Pennsylvania, and Texas. To request another state, please open an issue. To add another state yourself, see [Adding a new state](./CONTRIBUTING.md#adding-a-new-state).

This package was developed at the Data Science Institute at the University of Chicago in partnership with Climate Cabinet. 

## Installation

### Docker (recommended)

For the most consistent installation, the pipeline components can be run using Docker. To install Docker, visit [the docker website](https://docs.docker.com/get-started/get-docker/) and follow the directions to get started. 

### Local

If you are not using Docker, it is still recommended to use a Python environment to avoid dependency conflicts. Conda is a good option. 

To install in an environment:
```bash
pip install -r requirements
pip install -e . 
```

## Use

### Docker / Make

If you have set up with Docker, the quickest way to get started is to use make to interact with Docker. You can run one of the following commands:

```bash
make run-collect
make run-standardize
make run-normalize
make run-clean
make run-link
make run-classify
```
To run the step of the pipeline with default options and chunk-size set to 2000.

If you would like to connect a jupyter lab to the docker container and use python notebooks in your browser:

```bash
make run-notebooks
```

If you would like to run the fully configurable [Command Line Interface](#local--command-line-interface), please run `make interactive` to connect your terminal to the docker container and use the commands below. To exit, type `exit`

### Local / Command Line Interface

If you have completed a local installation, the `cft` command should be accessible to you. This way will provide you with the most configuration options. 

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
Path to DuckDB database to load/save data. (link step)

#### --model-path
Path to record linkage model. If training, this is the path to save the model. (link step)

#### --threshold
Match probability threshold to consider two records a match. Default: `0.95`. (link step)

#### --table-name
Table to perform record linkage on. Default: `transactor_detailed_view`. (link step)

#### --train
Train a record linkage model. (link step)

#### --overwrite
Overwrite existing database and tables. (link step)

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