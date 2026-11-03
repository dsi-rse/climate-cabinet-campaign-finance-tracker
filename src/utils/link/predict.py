"""Module for running Splink inference to identify possible duplicate rows

Includes functions to update a duckdb database replacing duplicate ids with shared
ids and storing the mapping of old ids to new ids in a table called linkage_mapping.
"""

import json
from pathlib import Path

import duckdb
import pandas as pd
from splink import DuckDBAPI, Linker

from utils.database import (
    connect_duckdb,
    table_exists,
)
from utils.ids import get_all_id_references

LINKAGE_MAPPING_TABLE = "linkage_mapping"

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


def cluster_transactors(
    linker: Linker,
    *,
    threshold_match_probability: float,
) -> pd.DataFrame:
    """Run clustering.

    Args:
        linker: A configured Splink ``Linker``.
        threshold_match_probability: Records with a match probability **>=** this
            value are considered links when forming clusters.

    Returns:
        A DataFrame of clusters
    """
    df_predictions = linker.inference.predict(
        threshold_match_probability=threshold_match_probability
    )

    df_clusters = linker.clustering.cluster_pairwise_predictions_at_threshold(
        df_predictions, threshold_match_probability=threshold_match_probability
    )
    return df_clusters.as_pandas_dataframe()


# ID mapping + replacement helpers


def _create_mapping_of_old_ids_to_cluster_ids(
    linkage_clusters: pd.DataFrame,
) -> pd.DataFrame:
    """Return a 2-column DataFrame mapping *old_id* ➔ *canonical_id*.

    Args:
        linkage_clusters: df with "cluster_id" and "id" columns.
            Returned by Linker.clustering.cluster_pairwise_predictions_at_threshold.
            Should have provided id column, and new 'cluster_id' which is the min
            of all ids in the cluster. All other columns are also present.
    """
    mapping = linkage_clusters.loc[:, ["cluster_id", "id"]].drop_duplicates()
    mapping = mapping.rename(columns={"id": "old_id", "cluster_id": "canonical_id"})
    return mapping


def upsert_linkage_mapping(
    con: duckdb.DuckDBPyConnection,
    linkage_clusters: pd.DataFrame,
    *,
    replace: bool = True,
) -> None:
    """Persist a mapping of *old_id* ➔ *canonical_id* to *mapping_table*.

    Args:
        con: DuckDB connection.
        linkage_clusters: DataFrame produced by :func:`predict_and_cluster`.
        mapping_table: Target table name.
        replace: Whether to overwrite an existing table. If *False*, rows are
            **appended** (duplicates may occur).
    """
    df_mapping = _create_mapping_of_old_ids_to_cluster_ids(linkage_clusters)

    con.register("_tmp_mapping", df_mapping)
    create_stmt = "CREATE OR REPLACE TABLE" if replace else "CREATE TABLE IF NOT EXISTS"
    con.execute(f"{create_stmt} {LINKAGE_MAPPING_TABLE} AS SELECT * FROM _tmp_mapping")
    con.unregister("_tmp_mapping")


def replace_ids_with_canonical(
    con: duckdb.DuckDBPyConnection,
    table_name: str,
    id_columns: list[str],
) -> None:
    """Replace *id_columns* values in *table_name* with their canonical counterpart.

    The update is performed in-place via SQL ``UPDATE`` statements.

    Args:
        con: DuckDB connection.
        table_name: Name of the table to update.
        id_columns: Columns to update.
    """
    if (
        con.execute(
            """
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_name = ?
    """,
            [table_name],
        ).fetchone()[0]
        == 0
    ):
        print(f"Table {table_name} does not exist. Skipping")
        return
    cols = [
        row[1] for row in con.execute(f"PRAGMA table_info('{table_name}')").fetchall()
    ]
    for id_column in id_columns:
        # Verify column exists to avoid run-time errors
        print(f"Replacing {id_column} in {table_name}")
        if id_column not in cols:
            continue

        con.execute(
            f"""
            UPDATE {table_name} AS t
            SET {id_column} = m.canonical_id
            FROM {LINKAGE_MAPPING_TABLE} AS m
            WHERE t.{id_column} = m.old_id
            """
        )


def run_linkage_pipeline(
    *,
    duckdb_path: Path | str,
    model_path: Path | str,
    threshold: float = 0.95,
    table_name: str = "transactor_detailed_view",
) -> None:
    """Run the full linkage pipeline end-to-end.

    Args:
        duckdb_path: DuckDB database file. Must have 'table_name' as a table.
        model_path: Path to the trained Splink settings JSON.
        threshold: Match-probability threshold used when forming clusters.
        table_name: Table to perform linkage on. Default is transactor_detailed_view
    """
    # Ensure connection to duckdb database with transactor_detailed_view
    con = connect_duckdb(duckdb_path)
    if not table_exists(con, "transactor_detailed_view"):
        raise ValueError("transactor_detailed_view does not exist")
    # Load linker and run prediction and clustering
    linker = load_linker(con, model_path, table_name)
    clusters = cluster_transactors(linker, threshold_match_probability=threshold)

    upsert_linkage_mapping(con, clusters)

    id_references = get_all_id_references(base_table_name="Transactor")
    for table_name, id_columns in id_references.items():
        replace_ids_with_canonical(con, table_name=table_name, id_columns=id_columns)
