"""Command-line interface for campaign finance pipelines."""

from __future__ import annotations

import argparse
from pathlib import Path

from tqdm import tqdm

from utils.clean import clean_data
from utils.cli.utils import (
    create_subparsers,
    route_pipeline_step,
)
from utils.collect.state_collection_registry import get_state_collectors
from utils.database import (
    connect_duckdb,
    create_database_from_nested_parquet_directories,
    create_transactor_detailed_view,
    table_exists,
)
from utils.ids import load_id_mapping, save_id_mapping
from utils.io import load_database, save_database, staged_directory
from utils.link.predict import run_linkage_pipeline
from utils.link.train import train_splink
from utils.normalize import Normalizer, combine_duplicates_across_chunks
from utils.standardize import standardize_state

COERCED_VALUES_REPORT_NAME = "coerced_values.csv"
ID_MAPPING_NAME = "id_mapping.tsv"
# Database link builds when --database-path isn't given, in --output-directory
DEFAULT_DATABASE_NAME = "campaign_finance.duckdb"


def run_scrape(args: argparse.Namespace) -> int:
    """Command entry point for scraping raw data"""
    # how do I get the list of states to scrape?
    state_scraper, scraper_args = get_state_collectors()[args.state]
    state_output_directory = args.output_directory / args.state
    state_output_directory.mkdir(parents=True, exist_ok=True)
    scraper_args = {arg: getattr(args, arg) for arg in scraper_args}
    scraper_args["output_directory"] = state_output_directory
    state_scraper(**scraper_args)
    return 0


def run_standardize(args: argparse.Namespace) -> int:
    """Command entry point for standardizing raw data

    Output is written to a staging folder and replaces the state's folder only
    when the step finishes (see utils.io.staged_directory).
    """
    db, coerced_values = standardize_state(
        args.state, args.start_year, args.end_year, args.input_directory
    )
    print(
        f"Standardizing data for {args.state} from {args.input_directory} to {args.output_directory}"
    )
    state_output_directory = Path(args.output_directory) / args.state
    with staged_directory(state_output_directory) as staging:
        save_database(db, staging, format=args.output_format)
        # kept in a subfolder so later steps don't load the report as a table
        staged_report_path = staging / "reports" / COERCED_VALUES_REPORT_NAME
        staged_report_path.parent.mkdir(parents=True, exist_ok=True)
        coerced_values.to_csv(staged_report_path, index=False)
    report_path = state_output_directory / "reports" / COERCED_VALUES_REPORT_NAME
    print(
        f"{int(coerced_values['count'].sum())} raw values became null during"
        f" standardization; distinct values and counts are in {report_path}"
    )
    return 0


def run_normalize(args: argparse.Namespace) -> int:
    """Command entry point for normalizing standardized data

    Each chunk is normalized and appended to a staging folder. After the last
    chunk, duplicates that span chunks are removed (see
    utils.normalize.combine_duplicates_across_chunks), and the staging folder
    replaces the state's folder (see utils.io.staged_directory).
    """
    # Process database in chunks (a chunk may be all data)
    database_chunks = load_database(
        args.input_directory / args.state,
        format=args.input_format,
        chunk_size=args.chunk_size,
    )
    # Load existing ID mappings or start with empty dict
    state_output_directory = args.output_directory / args.state
    accumulated_id_mapping = load_id_mapping(state_output_directory / ID_MAPPING_NAME)

    with staged_directory(state_output_directory) as staging:
        id_mapping_file = staging / ID_MAPPING_NAME
        # keep the loaded mapping even if there is nothing to normalize
        save_id_mapping(accumulated_id_mapping, id_mapping_file)
        first_chunk = True
        for chunk_database in tqdm(database_chunks, desc="Processing chunks"):
            # Create normalizer with accumulated ID mappings from previous chunks
            normalizer = Normalizer(chunk_database, args.schema, accumulated_id_mapping)
            normalized_database = normalizer.normalize_database()

            # Update accumulated ID mappings with new mappings from this chunk
            accumulated_id_mapping.update(normalizer.id_mapping)

            # Save first chunk with overwrite mode, subsequent chunks with append
            save_mode = "overwrite" if first_chunk else "append"
            save_database(
                normalized_database,
                staging,
                format=args.output_format,
                mode=save_mode,
            )

            # Save updated ID mappings after each chunk
            save_id_mapping(accumulated_id_mapping, id_mapping_file)

            first_chunk = False
        combine_duplicates_across_chunks(staging, args.output_format, args.schema)
    return 0


def run_clean(args: argparse.Namespace) -> int:
    """Command entry point for cleaning normalized data

    Written to a staging folder like run_normalize, with duplicates that span
    chunks removed at the end.
    """
    database_chunks = load_database(
        args.input_directory / args.state,
        format=args.input_format,
        chunk_size=args.chunk_size,
    )
    with staged_directory(args.output_directory / args.state) as staging:
        first_chunk = True
        for chunk_database in tqdm(database_chunks, desc="Processing chunks"):
            cleaned_database = clean_data(chunk_database, args.schema)
            save_mode = "overwrite" if first_chunk else "append"
            save_database(
                cleaned_database,
                staging,
                format=args.output_format,
                mode=save_mode,
            )
            first_chunk = False
        combine_duplicates_across_chunks(staging, args.output_format, args.schema)
    return 0


def run_link(args: argparse.Namespace) -> int:
    """Command entry point for performing record linkage on cleaned data

    The source tables in the database are reloaded from the cleaned data on
    every run (for the states in --states), so linkage always uses the
    latest data. The database defaults to <output-directory>/
    DEFAULT_DATABASE_NAME. Linkage results are added to the database's
    linkage tables (see utils.link.predict); the source tables are never
    changed.
    """
    if args.model_path is None:
        raise ValueError(
            "--model-path is required for link: it is where a trained model is "
            "saved (with --train) and loaded from for inference."
        )
    if args.database_path is None:
        args.database_path = Path(args.output_directory) / DEFAULT_DATABASE_NAME
    args.database_path.parent.mkdir(parents=True, exist_ok=True)
    input_directory = args.input_directory
    if input_directory is not None and Path(input_directory).is_dir():
        con = create_database_from_nested_parquet_directories(
            args.database_path,
            Path(input_directory),
            overwrite=args.overwrite,
            states=args.states,
        )
    else:
        if not args.database_path.exists():
            raise ValueError(
                f"No cleaned data in {input_directory} and no database at "
                f"{args.database_path}. Run clean first or give --input-directory."
            )
        con = connect_duckdb(args.database_path)
        if not table_exists(con, "Transactor"):
            con.close()
            raise ValueError(
                f"{args.database_path} has no Transactor table and there is no "
                f"cleaned data in {input_directory} to load."
            )
    try:
        # rebuilt every run, since the source tables may have changed
        create_transactor_detailed_view(con)
        if args.train:
            model_path = Path(args.model_path)
            checkpoint_path = model_path.with_name(
                f"{model_path.stem}_checkpoint{model_path.suffix or '.json'}"
            )
            train_splink(
                con,
                args.table_name,
                args.model_path,
                checkpoint_path=checkpoint_path,
                resume_from_checkpoint=args.resume_from_checkpoint,
            )
    finally:
        con.close()
    run_linkage_pipeline(
        duckdb_path=args.database_path,
        model_path=args.model_path,
        threshold=args.threshold,
        table_name=args.table_name,
    )
    return 0


def run_classify(args: argparse.Namespace) -> int:
    """Command entry point for classifying data by relation to climate"""
    return 0


def build_complete_parser() -> argparse.ArgumentParser:
    """Build a complete parser for CLI options for all pipeline steps"""
    parser = argparse.ArgumentParser(prog="ccf", description="Campaign finance CLI")
    step_parsers = create_subparsers(parser)

    step_parsers["collect"].set_defaults(
        func=route_pipeline_step, pipeline_step_func=run_scrape
    )

    step_parsers["standardize"].set_defaults(
        func=route_pipeline_step, pipeline_step_func=run_standardize
    )

    step_parsers["normalize"].set_defaults(
        func=route_pipeline_step, pipeline_step_func=run_normalize
    )

    step_parsers["clean"].set_defaults(
        func=route_pipeline_step, pipeline_step_func=run_clean
    )

    step_parsers["link"].set_defaults(
        func=route_pipeline_step, pipeline_step_func=run_link
    )

    step_parsers["classify"].set_defaults(
        func=route_pipeline_step, pipeline_step_func=run_classify
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for the `ccf` console script."""
    parser = build_complete_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
