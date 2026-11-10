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

Write the code in a file with the state's full, lowercase name in the `collect.finance` package. There should be a single function that is a wrapper for running the scraper with a subset of the arguments you see when running `cft collect --help` (with underscores replacing dashes `start_date` instead of `--start-date`). Add a decorator `@register_special_state_collector('il')` to make the state collector discoverable. 

## Standardize

To standardize state data, the `standardize.source.DataSourceStandardizationPipeline` class is used. This class provides a general algorithm that converts raw tabular data into a format that has standard and consistent names and properties. By default, the class initializes with a state code and a form code which map to yaml configuration files specifying how to standardize the data source's data. The components of the `DataSourceStandardizationPipeline` can be subclassed for more sophisticated modifications if necessary. This standardization allows the same code to normalize data from all state sources. 

For each state, a configuration file in `src/utils/standardize/finance/config` should be created with the state's lowercase 2 letter abbreviation code as the name and `.yaml` as the extension (ex: `pa.yaml` for Pennsylvania). Within the configuration file, a unique key should be made for each unique form type the state offers. A unique form type is a source of information from the state with a consistent format. For example, Pennsylvania releases data about contributions each year in one file and expenses in another. These each get their own form type. However, the 2002 files use an older layout with no header row. Therefore a separate form type (for example, `contributions_2002`) inherits from the main form type and overrides only what differs: the column order, the read parameters, and the path pattern. A form type that other forms inherit from does not need to be `meta` if it also describes real files. 

Please see [Configuration File Schema](#configuration-file-schema) for more details on how to fill out the configuration file. 

### Test fixtures

Add synthetic raw files for the state under `tests/data/standardize/raw/<state code>/`, written by a script next to them (see `make_pa_fixtures.py` and `make_az_mi_tx_fixtures.py`). Copy the real headers and value formats (dates, amounts, negatives) from the state's files, but use fake names, ids and amounts. Each file needs three rows, and every form needs at least one file: `tests/test_state_configs.py` checks that each file matches exactly one form and loads every row. Then add rows to `tests/test_state_golden_rows.py` that check, for each form, who paid whom, the amount, the date and the type. A row count alone won't catch a swapped donor and recipient or a date read from the wrong column.

### Checks when standardizing

- **Headers.** A form that reads columns by position (the default, `include_column_order: True`) has its `column_order` checked against each file before the file is read. With `header: 0`, the file's header must match `column_order`, ignoring case, spacing and a byte order mark. With `header: null`, the first row must have as many columns as `column_order` and must not be the header itself. Any other file raises `HeaderMismatchError`, which names the file and the columns that differ, so a state adding, dropping or reordering columns stops the run instead of putting values under the wrong names. Forms that read columns by header name (`include_column_order: False`) are not checked.
- **Values that become null.** A raw value becomes null if it is missing from an `enum_mapper` mapping, doesn't fit its `date_format`, or isn't a number in a `post_load_float` column. Each file that has such values logs a warning with counts per column. `cft standardize` writes every distinct value, with its count, form and file, to `standardized/<state>/reports/coerced_values.csv`. Values mapped to null on purpose (a key with an empty value) and unmapped `transaction_direction` values are not reported. Check this report when adding a state or a mapping: most rows mean a mapping is missing a value.


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
`transactors` will be read as having `foo = z` and `bar = y`. 

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
- `column_order`: list of columns in the order they appear in the data format. If not provided, will default to the order in column_properties.
- `duplicate_columns`:
    - map of standard column names to list of additional columns that should be copies of them
- state_code_columns:
    - list of columns to add to table with each row containing the state code. If this data is mixed with other states, we'll want to be able to know that the data pertains to this state.
- `enum_mapper`:
    - keys are names of standardized column names and map to mappings of raw values to standard values for a given enum.
    - if additional columns are generated during standardization that are enums, their names should be listed here as well. 
- `table_name`: type of table represented. transaction, transactor, election, election_result, address, membership.
- `overloaded_columns`:
    - map of raw column names to two maps 'filter' and 'pattern'. 
        - `filter` maps to a mapping of raw column names to values.
        - `pattern` maps to a string regex pattern with named capture groups
    - overloaded columns is for cases where the raw data contains a column with multiple pieces of information jammed into a single column. This column must follow a consistent format. The splitting will only be attempted on those rows that contain one of the listed values for each of the listed columns. The named groups should be mapped to standard names in `column_details`. If the pattern is not a match, the new columns will be filled with NaNs.
- `null_values`:
  - map of column names to list of values that should be replaced with null.
  - For example, states sometimes have 0 amount transactions that should be null.
- `filter`:
  - map of standard column names to 'NOT' key that maps to all the values of that column that should not be included in returned dataframe. This is required as sometimes unfiltered data will double count filer to filer transactions (ex: filer A makes a contribution to filer B. A reports it in expenses and B reports it in contributions). 
- `path_pattern`: regex describing the default location of default raw files of this type. Relative to `${DATA_DIR}/${state_code}` directory. 

#### Standard Column Naming
The state source standardization steps are to prepare the state code to be normalized and joined with other states. As part of this there is a specific naming pattern for columns. Standard table attributes are named in `table.yaml` under attributes. Provided source data, however, may not be normalized. These columns will be named with a `SPLIT` separator (`--`) between the name of the relation and the name of the attribute in the related column. This may be nested (i.e. if in a transaction table we are given a donor's address, this would be shown as `donor--address--line_1`). If a column is a repeated column (i.e. there are two amount columns to signify two transactions that share all other properties), it will end with `-\d` where \d is an integer. Valid column names include alphabetic characters and underscores.

Special column names:
- `transaction_direction`: this is used if a table doesn't have set 'donor' and 'recipient' columns and the direction of the transaction is specified by another column. If this exists this column should be given a standard name of 'transaction_direction' and an enum mapper that maps all values requiring a reversal mapped to 'reverse'. In reversed rows every column starting with `donor` is renamed to start with `recipient` and the reverse (so `donor_id` becomes `recipient_id` and `donor--address--city` becomes `recipient--address--city`); the column is dropped afterwards. Map the columns as they are for a transaction paid to the filer. If the direction comes from a column that is also mapped to something else, such as a transaction type code, copy it with `duplicate_columns` (see `az.yaml`).
  - Check direction for every form that includes money the filer pays out. A form that lists the filer and a counterparty in fixed columns usually needs the filer mapped to `donor` for expenditures and to `recipient` for contributions; it can't inherit one from the other's `column_details` unchanged.
- `reported_state`: this column is metadata automatically filled and propogated to all derived tables. 
- `source_identifier`: the state's own id for a transaction row (not named `source_id`, because normalize treats every `<name>_id` column as a foreign key and replaces its values with uuids). Map it only if the state gives an id that is unique per row (check the real files: PA's `CampaignFinanceID` and MI's `contribution_id` repeat, so they are not mapped). `source_file` (the raw file's path relative to the state's data directory) and `source_row` (the row's position among the rows read from that file, starting at 1) are filled automatically for every Transaction form, so each transaction can be traced to its raw row even without a `source_identifier`. Lines that `read_csv` skips as malformed (`on_bad_lines`) are not counted, so after one `source_row` is lower than the line number.
- `organization_name`: This will be mapped to `full_name` and the transactor type marked as organization.

#### Year Filtering Configuration

To enable year filtering for data sources, add the following optional fields:

- `year_filter_filepath_regex`: (optional) Regex pattern to extract year from file paths. The first capture group should contain the year as a 4-digit number (e.g., `"^(\\d{4})/"`).
- `year_column`: (optional) Raw column name containing year data for row-level filtering.

When both are provided, filepath filtering is applied first to reduce the number of files read, then column filtering is applied to the resulting data.

Example:
```yaml
contributions_2020_2022:
  inherits: base
  path_pattern: "(?i)^(20[2-3][0-9])/contrib.*\\.txt$"
  year_filter_filepath_regex: "^(\\d{4})/"  # Extract year from path like "2021/contrib.txt"
  year_column: "ELECTION_YEAR"  # Filter rows by this column after reading
```

This configuration allows the `standardize_states` function to filter data by year using `start_year` and `end_year` parameters.

### Special Cases

If the provided options in the provided configuration files do not allow the state's details to be captured, you will need to write a custom python code. This should be done by writing a subclass of `DataSourceStandardizationPipeline` or one of its component classes. This code should be added to `utils.standardization.finance` in a file with the states full, lowercased name. The class should be given a `@register_special_pipeline(state='il', form_code='some_form')` decorator where the configuration should be present in the `il.yaml` (or whatever the state's code is) config file under the `some_form` (or whatever you name the form) block. 

