"""Run Splink inference to find records that refer to the same transactor

Linkage never changes the source tables. Each run is recorded in append-only
tables, so every merge can be checked or undone:

- linkage_run: one row per run, with a run number that counts up, when it
  ran, the threshold, the model file's hash, and the splink version and git
  commit that produced it
- linkage_prediction: every pair of records the model scored at or above the
  threshold, with its match probability and weight
- linkage_mapping_history: for every record in the run, the canonical id of
  the cluster it belongs to (a record in no cluster maps to itself), and how
  it was linked

Two methods link records:

- splink: the trained model, which compares individuals by name, address and
  employer
- organization_name: organizations the model can't compare (they have no
  first name to block on) are linked when they are in the same state and
  their names match once case, punctuation and legal endings like "Inc" or
  "LLC" are ignored (see ORGANIZATION_NAME_KEY_SQL)

Records linked by either method are clustered together, so a record that a
Splink match and a name match both touch ends up in one cluster, and every
canonical id is its own canonical id. A record's method is organization_name
if it is in a group of matching names, splink if only the model linked it,
and NULL if it is the canonical record itself.

On top of these:

- the view linkage_mapping holds the latest run's mapping (old_id,
  canonical_id), under the name the mapping table used to have. A table of
  that name written by the old in-place linkage is renamed
  linkage_mapping_legacy the first time the new linkage runs
- for each table that refers to Transactor ids, a view <table>_canonical has
  the same rows with those ids replaced by their canonical id.
  Transactor_canonical keeps one row per canonical id (the canonical record's
  own row), so joining it on id doesn't repeat rows; the other members'
  rows are reachable through linkage_mapping
"""

import json
import uuid
from importlib import metadata
from pathlib import Path

import duckdb
from splink import DuckDBAPI, Linker

from utils.database import connect_duckdb, table_exists
from utils.ids import get_all_id_references
from utils.provenance import git_state, sha256_file, utc_now

LINKAGE_RUN_TABLE = "linkage_run"
LINKAGE_PREDICTION_TABLE = "linkage_prediction"
LINKAGE_MAPPING_HISTORY_TABLE = "linkage_mapping_history"
# a view of the latest run's mapping, under the old mapping table's name
LINKAGE_MAPPING_TABLE = "linkage_mapping"
# where a linkage_mapping table written by the old in-place linkage is moved
LINKAGE_MAPPING_LEGACY_TABLE = "linkage_mapping_legacy"
CANONICAL_VIEW_SUFFIX = "_canonical"
# Splink's working tables, dropped once a run has been recorded
_SPLINK_TABLE_PREFIX = "__splink__"

# started_at and finished_at are UTC, like the times in _run_manifest.json.
# They are plain TIMESTAMPs because reading a TIMESTAMPTZ into Python needs
# pytz, which isn't installed on Python 3.11 and later.
_CREATE_LINKAGE_TABLES = f"""
    CREATE SEQUENCE IF NOT EXISTS {LINKAGE_RUN_TABLE}_number;
    CREATE TABLE IF NOT EXISTS {LINKAGE_RUN_TABLE} (
        run_id VARCHAR PRIMARY KEY,
        -- counts up with each run; the latest run is the highest number
        -- (started_at is only to the second, so two runs can share one)
        run_number BIGINT DEFAULT nextval('{LINKAGE_RUN_TABLE}_number'),
        started_at TIMESTAMP,
        finished_at TIMESTAMP,
        table_name VARCHAR,
        threshold DOUBLE,
        model_path VARCHAR,
        model_sha256 VARCHAR,
        splink_version VARCHAR,
        git_commit VARCHAR,
        git_dirty BOOLEAN
    );
    CREATE TABLE IF NOT EXISTS {LINKAGE_PREDICTION_TABLE} (
        run_id VARCHAR,
        id_l VARCHAR,
        id_r VARCHAR,
        match_probability DOUBLE,
        match_weight DOUBLE
    );
    CREATE TABLE IF NOT EXISTS {LINKAGE_MAPPING_HISTORY_TABLE} (
        run_id VARCHAR,
        old_id VARCHAR,
        canonical_id VARCHAR,
        -- how old_id was linked to canonical_id: 'splink' or
        -- 'organization_name', or NULL if it is the canonical record itself
        method VARCHAR
    );
"""

# Words dropped from organization names before comparing them, so "Acme Inc"
# and "ACME, Inc." match
ORGANIZATION_NAME_STOP_WORDS = (
    "the",
    "inc",
    "incorporated",
    "llc",
    "llp",
    "ltd",
    "co",
    "corp",
    "corporation",
    "company",
)
# SQL giving an organization's comparison key from full_name: lowercased,
# punctuation replaced by spaces (letters in any alphabet and digits kept),
# stop words removed, and spaces collapsed
ORGANIZATION_NAME_KEY_SQL = (
    "trim(regexp_replace(regexp_replace(regexp_replace(lower(full_name),"
    " '[^\\pL\\pN]+', ' ', 'g'),"
    f" '\\b({'|'.join(ORGANIZATION_NAME_STOP_WORDS)})\\b', ' ', 'g'),"
    " '\\s+', ' ', 'g'))"
)
_ORGANIZATION_COLUMNS = {"id", "full_name", "transactor_type", "reported_state"}

# Splink helpers


def load_linker(
    con: duckdb.DuckDBPyConnection,
    settings_path: Path | str,
    table_name: str,
) -> Linker:
    """Instantiate a Splink :class:`~splink.Linker` from a saved settings JSON.

    Args:
        con: An active DuckDB connection.
        settings_path: Path to the JSON created by
            :py:meth:`splink.Linker.misc.save_model_to_json` during training.
        table_name: Name of the table/view containing the records to be deduplicated.

    Returns:
        A ready-to-use :class:`splink.Linker` instance.
    """
    settings_path = Path(settings_path)
    with settings_path.open() as fp:
        settings = json.load(fp)

    db_api = DuckDBAPI(con)
    return Linker(table_name, settings, db_api=db_api)


def predict_and_cluster(
    linker: Linker,
    *,
    threshold_match_probability: float,
) -> tuple[str, str]:
    """Score record pairs and group them into clusters

    Args:
        linker: A configured Splink ``Linker``.
        threshold_match_probability: Pairs with a match probability **>=** this
            value are kept and used to form clusters.

    Returns:
        The names of the DuckDB tables Splink wrote the predictions (id_l,
        id_r, match_probability, match_weight, ...) and the clusters
        (cluster_id, id, ...) to. The cluster id is the smallest id in the
        cluster.
    """
    predictions = linker.inference.predict(
        threshold_match_probability=threshold_match_probability
    )
    clusters = linker.clustering.cluster_pairwise_predictions_at_threshold(
        predictions, threshold_match_probability=threshold_match_probability
    )
    return predictions.physical_name, clusters.physical_name


# Recording runs


def create_linkage_tables(con: duckdb.DuckDBPyConnection) -> None:
    """Create the linkage run, prediction and mapping history tables if missing

    A linkage_mapping table left by the old in-place linkage is renamed
    LINKAGE_MAPPING_LEGACY_TABLE, since linkage_mapping is now a view.

    Args:
        con: DuckDB connection.
    """
    mapping_type = con.execute(
        "SELECT table_type FROM information_schema.tables WHERE table_name = ?",
        [LINKAGE_MAPPING_TABLE],
    ).fetchone()
    if mapping_type is not None and mapping_type[0] == "BASE TABLE":
        con.execute(
            f"ALTER TABLE {LINKAGE_MAPPING_TABLE}"
            f" RENAME TO {LINKAGE_MAPPING_LEGACY_TABLE}"
        )
        print(
            f"Renamed the {LINKAGE_MAPPING_TABLE} table from an earlier linkage"
            f" to {LINKAGE_MAPPING_LEGACY_TABLE}; {LINKAGE_MAPPING_TABLE} is now"
            " a view of the latest run."
        )
    con.execute(_CREATE_LINKAGE_TABLES)


def create_canonical_views(con: duckdb.DuckDBPyConnection) -> None:
    """Create the views that expose the latest run's canonical ids

    linkage_mapping holds the latest run's (old_id, canonical_id) pairs. For
    each table that refers to Transactor ids, <table>_canonical has the same
    rows, with each of those id columns replaced by its canonical id. The
    table whose own id is replaced (Transactor) keeps only the canonical
    records' rows, so the view has one row per canonical id.

    Every table's canonical view is dropped first, so a view over a table
    that is no longer in the database (after loading fewer states) doesn't
    stay behind.

    Args:
        con: DuckDB connection that has the linkage tables.
    """
    con.execute(f"""
        CREATE OR REPLACE VIEW {LINKAGE_MAPPING_TABLE} AS
        SELECT old_id, canonical_id
        FROM {LINKAGE_MAPPING_HISTORY_TABLE}
        WHERE run_id = (
            SELECT run_id FROM {LINKAGE_RUN_TABLE}
            ORDER BY run_number DESC
            LIMIT 1
        )
    """)
    id_references = get_all_id_references("Transactor")
    for table_name in id_references:
        con.execute(f'DROP VIEW IF EXISTS "{table_name}{CANONICAL_VIEW_SUFFIX}"')
    for table_name, id_columns in id_references.items():
        if not id_columns or not table_exists(con, table_name):
            continue
        columns = [
            row[1]
            for row in con.execute(f"PRAGMA table_info('{table_name}')").fetchall()
        ]
        id_columns = [column for column in id_columns if column in columns]
        select_list = ",\n            ".join(
            f'COALESCE(m_{column}.canonical_id, t."{column}") AS "{column}"'
            if column in id_columns
            else f't."{column}"'
            for column in columns
        )
        joins = "\n        ".join(
            f"LEFT JOIN {LINKAGE_MAPPING_TABLE} AS m_{column}"
            f' ON t."{column}" = m_{column}.old_id'
            for column in id_columns
        )
        # one row per canonical id: a merged record's own row is left out
        canonical_rows_only = (
            'WHERE m_id.canonical_id IS NULL OR m_id.canonical_id = t."id"'
            if "id" in id_columns
            else ""
        )
        con.execute(f"""
            CREATE VIEW "{table_name}{CANONICAL_VIEW_SUFFIX}" AS
            SELECT
                {select_list}
            FROM "{table_name}" AS t
            {joins}
            {canonical_rows_only}
        """)


def _organization_groups_sql(con: duckdb.DuckDBPyConnection, table_name: str) -> str:
    """Build SQL grouping organizations by state and comparison key

    Args:
        con: DuckDB connection.
        table_name: Table linkage ran on.

    Returns:
        A query with id, org_canonical_id (the smallest id in its group) and
        group_size, for organizations with a non-empty key. It has no rows if
        the table lacks full_name, transactor_type or reported_state.
    """
    columns = {
        row[1] for row in con.execute(f"PRAGMA table_info('{table_name}')").fetchall()
    }
    if not _ORGANIZATION_COLUMNS <= columns:
        return (
            "SELECT NULL::VARCHAR AS id, NULL::VARCHAR AS org_canonical_id,"
            " NULL::BIGINT AS group_size WHERE false"
        )
    return f"""
        SELECT
            id,
            min(id) OVER (PARTITION BY state, name_key) AS org_canonical_id,
            count(*) OVER (PARTITION BY state, name_key) AS group_size
        FROM (
            SELECT
                CAST(id AS VARCHAR) AS id,
                lower(reported_state) AS state,
                {ORGANIZATION_NAME_KEY_SQL} AS name_key
            FROM "{table_name}"
            WHERE transactor_type = 'Organization' AND full_name IS NOT NULL
        ) keyed
        WHERE name_key <> ''
    """


def record_linkage_run(
    con: duckdb.DuckDBPyConnection,
    *,
    predictions_table: str,
    clusters_table: str,
    run: dict,
) -> None:
    """Save one run's predictions and mapping, all or nothing

    Args:
        con: DuckDB connection with Splink's prediction and cluster tables.
        predictions_table: Table Splink wrote the predictions to.
        clusters_table: Table Splink wrote the clusters to.
        run: The linkage_run row, keyed by column name.
    """
    create_linkage_tables(con)
    con.execute("BEGIN TRANSACTION")
    try:
        columns = ", ".join(run)
        placeholders = ", ".join("?" for _ in run)
        con.execute(
            f"INSERT INTO {LINKAGE_RUN_TABLE} ({columns}) VALUES ({placeholders})",
            list(run.values()),
        )
        con.execute(
            f"""
            INSERT INTO {LINKAGE_PREDICTION_TABLE}
            SELECT ?, CAST(id_l AS VARCHAR), CAST(id_r AS VARCHAR),
                match_probability, match_weight
            FROM {predictions_table}
            """,
            [run["run_id"]],
        )
        labels_table = _cluster_both_methods(con, clusters_table, run["table_name"])
        con.execute(
            f"""
            INSERT INTO {LINKAGE_MAPPING_HISTORY_TABLE}
            SELECT
                ?,
                id,
                label,
                CASE
                    WHEN label = id THEN NULL
                    WHEN name_group <> id THEN 'organization_name'
                    ELSE 'splink'
                END
            FROM {labels_table}
            """,
            [run["run_id"]],
        )
        con.execute(f"DROP TABLE {labels_table}")
        create_canonical_views(con)
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


def _cluster_both_methods(
    con: duckdb.DuckDBPyConnection, clusters_table: str, table_name: str
) -> str:
    """Put records linked by Splink or by organization name in one cluster

    Each record starts with the smaller of its Splink cluster id and its name
    group's id. Labels then spread: every record takes the smallest label in
    its Splink cluster and in its name group, until nothing changes. Records
    joined by any chain of either kind of link end with the same label, the
    smallest id among them, so a canonical id is always its own canonical id.

    Args:
        con: DuckDB connection with Splink's cluster table.
        clusters_table: Table Splink wrote the clusters to (id, cluster_id).
        table_name: Table linkage ran on, for the organization names.

    Returns:
        The name of a temporary table with id, cluster_id, name_group (the
        group's smallest id, or the record's own id when it is in no group)
        and label (the canonical id).
    """
    labels, next_labels = "_linkage_labels", "_linkage_labels_next"
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE {labels} AS
        WITH clusters AS (
            SELECT DISTINCT
                CAST(id AS VARCHAR) AS id,
                CAST(cluster_id AS VARCHAR) AS cluster_id
            FROM {clusters_table}
        ),
        organizations AS ({_organization_groups_sql(con, table_name)})
        SELECT
            c.id,
            c.cluster_id,
            CASE WHEN o.group_size > 1 THEN o.org_canonical_id ELSE c.id END
                AS name_group,
            least(
                c.cluster_id,
                CASE WHEN o.group_size > 1 THEN o.org_canonical_id ELSE c.id END
            ) AS label
        FROM clusters c
        LEFT JOIN organizations o USING (id)
        """
    )
    while True:
        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE {next_labels} AS
            SELECT
                id,
                cluster_id,
                name_group,
                least(
                    min(label) OVER (PARTITION BY cluster_id),
                    min(label) OVER (PARTITION BY name_group)
                ) AS label
            FROM {labels}
            """
        )
        (changed,) = con.execute(
            f"""
            SELECT count(*)
            FROM {labels} AS before
            JOIN {next_labels} AS after USING (id)
            WHERE before.label <> after.label
            """
        ).fetchone()
        labels, next_labels = next_labels, labels
        if changed == 0:
            con.execute(f"DROP TABLE {next_labels}")
            return labels


def drop_splink_tables(con: duckdb.DuckDBPyConnection) -> None:
    """Drop the working tables Splink leaves in the database

    Args:
        con: DuckDB connection.
    """
    for (table_name,) in con.execute(
        "SELECT table_name FROM information_schema.tables"
        " WHERE starts_with(table_name, ?)",
        [_SPLINK_TABLE_PREFIX],
    ).fetchall():
        con.execute(f'DROP TABLE IF EXISTS "{table_name}"')


def _splink_version() -> str | None:
    """Look up the installed splink version

    Returns:
        The version, or None if splink isn't installed as a package.
    """
    try:
        return metadata.version("splink")
    except metadata.PackageNotFoundError:
        return None


def run_linkage_pipeline(
    *,
    duckdb_path: Path | str,
    model_path: Path | str,
    threshold: float = 0.95,
    table_name: str = "transactor_detailed_view",
) -> str:
    """Run linkage and record it, without changing the source tables

    Args:
        duckdb_path: DuckDB database file. Must have 'table_name' as a table.
        model_path: Path to the trained Splink settings JSON.
        threshold: Match-probability threshold used when forming clusters.
        table_name: Table to perform linkage on. Default is transactor_detailed_view

    Returns:
        The new run's run_id.

    Raises:
        ValueError: if table_name doesn't exist.
    """
    con = connect_duckdb(duckdb_path)
    try:
        if not table_exists(con, table_name):
            raise ValueError(f"{table_name} does not exist")
        started_at = utc_now()
        linker = load_linker(con, model_path, table_name)
        predictions_table, clusters_table = predict_and_cluster(
            linker, threshold_match_probability=threshold
        )
        git = git_state()
        run = {
            "run_id": str(uuid.uuid4()),
            "started_at": started_at,
            "finished_at": utc_now(),
            "table_name": table_name,
            "threshold": threshold,
            "model_path": str(model_path),
            "model_sha256": sha256_file(Path(model_path)),
            "splink_version": _splink_version(),
            "git_commit": git["commit"],
            "git_dirty": git["dirty"],
        }
        record_linkage_run(
            con,
            predictions_table=predictions_table,
            clusters_table=clusters_table,
            run=run,
        )
        drop_splink_tables(con)
    finally:
        con.close()
    return run["run_id"]
