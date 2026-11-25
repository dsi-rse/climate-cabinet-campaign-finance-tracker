# General Contribution Guidelines

Thanks for your interest in contributing to the campaign finance tracker! If you would like to contribute or see an issue, please follow these steps:

1. Search through the [issues](https://github.com/uchicago-dsi/climate-cabinet-campaign-finance-tracker/issues) to see if someone else has identified this previously.
2. If your issue/feature has already been identified you can comment on it to see how you can contribute. Otherwise, create a new issue with a description of your bug or feature request. If it is a bug, please explain how someone can reproduce the unwanted behavoir in detail.
3. If you want to work on the bug or feature request, fork the repository and open a pull request. Name the Pull Request with '[WIP]' to indicate it is a work in progress until you are done and ready for review. 
4. Before getting merged into the main repository, your code must be reviewed, tested, and pass linting.

If you are interested in adding a new state to the collect or finance source standardizer logic, please see [below](#adding-a-new-state)



# Adding a new state

To add a new state to the campaign finance tracker, you need to add only code/configuration to `collect` the data and to `standardize` it. Once the data is standardized, all other steps run the same for each state. 

## Collect

Write code for scraping / collecting state data in the `collect` package. If the data is available as only a bulk download, it may not be a high priority to write code to do it. Document the process for finding the state's data in the state's `collect` module docstring. Please include links to relevant sources. It often makes sense to email the state organization responsible for placing campaign finance information on their website to ask questions about data availability and gaps in the data. 

Write the code in a file with the state's full, lowercase name in the `collect.finance` package. There should be a single function that is a wrapper for running the scraper with a subset of the arguments you see when running `cft collect --help` (with underscores replacing dashes `start_date` instead of `--start-date`); `output_directory` is always passed, so the function must accept it. Add a decorator `@register_special_state_collector('il')` to register the function under the state's code. `utils.collect` imports every module under `collect/finance` and `collect/election` when it loads, so a new module is found by being in the folder.

A collector should raise when it could not get what it was asked for (`utils.collect.downloads.DownloadError`, or the `requests` error itself), rather than printing the error and returning: `cft collect` then exits non-zero and the run manifest records the failure. If it fetches several files, try them all and raise once naming the ones that failed. Use `save_response` to write each download (it streams to a temporary file, hashes it, and can check the body before it replaces an earlier file), `append_download_record` to log it, and `replace_extracted_files` to extract an archive, so the folder ends up holding only the new archive's files. See `collect/finance/pennsylvania.py` for a small example.

## Standardize

To standardize state data, the `standardize.source.DataSourceStandardizationPipeline` class is used. This class provides a general algorithm that converts raw tabular data into a format that has standard and consistent names and properties. By default, the class initializes with a state code and a form code which map to yaml configuration files specifying how to standardize the data source's data. The components of the `DataSourceStandardizationPipeline` can be subclassed for more sophisticated modifications if necessary. This standardization allows the same code to normalize data from all state sources. 

For each state, a configuration file in `src/utils/standardize/finance/config` should be created with the state's lowercase 2 letter abbreviation code as the name and `.yaml` as the extension (ex: `pa.yaml` for Pennsylvania). Within the configuration file, a unique key should be made for each unique form type the state offers. A unique form type is a source of information from the state with a consistent format. For example, Pennsylvania releases data about contributions each year in one file and expenses in another. These each get their own form type. However, the 2002 files use an older layout with no header row. Therefore a separate form type (for example, `contributions_2002`) inherits from the main form type and overrides only what differs: the column order, the read parameters, and the path pattern. A form type that other forms inherit from does not need to be `meta` if it also describes real files. 

Please see [Configuration File Schema](#configuration-file-schema) for more details on how to fill out the configuration file. 

### Test fixtures

Add synthetic raw files for the state under `tests/data/standardize/raw/<state code>/`, written by a script next to them (see `make_pa_fixtures.py` and `make_az_mi_tx_fixtures.py`). Copy the real headers and value formats (dates, amounts, negatives) from the state's files, but use fake names, ids and amounts. Each file needs three rows, and every form needs at least one file: `tests/test_state_configs.py` checks that each file matches exactly one form and loads every row. Then add rows to `tests/test_state_golden_rows.py` that check, for each form, who paid whom, the amount, the date and the type. A row count alone won't catch a swapped donor and recipient or a date read from the wrong column.

### Checks when standardizing

- **Headers.** A form that reads columns by position (the default, `include_column_order: True`) has its `column_order` checked against each file before the file is read. With `header: 0`, the file's header must match `column_order`, ignoring case, spacing and a byte order mark. With `header: null`, the first row must have as many columns as `column_order` and must not be the header itself. Any other file raises `HeaderMismatchError`, which names the file and the columns that differ, so a state adding, dropping or reordering columns stops the run instead of putting values under the wrong names. Forms that read columns by header name (`include_column_order: False`) are not checked.
- **Superseded rows and who filed.** If the state marks rows that a corrected report has replaced (Texas's `infoOnlyFlag`), drop them with the form's `filter` so they aren't counted twice, and say in the config what the marker means. Every Transaction form sets `reported_by`: `recipient` for a file the receiving committee filed (contributions), `donor` for one the paying committee filed (expenditures). Rows that `transaction_direction` reverses switch it automatically. Linkage uses it to count a transfer between two filers once (`Transaction_deduplicated`), so don't drop the payer's copy at standardize unless the state gives no other way to tell the two apart.
- **Values that become null.** A raw value becomes null if it is missing from an `enum_mapper` mapping, doesn't fit its `date_format`, or isn't a number in a `post_load_float` column. Each file that has such values logs a warning with counts per column. `cft standardize` writes every distinct value, with its count, form and file, to `standardized/<state>/reports/coerced_values.csv`. Values mapped to null on purpose (a key with an empty value) and unmapped `transaction_direction` values are not reported. Check this report when adding a state or a mapping: most rows mean a mapping is missing a value.
- **Files and columns at run time.** A form whose `path_pattern` matches no file under the state's raw folder logs a warning naming the pattern and contributes no rows. A form read by header (`include_column_order: False`) logs a warning for each file that lacks a column the form maps, naming the standard columns that will be empty, unless another raw column in the file supplies the same standard name. A file whose path gives no year to `year_filter_filepath_regex` logs a warning (see [Year Filtering Configuration](#year-filtering-configuration)). `cft standardize` for a state code with no config stops with the codes that have one; codes are matched in lowercase.
- **The config itself.** A form is checked when it loads, and a `ConfigError` lists every problem at once, with the file and form. It catches: a key that isn't one the code reads (in the form or any block it inherits from), a column entry without `raw_name` and `type` or with a `type` pandas doesn't know, a `standard_name` that doesn't resolve against `table.yaml` for the form's `table_name` (each `--` part must be a relation of the table it's applied to and the last part an attribute; such a column would otherwise be carried along and dropped when the database is loaded), two columns the form reads given the same `standard_name`, an `enum_mapper` or `null_values` entry for a column the form never produces (the mapping would never be applied), an `enum_mapper` value that `table.yaml` doesn't list for an enum column, a `filter`, `year_column` or `overloaded_columns` entry naming a raw column the form doesn't have, a named group in an `overloaded_columns` pattern that isn't in `column_details`, `reported_by` or `transaction_direction` on a form that isn't a Transaction, a `year_filter_filepath_regex` with no capture group, a form that inherits a block that isn't in the file, and a missing `table_name`, `state_code` or `path_pattern` on a form that isn't `meta` (a form that inherits a `meta` base is not itself meta). `tests/test_state_configs.py` loads every form of every shipped config and checks each config has fixtures, so a broken config fails the suite.


### Configuration File Schema

There are two types of first level keys of the configuration files:
- Names of forms/data sources. These are used in the DataSourceStandardizationPipeline to lookup the data source's configuration.
- Abstract bases. These are used to prevent repeating keys that are shared accross multiple data sources in the same state. Another key can 'inherit' the properties of a base key by including an 'inherits' key with the name of the base. If both configurations share a key, the child one will take precedence. For example:

```yaml
base:
  foo: x
  bar: y

transactors:
  inherits: base
  foo: z
```
`transactors` will be read as having `foo = z` and `bar = y`. Only `column_details` is merged (the child's entry for a `raw_name` replaces the parent's; the rest are kept); every other key the child sets replaces the parent's value whole, so a child that sets `enum_mapper` or `null_values` must list everything it needs. An inherited column can't be removed, but a child's `column_order` only names the columns its files have, so a parent column left out of it is not read. Put a mapping or `null_values` entry on the form whose columns it names rather than on a shared base: validation rejects an entry for a column the form doesn't produce.

Both first level keys have the same set of subkeys:
- `state_code`: two letter lowercase state code
- `meta`: True if this block is only an abstract or meta class that is used by other forms.
- `read_csv_params`: keyword arguments to be passed to pandas [read_csv](https://pandas.pydata.org/docs/reference/api/pandas.read_csv.html) method.
- `include_column_order`: boolean. If True, 'names' is passed as a read_csv param with the column_order, and each file's header is checked against it (see [Checks when standardizing](#checks-when-standardizing)). If False, column order will be inferred from header row (either provided in `read_csv_params` or 0)
- `column_details`: list of column properties where each may have the following keys:
    - `raw_name`: the name of the column as it appears in the raw data
    - `type`: Pandas dtype of the column
    - `standard_name`: (optional) The name for the column based on our [standard naming rules](#standard-column-naming). Even if the standard name is the same as the raw name, this must be included. If no standard name is included, this column will be dropped during standardization. 
    - `date_format`: (optional) Format of dates in the provided data according to [datetime strftime](https://docs.python.org/3/library/datetime.html#strftime-strptime-behavior). If unix time is used, use `%unix_ms` for unix ms. If files write dates in more than one way, give a list of formats; each date is parsed with the first format that fits. Dates that fit no format become null, so check that the format matches the full value (for example `9/10/2003 0:00:00` needs `%m/%d/%Y %H:%M:%S`).
    - `post_load_float`: (optional) If the column should be interpreted as a float, but contains erroneous rows or ',' and '$' characters that pandas cannot handle, set the column as a string and set this value to True. Accounting-style negatives such as `($17.85)` are read as negative.
    - `absolute_value`: (optional) If True, negative values become positive after the transaction direction is applied. Use this when a source signs amounts by direction (negative for money leaving the filer) and `transaction_direction` already records that direction.
    - `optional`: (optional) If True, a file read by header (`include_column_order: False`) may lack this column without the run warning that its standard column will be empty. Say in a comment which files have it (Minnesota's vendor address columns are only in downloads before 2026). Forms read by position check the header instead.
    - `description`, `notes`: (optional) For the reader; the code ignores them.
- `column_order`: list of columns in the order they appear in the data format. If not provided, will default to the order in column_properties.
- `duplicate_columns`:
    - map of a standard column name the form produces (including one added by `state_code_columns`) to a list of standard names that should be copies of it. The copies are made after renaming, so an `enum_mapper` entry for a copy applies to it.
- `new_empty_columns`:
    - list of standard column names to add with every value null, for a table that needs the column but the state gives nothing for.
- state_code_columns:
    - list of columns to add to table with each row containing the state code. If this data is mixed with other states, we'll want to be able to know that the data pertains to this state.
- `enum_mapper`:
    - keys are standard column names the form produces (from `column_details`, `duplicate_columns`, `state_code_columns` or `new_empty_columns`) and map to mappings of raw values to standard values.
    - where `table.yaml` lists values for the column (`enum_columns`, for example `transactor_type`, `transactor_type_specific`, `party`, `transaction_type`, `office_sought`), every mapped value must be one of them; add a value to `table.yaml` if a state really needs a new one. A column with no listed values (a county name, say) may map to anything.
    - raw values missing from the mapping become null and are reported (see [Checks when standardizing](#checks-when-standardizing)).
- `table_name`: the table the form's rows belong to: `Transaction` or `Transactor` (a key of `table.yaml`, so the capital letter matters). Required on every form that isn't `meta`, as are `state_code` and `path_pattern`.
- `overloaded_columns`:
    - map of raw column names to two maps 'filter' and 'pattern'. 
        - `filter` maps to a mapping of raw column names to values.
        - `pattern` maps to a string regex pattern with named capture groups
    - overloaded columns is for cases where the raw data contains a column with multiple pieces of information jammed into a single column. This column must follow a consistent format. The splitting will only be attempted on those rows that contain one of the listed values for each of the listed columns. The named groups should be mapped to standard names in `column_details`. If the pattern is not a match, the new columns will be filled with NaNs.
- `null_values`:
  - map of standard column names the form produces to a list of values that should be replaced with null.
  - For example, states sometimes have 0 amount transactions that should be null.
- `filter`:
  - map of raw column names to either a list of the values to keep, or a `NOT` key mapping to a list of the values to drop. Use it for rows the state marks as superseded (Texas keeps only `infoOnlyFlag: ["N"]`), not for the payer's copy of a transfer between filers: linkage pairs those copies through `reported_by` and `Transaction_deduplicated` (Minnesota's `Type: NOT: [Contribution]` predates that and is kept for now).
- `reported_by`:
  - `recipient` or `donor`: which party filed the rows of a Transaction form. Contribution files are the recipient's report; expenditure files the payer's. Rows that `transaction_direction` reverses switch it automatically. Any other value raises `ConfigError` when the config loads.
- `path_pattern`: regex describing the default location of default raw files of this type. Relative to `${DATA_DIR}/${state_code}` directory. 

#### Standard Column Naming
The state source standardization steps are to prepare the state code to be normalized and joined with other states. As part of this there is a specific naming pattern for columns. Standard table attributes are named in `table.yaml` under attributes. Provided source data, however, may not be normalized. These columns will be named with a `SPLIT` separator (`--`) between the name of the relation and the name of the attribute in the related column. This may be nested (i.e. if in a transaction table we are given a donor's address, this would be shown as `donor--address--line_1`). If a column is a repeated column (i.e. there are two amount columns to signify two transactions that share all other properties), it will end with `-\d` where \d is an integer. Valid column names include alphabetic characters and underscores.

Special column names:
- `transaction_direction`: this is used if a table doesn't have set 'donor' and 'recipient' columns and the direction of the transaction is specified by another column. If this exists this column should be given a standard name of 'transaction_direction' and an enum mapper that maps all values requiring a reversal mapped to 'reverse'. In reversed rows every column starting with `donor` is renamed to start with `recipient` and the reverse (so `donor_id` becomes `recipient_id` and `donor--address--city` becomes `recipient--address--city`); the column is dropped afterwards. Map the columns as they are for a transaction paid to the filer. If the direction comes from a column that is also mapped to something else, such as a transaction type code, copy it with `duplicate_columns` (see `az.yaml`).
  - Check direction for every form that includes money the filer pays out. A form that lists the filer and a counterparty in fixed columns usually needs the filer mapped to `donor` for expenditures and to `recipient` for contributions; it can't inherit one from the other's `column_details` unchanged.
- `reported_state`: this column is metadata automatically filled and propogated to all derived tables. 
- `source_identifier`: the state's own id for a transaction row (not named `source_id`, because normalize treats every `<name>_id` column as a foreign key and replaces its values with uuids). Map it only if the state gives an id that is unique per row (check the real files: PA's `CampaignFinanceID` and MI's `contribution_id` repeat, so they are not mapped). `source_file` (the raw file's path relative to the state's data directory) and `source_row` (the row's record number in that file, starting at 1 after any header) are filled automatically for every Transaction form, so each transaction can be traced to its raw row even without a `source_identifier`. Blank lines and lines that `read_csv` skips as malformed (`on_bad_lines: warn` or `skip`) are counted, so `source_row` stays right after them: with a header, the raw line is line `source_row + 1`. A quoted value that spans lines is one record, so after one the line number is higher.
- `organization_name`: This will be mapped to `full_name` and the transactor type marked as organization.
- Name columns (`full_name`, `first_name`, `last_name`, ...): map each column to what it holds, and leave the rest to `clean`. A column that holds either a person's last name or an organization's name (Michigan's `l_name_or_org`, Arizona's `LastName`) is a `last_name`: clean moves a last name with no first name beside it to `full_name` for any record not typed Individual, takes a trailing title, suffix or marker off a person's last name, and fills the missing parts of a person's name from `full_name`. A record the state gives no type is typed Individual when it has a first name and no organization word in its name, so don't map a contact person's first name onto an organization's row. Don't map a whole name to `full_name` beside a separate given-name column unless `full_name` really is the whole name.

#### Year Filtering Configuration

To enable year filtering for data sources, add the following optional fields:

- `year_filter_filepath_regex`: (optional) Regex pattern to extract the year from a file's path. It is searched against the path relative to the state's raw folder (`2021/contrib.txt`), so `"^(\\d{4})/"` matches files in a year folder. The first capture group must hold the year as a 4-digit number (the config check requires a group). A file whose path gives no year is read when no year range was asked for and skipped when one was, and a warning names the file either way.
- `year_column`: (optional) Raw column name containing year data for row-level filtering.

When both are provided, filepath filtering is applied first to reduce the number of files read, then column filtering is applied to the resulting data.

Example:
```yaml
contributions_2020_2022:
  inherits: base
  path_pattern: "(?i)^(20[2-3][0-9])/contrib.*\\.txt$"
  year_filter_filepath_regex: "^(\\d{4})/"  # the year is the folder: "2021/contrib.txt"
  year_column: "ELECTION_YEAR"  # Filter rows by this column after reading
```

This configuration allows the `standardize_states` function to filter data by year using `start_year` and `end_year` parameters.

### Special Cases

If the configuration keys can't express what a state needs, a form can be backed by Python. Subclass `DataSourceStandardizationPipeline` (or give it a custom `DataReader`, `SchemaTransformer` or `DataStandardizer`) in `src/utils/standardize/finance/<state code>.py`, for example `il.py`, and decorate the class:

```python
from utils.standardize.source import DataSourceStandardizationPipeline
from utils.standardize.source_standardization_registry import register_special_data_source


@register_special_data_source("il", form_code="some_form")
class IllinoisSomeForm(DataSourceStandardizationPipeline):
    ...
```

The form still has its block in `il.yaml` under `some_form`, and is validated like any other; the decorator makes an instance of the class with that state and form code, and registers it before the config-only pipelines, so it replaces the one the config alone would have made. Modules whose names start with an underscore are not imported as states. Before writing Python, check whether a config feature would serve every state (open an issue for it): the five existing states need none.

### Definition of done for a new state

- `cft collect -s <code>` fetches the state's files, raises if it can't, and logs each download; the module docstring says where the data comes from.
- `<code>.yaml` has a form for every file layout; every form loads (the config checks pass) and the only `coerced_values.csv` rows are values that really have no mapping.
- Synthetic fixtures under `tests/data/standardize/raw/<code>/` cover every form, with a script that made them, and `tests/test_state_configs.py` passes for the state.
- `tests/test_state_golden_rows.py` has a row per form checking who paid whom, the amount, the date and the type, including a reversed row if the state uses `transaction_direction`.
- Every Transaction form sets `reported_by`; the config comments say what the state's amendment or superseded markers mean and what was done about them (see the Texas and Pennsylvania configs).
- `cft standardize`, `normalize` and `clean` run on the real data, and the row counts and totals were compared with a figure the state publishes.

