"""Run Splink inference to find records that refer to the same transactor

Linkage never changes the source tables. Each run is recorded in append-only
tables, so every merge can be checked or undone:

- linkage_run: one row per run, with when it ran, the threshold, the model
  file's hash, and the splink version and git commit that produced it
- linkage_prediction: every pair of records the model scored at or above the
  threshold, with its match probability and weight
- linkage_mapping_history: for every record in the run, the canonical id of
  the cluster it belongs to (a record in no cluster maps to itself)

On top of these:

- the view linkage_mapping holds the latest run's mapping (old_id,
  canonical_id), under the name the mapping table used to have
- for each table that refers to Transactor ids, a view <table>_canonical has
  the same rows with those ids replaced by their canonical id
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
CANONICAL_VIEW_SUFFIX = "_canonical"
# Splink's working tables, dropped once a run has been recorded
_SPLINK_TABLE_PREFIX = "__splink__"

_CREATE_LINKAGE_TABLES = f"""
    CREATE TABLE IF NOT EXISTS {LINKAGE_RUN_TABLE} (
        run_id VARCHAR PRIMARY KEY,
        started_at TIMESTAMPTZ,
        finished_at TIMESTAMPTZ,
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
        -- how old_id was linked: 'splink', or NULL if it is its own cluster
        method VARCHAR
    );
"""

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

    Args:
        con: DuckDB connection.
    """
    con.execute(_CREATE_LINKAGE_TABLES)


def create_canonical_views(con: duckdb.DuckDBPyConnection) -> None:
    """Create the views that expose the latest run's canonical ids

    linkage_mapping holds the latest run's (old_id, canonical_id) pairs. For
    each table that refers to Transactor ids, <table>_canonical has the same
    rows, with each of those id columns replaced by its canonical id.

    Args:
        con: DuckDB connection that has the linkage tables.
    """
    con.execute(f"""
        CREATE OR REPLACE VIEW {LINKAGE_MAPPING_TABLE} AS
        SELECT old_id, canonical_id
        FROM {LINKAGE_MAPPING_HISTORY_TABLE}
        WHERE run_id = (
            SELECT run_id FROM {LINKAGE_RUN_TABLE}
            ORDER BY started_at DESC, run_id DESC
            LIMIT 1
        )
    """)
    for table_name, id_columns in get_all_id_references("Transactor").items():
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
        con.execute(f"""
            CREATE OR REPLACE VIEW "{table_name}{CANONICAL_VIEW_SUFFIX}" AS
            SELECT
                {select_list}
            FROM "{table_name}" AS t
            {joins}
        """)


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
        con.execute(
            f"""
            INSERT INTO {LINKAGE_MAPPING_HISTORY_TABLE}
            SELECT DISTINCT ?, CAST(id AS VARCHAR), CAST(cluster_id AS VARCHAR),
                CASE WHEN CAST(cluster_id AS VARCHAR) <> CAST(id AS VARCHAR)
                    THEN 'splink' END
            FROM {clusters_table}
            """,
            [run["run_id"]],
        )
        create_canonical_views(con)
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


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
