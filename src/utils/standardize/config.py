"""Configuration hanlder class for state campaign finance data"""

import re
from functools import lru_cache
from pathlib import Path

import pandas as pd
from yaml import safe_load

from utils.constants import (
    DEFAULT_SCHEMA_PATH,
    SPLIT,
    STATE_FINANCE_CONFIG_DIRECTORY,
)
from utils.schema import DataSchema

# Values of a Transaction form's reported_by: the party whose report the rows
# come from (see table.yaml)
REPORTED_BY_VALUES = ("donor", "recipient")
# Every key a form block may have. Anything else is a typo (CONTRIBUTING.md
# describes each one)
FORM_KEYS = frozenset(
    {
        "inherits",
        "meta",
        "state_code",
        "table_name",
        "path_pattern",
        "read_csv_params",
        "include_column_order",
        "column_details",
        "column_order",
        "duplicate_columns",
        "new_empty_columns",
        "state_code_columns",
        "reported_by",
        "enum_mapper",
        "overloaded_columns",
        "null_values",
        "filter",
        "year_filter_filepath_regex",
        "year_column",
    }
)
# Every key a column_details entry may have. description and notes are for
# the reader only
COLUMN_KEYS = frozenset(
    {
        "raw_name",
        "type",
        "standard_name",
        "date_format",
        "post_load_float",
        "absolute_value",
        "description",
        "notes",
    }
)
# column_details keys that only mean something on a column that is kept
_KEYS_NEEDING_STANDARD_NAME = ("date_format", "post_load_float", "absolute_value")
# Standard names that are not schema attributes: transaction_direction marks
# rows whose donor and recipient are swapped (Transaction forms only), and a
# <prefix>organization_name becomes <prefix>full_name with the type set to
# Organization (see DataStandardizer)
TRANSACTION_DIRECTION = "transaction_direction"
ORGANIZATION_NAME = "organization_name"
# the only value transaction_direction's mapping may produce
TRANSACTION_DIRECTION_VALUES = ("reverse",)
# a repeated column, such as amount-2, ends with a dash and a number
_REPEATED_COLUMN_SUFFIX = re.compile(r"-\d+$")


class ConfigError(ValueError):
    """A state config says something the pipeline can't act on

    The message lists every problem found in the form, one per line, with the
    file and form it was found in.
    """


@lru_cache(maxsize=1)
def _data_schema() -> DataSchema:
    """The table schema standard names are checked against"""
    return DataSchema(DEFAULT_SCHEMA_PATH)


def resolve_standard_name(table_name: str, standard_name: str) -> tuple[str, str]:
    """Find the table and attribute a standard column name refers to

    A standard name is an attribute of the form's table (amount), a path of
    relation prefixes to an attribute of a related table
    (donor--address--city), either optionally ending in a repeat number
    (amount-2), or one of the special names TRANSACTION_DIRECTION (Transaction
    forms only) and <prefix>ORGANIZATION_NAME, which stands for the full_name
    of a Transactor. Relations follow table.yaml, with every table's children
    folded in, so donor--employer--role goes Transaction, Transactor
    (Individual's employer), Membership.

    Args:
        table_name: The form's table_name.
        standard_name: The name to resolve.

    Returns:
        The table that holds the attribute, and the attribute.

    Raises:
        ConfigError: if any step of the path is not a relation of the table it
            is applied to, or the last part is not an attribute of the table
            it lands on.
    """
    schema = _data_schema().schema
    if table_name not in schema:
        raise ConfigError(f"{table_name!r} is not a table in table.yaml")
    base_name = _REPEATED_COLUMN_SUFFIX.sub("", standard_name)
    *prefixes, attribute = base_name.split(SPLIT)
    current = table_name
    for prefix in prefixes:
        relations = schema[current].relations
        if prefix not in relations:
            raise ConfigError(
                f"{standard_name!r}: {prefix!r} is not a relation of {current}"
                f" (its relations are {', '.join(sorted(relations)) or 'none'})"
            )
        current = relations[prefix]
    if attribute == ORGANIZATION_NAME and "full_name" in schema[current].attributes:
        return current, "full_name"
    if attribute in schema[current].attributes:
        return current, attribute
    if not prefixes and attribute == TRANSACTION_DIRECTION:
        if table_name != "Transaction":
            raise ConfigError(
                f"{TRANSACTION_DIRECTION} only applies to Transaction forms,"
                f" not {table_name}"
            )
        return table_name, attribute
    raise ConfigError(
        f"{standard_name!r}: {attribute!r} is not an attribute of {current}"
        f" (see table.yaml)"
    )


def resolve_inheritance(config: dict, form_code: str) -> dict:
    """Recursively resolves inheritance from parent form_configs

    If the a key exists in both a child and parent config:
    - For most cases, the child value will override the parent value
    - If the value is 'column_details', the child value will 'update' the parent
      value. (i.e. the child value will be used in duplicate 'raw_name' keys, all
      keys not present in the child will be used from the parent).
    - If there is no column_order in parent or child, the column order will be the
      order of appearance in column_details in the parent, followed by those that only
      appear in the child.

    Args:
        config: dictionary contents of a state config
        form_code: key in config
    Raises:
        KeyError: if the value of config[form_code]['inherits'] is
            not a key in config
        RecursionError: if a form in config inherits from itself.
            Note that more complex cases of recusrion are not caught by
            this.
    """
    child_config = config.get(form_code, {}).copy()
    parent_form_code = child_config.get("inherits")
    if not parent_form_code:
        return child_config
    if parent_form_code not in config:
        raise KeyError(
            "{inherits} not in config file. Avaialbe options are:"
            f" {', '.join(child_config.keys())}"
        )
    if parent_form_code == form_code:
        raise RecursionError(f"Configuration {form_code} inherits from itself")
    parent_config = resolve_inheritance(config, parent_form_code)
    for key, value in child_config.items():
        if key in parent_config and key == "column_details":
            parent_column_details_dict = {
                column_detail["raw_name"]: column_detail
                for column_detail in parent_config["column_details"]
            }
            child_column_details_dict = {
                column_detail["raw_name"]: column_detail for column_detail in value
            }
            parent_config[key] = {
                **parent_column_details_dict,
                **child_column_details_dict,
            }.values()
        elif value is not None:
            parent_config[key] = value
    return parent_config


class ConfigHandler:
    """Handles and validates metadata"""

    @property
    def default_config_folder(self) -> Path:
        """Default path to state campaign finance configuration files"""
        return STATE_FINANCE_CONFIG_DIRECTORY

    @property
    def state_code(self) -> str:
        """Two letter state abbreviation"""
        return self._state_code

    @property
    def table_name(self) -> str:
        """Type of table: 'Transaction', 'Transactor', etc."""
        return self._table_name

    @property
    def raw_data_path_pattern(self) -> str:
        """Regex matching names of raw files in data/raw folder"""
        return re.compile(self._raw_data_path_pattern)

    @property
    def dtype_dict(self) -> dict[str, str]:
        """Maps raw column names to pandas dtypes"""
        return {col["raw_name"]: col["type"] for col in self._column_details}

    @property
    def read_csv_params(self) -> dict:
        """Keyword arguments used in pandas read_csv for reading in tabular file"""
        if self._include_column_order:
            self._read_csv_params["names"] = self.raw_column_order
        elif "header" not in self._read_csv_params:
            self._read_csv_params["header"] = 0
        return self._read_csv_params

    @property
    def column_mapper(self) -> dict[str, str]:
        """Maps raw column names to standardized column names"""
        return {
            col["raw_name"]: col["standard_name"]
            for col in self._column_details
            if "standard_name" in col
        }

    @property
    def relevant_columns(self) -> list[str]:
        """List of raw column names to keep"""
        return list(self.column_mapper.keys())

    @property
    def duplicate_columns(self) -> dict[str, str]:
        """Maps existing columns to additional columns that should have equal values"""
        return self._duplicate_columns

    @property
    def new_empty_columns(self) -> list[str]:
        """Names of new columns that should be added empty"""
        return self._new_empty_columns

    @property
    def state_code_columns(self) -> list[str]:
        """Names of columns that should be added with all rows containing state code"""
        return self._state_code_columns

    @property
    def reported_by(self) -> str | None:
        """Which party filed the rows of this form: "donor", "recipient" or None

        Set on Transaction forms. A contribution file is filed by the committee
        that received the money ("recipient"); an expenditure file by the one
        that paid it ("donor"). Rows that transaction_direction reverses get the
        other value. None leaves the column empty.
        """
        return self._reported_by

    @property
    def enum_mapper(self) -> dict[dict[str, str]]:
        """Maps enum column names to mappings of raw values to standard values"""
        return self._enum_mapper

    @property
    def column_to_date_format(self) -> dict[str, str]:
        """Maps date columns to default raw date format"""
        return {
            col["standard_name"]: col["date_format"]
            for col in self._column_details
            if "date_format" in col
        }

    @property
    def post_load_float_columns(self) -> list[str]:
        """List of columns that should be converted to floats after loading

        These are columns that have floats that pandas cannot accurately convert
        to floats on read (they contain , or $).
        """
        return [
            col["standard_name"]
            for col in self._column_details
            if col.get("post_load_float", False)
        ]

    @property
    def absolute_value_columns(self) -> list[str]:
        """Standard names of columns whose values should be made non-negative

        For sources that sign amounts by direction, once that direction is
        applied through transaction_direction.
        """
        return [
            col["standard_name"]
            for col in self._column_details
            if col.get("absolute_value", False)
        ]

    @property
    def raw_column_order(self) -> list[str]:
        """List of columns in order in raw file"""
        return self._column_order

    @property
    def year_filter_filepath_regex(self) -> str | None:
        """Regex pattern to extract year from filepath for filtering"""
        return self._year_filter_filepath_regex

    @property
    def year_column(self) -> str | None:
        """Raw column name containing year data for filtering"""
        return self._year_column

    @property
    def overloaded_columns(self) -> dict[str, dict[str, str]]:
        """Maps column names to information about how to split them into multiple columns"""
        return self._overloaded_columns

    def __init__(
        self,
        form_code: str,
        config_file_path: Path | None = None,
        state_code: str | None = None,
    ) -> None:
        """Manage configuration from config file

        Notes: One and only one of config_file_path or state_code must be provided.

        Args:
            form_code: Name of form, must be key in configuration file
            config_file_path: Path to configuration file
            state_code: two letter abbreviation of state with config
                file in default config folder
        """
        if config_file_path is None:
            if state_code is not None:
                config_file_path = (
                    self.default_config_folder / f"{state_code.lower()}.yaml"
                )
            else:
                raise RuntimeError(
                    "Either config_file_path or state_code must be provided"
                )
        else:
            if state_code is not None:
                raise RuntimeError(
                    "Only one of state_code and config_file_path should be provided"
                )

        config_file_path = Path(config_file_path)
        with config_file_path.open("r") as f:
            config = safe_load(f)

        if form_code not in config:
            raise KeyError(f"Form code '{form_code}' not found in configuration file.")

        form_config = resolve_inheritance(config, form_code)
        validate_form_config(config, form_code, form_config, config_file_path)

        # default column order is the order in column_details

        self._column_details = form_config.get("column_details", [])
        self._column_order = form_config.get(
            "column_order", [col["raw_name"] for col in self._column_details]
        )

        self._include_column_order = form_config.get("include_column_order", True)
        self._enum_mapper = form_config.get("enum_mapper", {})
        self._read_csv_params = form_config.get("read_csv_params", {})
        self._duplicate_columns = form_config.get("duplicate_columns", {})
        self._new_empty_columns = form_config.get("new_empty_columns", [])
        self._state_code_columns = form_config.get("state_code_columns", [])
        self._reported_by = form_config.get("reported_by")
        self._state_code = form_config.get("state_code", state_code)
        self._table_name = form_config.get("table_name")
        self._raw_data_path_pattern = form_config.get("path_pattern")
        self._year_filter_filepath_regex = form_config.get("year_filter_filepath_regex")
        self._year_column = form_config.get("year_column")
        self._overloaded_columns = form_config.get("overloaded_columns", {})
        self._null_values = form_config.get("null_values", {})
        self._filter = form_config.get("filter", {})


def _inheritance_chain(config: dict, form_code: str) -> list[str]:
    """The form and the forms it inherits from, child first"""
    chain = []
    while form_code and form_code in config and form_code not in chain:
        chain.append(form_code)
        form_code = (config[form_code] or {}).get("inherits")
    return chain


def validate_form_config(
    config: dict, form_code: str, form_config: dict, config_file_path: Path
) -> None:
    """Check a resolved form config against the schema and the keys the code reads

    Catches what would otherwise fail silently: a misspelled key, a
    standard_name that is not in table.yaml (its column would be carried along
    and dropped when the database is loaded), a mapping on a column the form
    never produces (never applied), an enum value the schema does not know, a
    filter on a column the file does not have.

    Args:
        config: The whole config file, for the form's inheritance chain.
        form_code: The form being loaded.
        form_config: That form after resolve_inheritance.
        config_file_path: Where it came from, for the message.

    Raises:
        ConfigError: listing every problem found, one per line.
    """
    problems = []
    blocks = {
        code: config[code] or {} for code in _inheritance_chain(config, form_code)
    }
    for code, block in blocks.items():
        unknown = sorted(set(block) - FORM_KEYS)
        if unknown:
            problems.append(
                f"{code}: unknown key(s) {', '.join(unknown)}. Allowed keys:"
                f" {', '.join(sorted(FORM_KEYS))}"
            )
        raw_names = [
            column.get("raw_name")
            for column in block.get("column_details") or []
            if isinstance(column, dict)
        ]
        repeated = sorted({name for name in raw_names if raw_names.count(name) > 1})
        if repeated:
            problems.append(
                f"{code}: raw_name repeated in column_details: {', '.join(map(str, repeated))}"
            )
    is_meta = bool(form_config.get("meta"))
    table_name = form_config.get("table_name")
    schema = _data_schema().schema
    if table_name is None and not is_meta:
        problems.append("table_name is required (one of " + ", ".join(schema) + ")")
    elif table_name is not None and table_name not in schema:
        problems.append(
            f"table_name {table_name!r} is not a table in table.yaml (one of"
            f" {', '.join(schema)})"
        )
    if form_config.get("path_pattern") is None and not is_meta:
        problems.append(
            "path_pattern is required: the regex that picks the form's raw files"
        )
    else:
        _check_regex(form_config.get("path_pattern"), "path_pattern", problems)
    _check_regex(
        form_config.get("year_filter_filepath_regex"),
        "year_filter_filepath_regex",
        problems,
    )
    if not isinstance(form_config.get("read_csv_params", {}), dict):
        problems.append(
            "read_csv_params must be a mapping of read_csv keyword arguments"
        )
    if not isinstance(form_config.get("include_column_order", True), bool):
        problems.append("include_column_order must be true or false")

    # resolve_inheritance leaves merged column_details as a dict view
    columns = list(form_config.get("column_details") or [])
    if not all(isinstance(c, dict) for c in columns):
        problems.append("column_details must be a list of mappings")
        columns = []
    column_order = form_config.get("column_order") or [
        c.get("raw_name") for c in columns
    ]
    raw_names = {c.get("raw_name") for c in columns}
    file_columns = raw_names | set(column_order)
    standard_names = {}
    for column in columns:
        raw_name = column.get("raw_name")
        unknown = sorted(set(column) - COLUMN_KEYS)
        if unknown:
            problems.append(
                f"column {raw_name!r}: unknown key(s) {', '.join(unknown)}. Allowed:"
                f" {', '.join(sorted(COLUMN_KEYS))}"
            )
        if raw_name is None or "type" not in column:
            problems.append(f"column {column}: needs raw_name and type")
            continue
        try:
            pd.api.types.pandas_dtype(column["type"])
        except TypeError:
            problems.append(
                f"column {raw_name!r}: type {column['type']!r} is not a pandas dtype"
                " (str, Int64, Float64, ...)"
            )
        standard_name = column.get("standard_name")
        if standard_name is None:
            for key in _KEYS_NEEDING_STANDARD_NAME:
                if key in column:
                    problems.append(
                        f"column {raw_name!r}: {key} needs a standard_name; a column"
                        " without one is dropped"
                    )
            continue
        standard_names[raw_name] = standard_name
        _check_name(table_name, standard_name, f"column {raw_name!r}", problems)
    if form_config.get("include_column_order", True):
        read_names = [standard_names[r] for r in column_order if r in standard_names]
        for name in sorted({n for n in read_names if read_names.count(n) > 1}):
            raws = [r for r in column_order if standard_names.get(r) == name]
            problems.append(
                f"standard_name {name!r} is given to {len(raws)} columns the form reads"
                f" ({', '.join(map(repr, raws))}); the table would get two columns"
                " with one name"
            )

    outputs = set(standard_names.values())
    for key in ("state_code_columns", "new_empty_columns"):
        names = form_config.get(key) or []
        if not isinstance(names, list):
            problems.append(f"{key} must be a list of standard names")
            continue
        for name in names:
            _check_name(table_name, name, key, problems)
        outputs.update(names)
    reported_by = form_config.get("reported_by")
    if reported_by is not None:
        if reported_by not in REPORTED_BY_VALUES:
            problems.append(
                f"reported_by must be one of {', '.join(REPORTED_BY_VALUES)}, not"
                f" {reported_by!r}"
            )
        elif table_name != "Transaction":
            problems.append(
                f"reported_by only applies to Transaction forms, not {table_name}"
            )
        outputs.add("reported_by")
    duplicates = form_config.get("duplicate_columns") or {}
    if not isinstance(duplicates, dict):
        problems.append("duplicate_columns must map a standard name to a list of names")
        duplicates = {}
    for base, copies in duplicates.items():
        if base not in outputs:
            problems.append(
                f"duplicate_columns: {base!r} is not a column this form produces"
                f" (one of {_listed(outputs)})"
            )
        if not isinstance(copies, list):
            problems.append(f"duplicate_columns: {base!r} must map to a list of names")
            continue
        for copy in copies:
            _check_name(table_name, copy, f"duplicate_columns {base!r}", problems)
        outputs.update(copies)

    enum_mapper = form_config.get("enum_mapper") or {}
    if not isinstance(enum_mapper, dict):
        problems.append("enum_mapper must map standard names to mappings of values")
        enum_mapper = {}
    for name, mapping in enum_mapper.items():
        if name not in outputs:
            problems.append(
                f"enum_mapper: {name!r} is not a column this form produces, so the"
                f" mapping is never applied (columns: {_listed(outputs)})"
            )
            continue
        if not isinstance(mapping, dict):
            problems.append(
                f"enum_mapper: {name!r} must map raw values to standard values"
            )
            continue
        allowed = _allowed_values(table_name, name)
        if allowed is None:
            continue
        bad = sorted(
            {str(v) for v in mapping.values() if v is not None and v not in allowed}
        )
        if bad:
            problems.append(
                f"enum_mapper: {name!r} maps to {', '.join(bad)}, not among its values"
                f" in table.yaml: {', '.join(allowed)}"
            )
    if TRANSACTION_DIRECTION in outputs and TRANSACTION_DIRECTION not in enum_mapper:
        problems.append(
            f"{TRANSACTION_DIRECTION} needs an enum_mapper entry mapping the raw values"
            " of reversed rows to 'reverse'"
        )
    null_values = form_config.get("null_values") or {}
    if not isinstance(null_values, dict):
        problems.append("null_values must map standard names to lists of values")
        null_values = {}
    for name, values in null_values.items():
        if name not in outputs:
            problems.append(
                f"null_values: {name!r} is not a column this form produces"
                f" (columns: {_listed(outputs)})"
            )
        if not isinstance(values, list):
            problems.append(f"null_values: {name!r} must map to a list of values")

    filters = form_config.get("filter") or {}
    if not isinstance(filters, dict):
        problems.append(
            "filter must map raw column names to values to keep, or NOT: [values]"
        )
        filters = {}
    for raw_name, values in filters.items():
        if raw_name not in file_columns:
            problems.append(
                f"filter: {raw_name!r} is not a raw column of this form (raw names:"
                f" {_listed(file_columns)})"
            )
        if isinstance(values, dict):
            if set(values) != {"NOT"} or not isinstance(values["NOT"], list):
                problems.append(
                    f"filter: {raw_name!r} must be a list, or NOT: [values]"
                )
        elif not isinstance(values, list):
            problems.append(f"filter: {raw_name!r} must be a list, or NOT: [values]")
    year_column = form_config.get("year_column")
    if year_column is not None and year_column not in file_columns:
        problems.append(f"year_column {year_column!r} is not a raw column of this form")
    overloaded = form_config.get("overloaded_columns") or {}
    if not isinstance(overloaded, dict):
        problems.append(
            "overloaded_columns must map raw column names to pattern and filter"
        )
        overloaded = {}
    for raw_name, info in overloaded.items():
        if raw_name not in file_columns:
            problems.append(
                f"overloaded_columns: {raw_name!r} is not a raw column of this form"
            )
        if not isinstance(info, dict) or "pattern" not in info:
            problems.append(f"overloaded_columns: {raw_name!r} needs a pattern")
            continue
        try:
            groups = re.compile(info["pattern"]).groupindex
        except re.error as error:
            problems.append(
                f"overloaded_columns: {raw_name!r} pattern does not compile: {error}"
            )
            continue
        for group in sorted(set(groups) - raw_names):
            problems.append(
                f"overloaded_columns: {raw_name!r} extracts {group!r}, which is not in"
                " column_details, so it is dropped"
            )
        for filter_column in info.get("filter") or {}:
            if filter_column not in file_columns:
                problems.append(
                    f"overloaded_columns: {raw_name!r} filters on {filter_column!r},"
                    " not a raw column of this form"
                )
    if problems:
        raise ConfigError(
            f"{config_file_path.name}, form {form_code!r}:\n  " + "\n  ".join(problems)
        )


def _check_name(table_name: str | None, name: str, where: str, problems: list) -> None:
    """Add a problem if name doesn't resolve against the form's table"""
    if table_name is None:
        return
    try:
        resolve_standard_name(table_name, name)
    except ConfigError as error:
        problems.append(f"{where}: {error}")


def _check_regex(pattern: str | None, key: str, problems: list) -> None:
    """Add a problem if a config regex doesn't compile"""
    if pattern is None:
        return
    try:
        re.compile(pattern)
    except (re.error, TypeError) as error:
        problems.append(f"{key} {pattern!r} is not a valid regex: {error}")


def _allowed_values(table_name: str | None, name: str) -> list[str] | None:
    """The schema's values for an enum column, or None if it is not an enum"""
    if table_name is None:
        return None
    if name == TRANSACTION_DIRECTION:
        return list(TRANSACTION_DIRECTION_VALUES)
    try:
        table, attribute = resolve_standard_name(table_name, name)
    except ConfigError:
        return None
    return _data_schema().schema[table].enum_columns.get(attribute)


def _listed(names: set[str]) -> str:
    """Names in a stable order for a message"""
    return ", ".join(sorted(names)) or "none"
