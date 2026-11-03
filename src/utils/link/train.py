"""Code for training and running splink on dataset

Based on:
https://moj-analytical-services.github.io/splink/demos/tutorials/00_Tutorial_Introduction.html
"""
#!/usr/bin/env python
# coding: utf-8

import json
from pathlib import Path

import duckdb
import pandas as pd
import splink.comparison_library as cl
from splink import DuckDBAPI, Linker, SettingsCreator, block_on

# Tables larger than this are sampled down before training to limit disk usage
TRAINING_SAMPLE_SIZE = 200_000
TRAINING_SAMPLE_TABLE = "random_subset"
# Key used to store per-level EM training history inside checkpoint files
TRAINED_M_HISTORY_KEY = "cft_trained_m_history"


def create_random_individuals_subset(
    con: duckdb.DuckDBPyConnection, table_name: str, size: int
) -> None:
    """Create a temp table that is a random subset of individuals in table_name

    Individuals are filtered before sampling so the subset has `size` rows
    whenever the table has at least that many individuals.
    """
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE {TRAINING_SAMPLE_TABLE} AS
        SELECT *
        FROM (
            SELECT * FROM {table_name} WHERE transactor_type = 'Individual'
        )
        USING SAMPLE {size} ROWS (RESERVOIR)
    """)


def _get_trained_m_history(linker: Linker) -> dict[str, dict[str, list[float]]]:
    """Get m probabilities estimated by each EM session, by comparison and level

    Splink sets each level's final m probability to the median across EM sessions,
    but this history is not included in saved settings.
    """
    history = {}
    for comparison in linker._settings_obj.comparisons:
        history[comparison.output_column_name] = {
            str(level.comparison_vector_value): [
                record["probability"] for record in level._trained_m_probabilities
            ]
            for level in comparison._comparison_levels_excluding_null
        }
    return history


def _restore_trained_m_history(
    linker: Linker, history: dict[str, dict[str, list[float]]]
) -> None:
    """Re-add EM training history saved by _get_trained_m_history to a linker"""
    for comparison in linker._settings_obj.comparisons:
        level_history = history.get(comparison.output_column_name, {})
        for level in comparison._comparison_levels_excluding_null:
            for probability in level_history.get(
                str(level.comparison_vector_value), []
            ):
                level._add_trained_m_probability(probability, "restored checkpoint")


def save_checkpoint(linker: Linker, checkpoint_path: Path | str) -> None:
    """Save linker settings along with EM training history"""
    settings = linker.misc.save_model_to_json()
    settings[TRAINED_M_HISTORY_KEY] = _get_trained_m_history(linker)
    with Path(checkpoint_path).open("w") as f:
        json.dump(settings, f, indent=4)


def load_checkpoint(
    con: duckdb.DuckDBPyConnection, checkpoint_path: Path | str, table_name: str
) -> Linker:
    """Load a linker saved by save_checkpoint, including EM training history"""
    with Path(checkpoint_path).open() as f:
        settings = json.load(f)
    history = settings.pop(TRAINED_M_HISTORY_KEY, {})
    linker = Linker(table_name, settings, db_api=DuckDBAPI(con))
    _restore_trained_m_history(linker, history)
    return linker


def train_splink(
    con: duckdb.DuckDBPyConnection,
    table_name: str,
    output_file: Path | str,
    checkpoint_path: Path | str | None = None,
    resume_from_checkpoint: bool = False,
) -> None:
    """Train splink linker and save to json

    If table_name has more than TRAINING_SAMPLE_SIZE rows, training uses a random
    sample of TRAINING_SAMPLE_SIZE individuals (transactor_type = 'Individual')
    from it instead of the full table.

    Args:
        con: duckdb connection to database that has table table_name with arguments
            including first_name, last_name, address_city, address_street_name,
            name_suffix, name_prefix, employer_full_name, employer_role, phone_number,
            transactor_type and an id column with unique ids.
        table_name: name of a table in database
        output_file: where to save splink settings
        checkpoint_path: path to save/load checkpoint after first EM training
        resume_from_checkpoint: if True, load from checkpoint and skip initial
            training. As in a run from scratch, final m probabilities combine the
            estimates from both EM sessions.
    """
    # Sample dataset if it's too large for training to prevent disk space issues
    row_count = con.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
    if row_count > TRAINING_SAMPLE_SIZE:
        print(
            f"Dataset has {row_count:,} records. Creating training sample of "
            f"{TRAINING_SAMPLE_SIZE:,} individuals for memory efficiency..."
        )
        create_random_individuals_subset(con, table_name, TRAINING_SAMPLE_SIZE)
        table_name = TRAINING_SAMPLE_TABLE
        print(f"Training will use sample table: {table_name}")
    else:
        print(f"Training on full dataset: {row_count:,} records")

    checkpoint_exists = checkpoint_path and Path(checkpoint_path).exists()

    if resume_from_checkpoint and checkpoint_exists:
        print(f"Resuming from checkpoint: {checkpoint_path}")
        linker = load_checkpoint(con, checkpoint_path, table_name)
    else:
        if resume_from_checkpoint and not checkpoint_exists:
            print(
                f"Warning: --resume-from-checkpoint specified but checkpoint not found at {checkpoint_path}"
            )
            print("Starting training from scratch")

        db_api = DuckDBAPI(con)
        comparisons = [
            cl.ForenameSurnameComparison("first_name", "last_name"),
            cl.NameComparison("address_city").configure(
                term_frequency_adjustments=True
            ),
            cl.NameComparison("address_street_name").configure(
                term_frequency_adjustments=True
            ),
            cl.NameComparison("name_suffix").configure(term_frequency_adjustments=True),
            cl.NameComparison("name_prefix").configure(term_frequency_adjustments=True),
            cl.NameComparison("employer_full_name").configure(
                term_frequency_adjustments=True
            ),
            cl.NameComparison("employer_role").configure(
                term_frequency_adjustments=True
            ),
        ]
        if (
            "phone_number"
            in con.execute(f"PRAGMA table_info('{table_name}')").fetchall()
        ):
            comparisons.append(cl.LevenshteinAtThresholds("phone_number", 1))

        settings = SettingsCreator(
            link_type="dedupe_only",
            unique_id_column_name="id",
            comparisons=comparisons,
            blocking_rules_to_generate_predictions=[
                block_on("first_name", "last_name"),
                # More restrictive address blocking to prevent disk space issues
                block_on("first_name", "address_city", "address_street_name"),
            ],
            retain_intermediate_calculation_columns=False,
        )

        linker = Linker(table_name, settings, db_api=db_api)

        deterministic_rules = [
            block_on("first_name", "last_name", "address_city"),
            "jaro_winkler_similarity(l.first_name, r.first_name) >= 0.94 and l.last_name = r.last_name and l.address_street_name = r.address_street_name",
        ]

        linker.training.estimate_probability_two_random_records_match(
            deterministic_rules, recall=0.9
        )
        linker.training.estimate_u_using_random_sampling(max_pairs=1e8)
        training_blocking_rule = block_on("first_name", "last_name")

        linker.training.estimate_parameters_using_expectation_maximisation(
            training_blocking_rule
        )

        if checkpoint_path:
            save_checkpoint(linker, checkpoint_path)
            print(f"Saved checkpoint: {checkpoint_path}")

    # Use more restrictive blocking rule (includes first_name) to prevent
    # generating billions of pairs from common addresses
    training_blocking_rule_address = block_on(
        "first_name", "address_city", "address_street_name"
    )
    linker.training.estimate_parameters_using_expectation_maximisation(
        training_blocking_rule_address
    )

    linker.misc.save_model_to_json(output_file, overwrite=True)
    print(f"Saved final model: {output_file}")


def create_splink_visualizations(linker: Linker) -> None:
    """Create and save splink visualizations for analysis"""
    linker.visualisations.match_weights_chart()
    linker.visualisations.m_u_parameters_chart()
    linker.visualisations.parameter_estimate_comparisons_chart()
    linker.evaluation.unlinkables_chart()


def create_linker_dashboard(
    linker: Linker, df_predictions: pd.DataFrame, output_file: str
) -> None:
    """Create and save splink comparison dashboard"""
    linker.visualisations.comparison_viewer_dashboard(
        df_predictions, output_file, overwrite=True
    )


def create_clustering_chart(linker: Linker, df_predictions: pd.DataFrame) -> None:
    """Create splink clustering chart"""
    df_clusters = linker.clustering.cluster_pairwise_predictions_at_threshold(
        df_predictions, threshold_match_probability=0.5
    )

    linker.visualisations.cluster_studio_dashboard(
        df_predictions,
        df_clusters,
        "cluster_studio.html",
        sampling_method="by_cluster_size",
        overwrite=True,
    )
