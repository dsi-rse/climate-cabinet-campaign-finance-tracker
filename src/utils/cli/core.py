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
from utils.io import load_database, save_database
from utils.link.predict import run_linkage_pipeline
from utils.link.train import train_splink
from utils.normalize import Normalizer
from utils.standardize import standardize_state


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
    """Command entry point for standardizing raw data"""
    db = standardize_state(
        args.state, args.start_year, args.end_year, args.input_directory
    )
    print(
        f"Standardizing data for {args.state} from {args.input_directory} to {args.output_directory}"
    )
    save_database(
        db,
        Path(args.output_directory) / args.state,
        format=args.output_format,
    )
    return 0


def run_normalize(args: argparse.Namespace) -> int:
    """Command entry point for normalizing standardized data"""
    # Process database in chunks (a chunk may be all data)
    database_chunks = load_database(
        args.input_directory / args.state,
        format=args.input_format,
        chunk_size=args.chunk_size,
    )
    # Load existing ID mappings or start with empty dict
    id_mapping_file = args.output_directory / args.state / "id_mapping.tsv"
    accumulated_id_mapping = load_id_mapping(id_mapping_file)

    first_chunk = True
    for chunk_database in tqdm(database_chunks, desc="Processing chunks"):
        # Create normalizer with accumulated ID mappings from previous chunks
        normalizer = Normalizer(chunk_database, args.schema, accumulated_id_mapping)
        normalized_database = normalizer.normalize_database()

        # Update accumulated ID mappings with new mappings from this chunk
        accumulated_id_mapping.update(normalizer.id_mapping)

        # Save first chunk with overwrite mode, subsequent chunks with append mode
        save_mode = "overwrite" if first_chunk else "append"
        save_database(
            normalized_database,
            args.output_directory / args.state,
            format=args.output_format,
            mode=save_mode,
        )

        # Save updated ID mappings after each chunk
        save_id_mapping(accumulated_id_mapping, id_mapping_file)

        first_chunk = False
    return 0


def run_clean(args: argparse.Namespace) -> int:
    """Command entry point for cleaning normalized data"""
    database_chunks = load_database(
        args.input_directory / args.state,
        format=args.input_format,
        chunk_size=args.chunk_size,
    )
    first_chunk = True
    for chunk_database in tqdm(database_chunks, desc="Processing chunks"):
        cleaned_database = clean_data(chunk_database, args.schema)
        save_mode = "overwrite" if first_chunk else "append"
        save_database(
            cleaned_database,
            args.output_directory / args.state,
            format=args.output_format,
            mode=save_mode,
        )
        first_chunk = False
    return 0


def run_link(args: argparse.Namespace) -> int:
    """Command entry point for performing record linkage on cleaned data"""
    if args.model_path is None:
        raise ValueError(
            "--model-path is required for link: it is where a trained model is "
            "saved (with --train) and loaded from for inference."
        )
    # check if the database exists and is not empty
    database_exists = args.database_path.exists()
    if database_exists:
        con = connect_duckdb(args.database_path)
        database_empty = (
            con.execute("SELECT COUNT(*) FROM information_schema.tables")
            .fetch_df()
            .iloc[0, 0]
            == 0
        )
    else:
        database_empty = True
    if database_exists and database_empty and args.input_directory is None:
        raise ValueError("Database is empty. Please provide an input directory.")
    if args.input_directory is not None and (args.overwrite or database_empty):
        con = create_database_from_nested_parquet_directories(
            args.database_path, args.input_directory, overwrite=args.overwrite
        )
    if not table_exists(con, args.table_name):
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
