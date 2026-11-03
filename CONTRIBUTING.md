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
- `include_column_order`: boolean. If True, 'names' is passed as a read_csv param with the column_order. If False, column order will be inferred from header row (either provided in `read_csv_params` or 0)
- `column_details`: list of column properties where each may have the following keys:
    - `raw_name`: the name of the column as it appears in the raw data
    - `type`: Pandas dtype of the column
    - `standard_name`: (optional) The name for the column based on our [standard naming rules](#standard-column-naming). Even if the standard name is the same as the raw name, this must be included. If no standard name is included, this column will be dropped during standardization. 
    - `date_format`: (optional) Format of dates in the provided data according to [datetime strftime](https://docs.python.org/3/library/datetime.html#strftime-strptime-behavior). If unix time is used, use `%unix_ms` for unix ms.
    - `post_load_float`: (optional) If the column should be interpreted as a float, but contains erroneous rows or ',' and '$' characters that pandas cannot handle, set the column as a string and set this value to True. 
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
- `transaction_direction`: this is used if a table doesn't have set 'donor' and 'recipient' columns and the direction of the transaction is specified by another column. If this exists this column should be given a standard name of 'transaction_direction' and an enum mapper that maps all values requiring a reversal mapped to 'reverse'
- `reported_state`: this column is metadata automatically filled and propogated to all derived tables. 
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

