"""Database utilities."""

from pathlib import Path
from textwrap import dedent

import duckdb

from utils.constants import DEFAULT_SCHEMA_PATH
from utils.schema import DataSchema, TableSchema, parse_decimal_type


def _get_data_schema() -> DataSchema:
    return DataSchema(DEFAULT_SCHEMA_PATH)


# Mapping from YAML type strings to DuckDB SQL types
_TYPE_MAP: dict[str, str] = {
    "string": "VARCHAR",
    "str": "VARCHAR",
    "Int8": "TINYINT",
    "Int16": "SMALLINT",
    "Int32": "INTEGER",
    "Int64": "BIGINT",
    "Float32": "REAL",
    "Float64": "DOUBLE",
    "bool": "BOOLEAN",
    "boolean": "BOOLEAN",
    "datetime64[ns]": "TIMESTAMP",
    "date": "DATE",
}


def _duckdb_type(schema_type: str) -> str:
    """Convert a YAML schema type string to a DuckDB SQL type.

    "decimal(p,s)" becomes DECIMAL(p,s). Unrecognised types default to VARCHAR.
    """
    decimal_type = parse_decimal_type(schema_type)
    if decimal_type is not None:
        precision, scale = decimal_type
        return f"DECIMAL({precision},{scale})"
    return _TYPE_MAP.get(schema_type, "VARCHAR")


def connect_duckdb(database_path: Path | str) -> duckdb.DuckDBPyConnection:
    """Return a DuckDB connection to *database_path*.

    The database file will be created if it does not already exist.

    Args:
        database_path: File path to the DuckDB database.

    Returns:
        An open :class:`duckdb.DuckDBPyConnection` instance.
    """
    database_path = Path(database_path)
    return duckdb.connect(str(database_path))


def create_table_from_parquet_to_db(
    con: duckdb.DuckDBPyConnection, table_path: Path
) -> None:
    """Load a table from a parquet file into a DuckDB database."""
    con.execute(
        f"CREATE TABLE IF NOT EXISTS {table_path.stem} AS "
        f"SELECT * FROM read_parquet('{str(table_path.resolve())}')"
    )


def create_database_from_parquet(
    database_path: Path, database_dir: Path
) -> duckdb.DuckDBPyConnection:
    """Create a DuckDB database from a directory of parquet files."""
    con = duckdb.connect(str(database_path))
    for file in database_dir.glob("*.parquet"):
        create_table_from_parquet_to_db(con, file)
    return con


def create_or_append_parquet_to_db(
    con: duckdb.DuckDBPyConnection, table_path: Path
) -> None:
    """Create or append a table from a parquet file to a DuckDB database.

    If the table with name table_path.stem, already exists, the data in
    the parquet file located at table_path will be appended. Otherwise,
    a new table will be created.

    Args:
        con: DuckDB connection
        table_path: Path to the parquet file to create or append
    Modifies:
        DuckDB database will now have a table 'table_path.stem' containing
        data from the parquet file.
    """
    table_exists = (
        con.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
            [table_path.stem],
        )
        .fetchdf()
        .iloc[0, 0]
        > 0
    )
    if table_exists:
        # Ensure we insert columns in the same order as the existing table to avoid
        # positional mismatches (DuckDB maps INSERT values positionally).
        # Fetch column names from the existing DuckDB table.
        table_columns = [
            col[1]
            for col in con.execute(f"PRAGMA table_info('{table_path.stem}')").fetchall()
        ]

        # Build a comma-separated column list
        column_list = ", ".join(table_columns)

        # Insert data selecting the same columns from the parquet file, allowing
        # DuckDB to match by name rather than position.
        con.execute(
            f"INSERT INTO {table_path.stem} ({column_list}) "
            f"SELECT {_parquet_select_list(con, table_path, table_columns)} "
            f"FROM read_parquet('{str(table_path.resolve())}')"
        )
    else:
        # Create the table explicitly using the YAML schema definitions so column
        # types are consistent regardless of the first Parquet file encountered.

        table_schema = create_schema_table(con, table_path.stem)

        # Insert data from the Parquet file into the table, aligning by column
        # names present in the YAML schema.
        column_list = ", ".join(table_schema.types.keys())
        select_list = _parquet_select_list(
            con, table_path, list(table_schema.types.keys())
        )
        con.execute(
            f"INSERT INTO {table_path.stem} ({column_list}) "
            f"SELECT {select_list} FROM read_parquet('{str(table_path.resolve())}')"
        )


def create_schema_table(con: duckdb.DuckDBPyConnection, table_name: str) -> TableSchema:
    """Create an empty table with the columns and types the schema gives it

    Args:
        con: DuckDB connection.
        table_name: A table in the YAML schema.

    Returns:
        The table's schema.

    Raises:
        KeyError: if the schema has no such table.
    """
    try:
        table_schema = _get_data_schema().schema[table_name]
    except KeyError as exc:
        raise KeyError(
            f"Table '{table_name}' not found in YAML schema located at {DEFAULT_SCHEMA_PATH}."
        ) from exc
    column_defs = ", ".join(
        f"{col} {_duckdb_type(dtype)}" for col, dtype in table_schema.types.items()
    )
    con.execute(f"CREATE TABLE {table_name} ({column_defs})")
    return table_schema


def _parquet_select_list(
    con: duckdb.DuckDBPyConnection, table_path: Path, columns: list[str]
) -> str:
    """Select columns from a parquet file, with NULL for any it lacks

    Files written before a column was added to the schema (for example the
    Transaction source columns) don't have it. Filling it with NULL loads
    those files instead of failing on the missing column.

    Args:
        con: DuckDB connection.
        table_path: Parquet file to read.
        columns: Columns to select, in order.

    Returns:
        A comma-separated SELECT list.
    """
    parquet_columns = {
        row[0]
        for row in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{str(table_path.resolve())}')"
        ).fetchall()
    }
    missing = [column for column in columns if column not in parquet_columns]
    if missing:
        print(
            f"{table_path} has no {', '.join(missing)} column(s); they are loaded"
            " as NULL. Re-run the pipeline from standardize to fill them."
        )
    return ", ".join(
        column if column in parquet_columns else f"NULL AS {column}"
        for column in columns
    )


DETAILED_VIEW_TABLE = "transactor_detailed_view"
# Tables the detailed view is built from. A state without one (TX has no
# Membership rows) still gets an empty table, so the view can be built.
DETAILED_VIEW_SOURCE_TABLES = ("Transactor", "Membership", "Address")


def _drop_relations(con: duckdb.DuckDBPyConnection, names: set[str] | None) -> None:
    """Drop tables and views by name

    Args:
        con: DuckDB connection.
        names: Names to drop, or None to drop every table and view.
    """
    relations = con.execute(
        "SELECT table_name, table_type FROM information_schema.tables"
    ).fetchall()
    # views first, since they can depend on tables
    for name, table_type in sorted(relations, key=lambda row: row[1] != "VIEW"):
        if names is not None and name not in names:
            continue
        kind = "VIEW" if table_type == "VIEW" else "TABLE"
        con.execute(f'DROP {kind} IF EXISTS "{name}"')


def create_database_from_nested_parquet_directories(
    database_path: Path,
    database_dir: Path,
    overwrite: bool = False,
    states: list[str] | None = None,
) -> duckdb.DuckDBPyConnection:
    """Load the cleaned tables of each state into a DuckDB database

    The source tables (every table in the schema, and the detailed view
    table built from them) are dropped and reloaded, so the database always
    matches the cleaned data. Other tables, such as the linkage run history,
    are kept unless overwrite is True.

    database_dir holds one folder per state, each with one parquet file per
    table. Example:
    database_dir/
    ├── state1/
    │   ├── table1.parquet
    │   ├── table2.parquet
    ├── state2/
    │   ├── table3.parquet
    │   ├── table4.parquet

    Args:
        database_path: Path to the DuckDB database
        database_dir: Path to the directory containing the parquet files
        overwrite: If True, drop every table and view first, including the
            linkage history
        states: State folders to load (compared ignoring case). None loads
            every state folder.

    Returns:
        DuckDB connection

    Raises:
        RuntimeError: if any table file fails to load. Every failure is
            listed, after all the other files have been tried.
    """
    con = duckdb.connect(str(database_path))
    source_tables = set(_get_data_schema().schema) | {DETAILED_VIEW_TABLE}
    _drop_relations(con, None if overwrite else source_tables)
    wanted_states = {state.lower() for state in states} if states else None
    failures = []
    for state_dir in sorted(database_dir.iterdir()):
        # skip files and hidden folders, such as a staging folder left by a
        # step that was killed (see utils.io.staged_directory)
        if not state_dir.is_dir() or state_dir.name.startswith("."):
            continue
        if wanted_states is not None and state_dir.name.lower() not in wanted_states:
            continue
        for file in sorted(state_dir.glob("*.parquet")):
            try:
                create_or_append_parquet_to_db(con, file)
            except Exception as error:  # noqa: BLE001 (all reported below)
                failures.append(f"{state_dir.name}/{file.name}: {error}")
    for table_name in DETAILED_VIEW_SOURCE_TABLES:
        if not table_exists(con, table_name):
            create_schema_table(con, table_name)
    if failures:
        con.close()
        raise RuntimeError(
            f"{len(failures)} table file(s) failed to load into {database_path}:\n"
            + "\n".join(failures)
        )
    return con


# Text columns of the detailed view that linkage compares. They are
# lowercased and trimmed, so "SMITH" and "Smith " compare as equal.
LINKAGE_TEXT_COLUMNS = (
    "full_name",
    "first_name",
    "middle_name",
    "last_name",
    "name_prefix",
    "name_suffix",
    "address_city",
    "address_street_name",
    "employer_full_name",
    "employer_role",
)


def _most_common_row(key: str, source_sql: str, columns: list[str]) -> str:
    """Build SQL that picks each key's most common row, the same way every time

    Rows are counted per key and combination of columns. The most common
    combination wins; ties go to the latest latest_known_date (if it is one
    of the columns), then to the values in alphabetical order. The whole row
    comes from one combination, so a city and street are never mixed from
    two addresses.

    Args:
        key: Column to pick one row for each value of, such as transactor_id.
        source_sql: Query giving key and columns.
        columns: Columns to pick.

    Returns:
        A query with one row per key: key and columns.
    """
    if not columns:
        return f"SELECT DISTINCT {key} FROM ({source_sql}) source"
    column_list = ", ".join(columns)
    tie_breakers = ["COUNT(*) DESC"]
    if "latest_known_date" in columns:
        tie_breakers.append("latest_known_date DESC NULLS LAST")
    tie_breakers += [f"{column} ASC NULLS LAST" for column in columns]
    return dedent(f"""
        SELECT {key}, {column_list}
        FROM (
            SELECT
                {key}, {column_list},
                ROW_NUMBER() OVER (
                    PARTITION BY {key} ORDER BY {", ".join(tie_breakers)}
                ) AS row_rank
            FROM ({source_sql}) source
            GROUP BY {key}, {column_list}
        ) ranked
        WHERE row_rank = 1
    """).strip()


def create_transactor_detailed_view(con: duckdb.DuckDBPyConnection) -> None:
    """Create or replace a table of transactors with their employer and address

    Each transactor gets its most common employer (from Employee memberships)
    and its most common address, picked as in _most_common_row, with the
    employer's columns prefixed employer_ and the address's address_. The
    text columns linkage compares (LINKAGE_TEXT_COLUMNS) are lowercased and
    trimmed.

    Args:
        con: DuckDB connection with Transactor, Membership and Address tables.
    """

    def _cols(table: str, exclude: set[str]) -> list[str]:
        """Return column names for a DuckDB `table` excluding `exclude`."""
        return [
            col[1]
            for col in con.execute(f"PRAGMA table_info('{table}')").fetchall()
            if col[1] not in exclude
        ]

    transactor_columns = _cols("Transactor", {"id"})
    # a column in both (reported_state) is taken from the employer itself
    membership_columns = _cols(
        "Membership", {"member_id", "organization_id", *transactor_columns}
    )
    address_columns = _cols("Address", {"transactor_id"})
    employer_columns = membership_columns + transactor_columns

    employer_source = (
        "SELECT Membership.member_id"
        + "".join(
            [f", Membership.{column}" for column in membership_columns]
            + [f", Transactor.{column}" for column in transactor_columns]
        )
        + dedent("""
        FROM Membership
        JOIN Transactor ON Membership.organization_id = Transactor.id
        WHERE Membership.membership_type = 'Employee'
    """)
    )
    employer_subquery = _most_common_row("member_id", employer_source, employer_columns)
    address_source = (
        "SELECT transactor_id"
        + "".join(f", {column}" for column in address_columns)
        + " FROM Address"
    )
    address_subquery = _most_common_row(
        "transactor_id", address_source, address_columns
    )

    select_parts = ["t.*"]
    select_parts += [
        f"employer_data.{column} AS employer_{column}" for column in employer_columns
    ]
    select_parts += [
        f"address_data.{column} AS address_{column}" for column in address_columns
    ]
    select_list = ",\n            ".join(select_parts)
    table_name = DETAILED_VIEW_TABLE
    con.execute(f"DROP TABLE IF EXISTS {table_name}")
    con.execute(
        dedent(f"""
        CREATE TABLE {table_name} AS
        SELECT
            {select_list}
        FROM Transactor t
        LEFT JOIN ({employer_subquery}) employer_data
            ON t.id = employer_data.member_id
        LEFT JOIN ({address_subquery}) address_data
            ON t.id = address_data.transactor_id
    """)
    )
    text_columns = [
        name
        for _, name, column_type, *_ in con.execute(
            f"PRAGMA table_info('{table_name}')"
        ).fetchall()
        if name in LINKAGE_TEXT_COLUMNS and column_type == "VARCHAR"
    ]
    if text_columns:
        assignments = ", ".join(
            f"{column} = lower(trim({column}))" for column in text_columns
        )
        con.execute(f"UPDATE {table_name} SET {assignments}")


def create_transactor_details_view_from_parquet(
    database_path: Path, database_dir: Path
) -> duckdb.DuckDBPyConnection:
    """Idempotently make duckdb view of transactor details (employment and address info).

    Args:
        database_path: Path to the DuckDB database
        database_dir: Path to the directory containing the parquet files

    Returns:
        DuckDB connection
    """
    con = create_database_from_parquet(database_path, database_dir)
    create_transactor_detailed_view(con)
    return con


def table_exists(con: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    """Check whether *table_name* exists in the connected database."""
    (exists,) = con.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_name = ?
        """,
        [table_name],
    ).fetchone()
    return exists > 0
