"""Utility functions for the CLI"""

from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path

from utils.collect.state_collection_registry import get_state_collectors
from utils.constants import DATA_DIR, DEFAULT_SCHEMA_PATH

pipeline_step_details = {
    "collect": {
        "arguments": [
            "states",
            "start-year",
            "end-year",
            "start-date",
            "end-date",
            "data-directory",
            "output-directory",
            "slurm",
            "verbose",
            "override-existing-data",
            "chunk-size",
        ],
        "help": "Collect raw data from sources.",
        "input_directory_name": None,
        "output_directory_name": "raw",
    },
    "standardize": {
        "arguments": [
            "states",
            "start-year",
            "end-year",
            "data-directory",
            "input-directory",
            "output-directory",
            "slurm",
            "verbose",
            "output-format",
            "format",
        ],
        "help": "Standardize raw data with deterministic rules while maintaining shape.",
        "input_directory_name": "raw",
        "output_directory_name": "standardized",
    },
    "normalize": {
        "arguments": [
            "states",
            "start-year",
            "end-year",
            "data-directory",
            "input-directory",
            "output-directory",
            "slurm",
            "verbose",
            "chunk-size",
            "format",
            "output-format",
            "input-format",
            "schema",
        ],
        "help": "Reshape standardized data to match normalized database schema.",
        "input_directory_name": "standardized",
        "output_directory_name": "normalized",
    },
    "clean": {
        "arguments": [
            "states",
            "start-year",
            "end-year",
            "data-directory",
            "input-directory",
            "output-directory",
            "chunk-size",
            "slurm",
            "verbose",
            "format",
            "output-format",
            "input-format",
            "schema",
        ],
        "help": "Clean normalized data with heuristic transformations.",
        "input_directory_name": "normalized",
        "output_directory_name": "cleaned",
    },
    "link": {
        "arguments": [
            "states",
            "start-year",
            "end-year",
            "data-directory",
            "input-directory",
            "output-directory",
            "slurm",
            "verbose",
            "format",
            "output-format",
            "input-format",
            "database-path",
            "model-path",
            "threshold",
            "table-name",
            "train",
            "overwrite",
            "resume-from-checkpoint",
        ],
        "help": "Perform probabilistic record linkage on cleaned data to identify duplicate records.",
        "input_directory_name": "cleaned",
        "output_directory_name": "linked",
        # link operates on the full database, so it runs once, not per state
        "per_state": False,
    },
    "classify": {
        "arguments": [
            "states",
            "start-year",
            "end-year",
            "data-directory",
            "input-directory",
            "output-directory",
            "slurm",
            "verbose",
        ],
        "help": "Classify data by relation to climate.",
        "input_directory_name": "linked",
        "output_directory_name": "classified",
    },
}


def create_parser_for_step(
    parser: argparse.ArgumentParser, step: str
) -> argparse.ArgumentParser:
    """Create a parser for a given step"""
    step_details = pipeline_step_details[step]
    step_parser = parser.add_parser(step, help=step_details["help"])
    for argument in step_details["arguments"]:
        step_parser = add_argument_to_parser(step_parser, step, argument)
    step_parser.add_argument(
        "--input-directory-name",
        type=str,
        default=step_details["input_directory_name"],
        help=argparse.SUPPRESS,
    )
    step_parser.add_argument(
        "--output-directory-name",
        type=str,
        default=step_details["output_directory_name"],
        help=argparse.SUPPRESS,
    )
    step_parser.set_defaults(per_state=step_details.get("per_state", True))

    return step_parser


def add_argument_to_parser(
    parser: argparse.ArgumentParser,
    step_name: str,
    argument_name: str,
) -> argparse.ArgumentParser:
    """Add arguments to a parser for a given step.

    Args:
        parser: ArgumentParser to add arguments to
        step_name: Name of the step
        argument_name: Name of the argument
    """
    if argument_name == "states":
        parser.add_argument(
            "-s",
            "--states",
            nargs="+",
            default=None,
            help="List of states on which to run the given pipeline step. The pipeline steps except link will process each state separately.",
        )
    elif argument_name == "start-year":
        parser.add_argument(
            "--start-year",
            type=int,
            default=None,
            help="Earliest year (in YYYY format) on which to process data. If none is given, the earliest available year will be included.",
        )
    elif argument_name == "end-year":
        parser.add_argument(
            "--end-year",
            type=int,
            default=None,
            help="Latest year (in YYYY format) on which to process data. If none is given, the latest available year will be included.",
        )
    elif argument_name == "start-date":
        parser.add_argument(
            "--start-date",
            type=str,
            default=None,
            help="Earliest date (in YYYY-MM-DD format) on which to collect data. Overrides --start-year. Currently only used by Arizona.",
        )
    elif argument_name == "end-date":
        parser.add_argument(
            "--end-date",
            type=str,
            default=None,
            help="Latest date (in YYYY-MM-DD format) on which to collect data. Overrides --end-year. Currently only used by Arizona.",
        )
    elif argument_name == "data-directory":
        parser.add_argument(
            "-d",
            "--data-directory",
            type=Path,
            default=DATA_DIR,
            help=(
                "Path to main data directory. If --input-directory or "
                "--ouptut-directory are not set, pipeline steps will look for "
                "data in specified subdirectories of --data-directory. "
                "For this step, the default input directory is "
                f"{pipeline_step_details[step_name]['input_directory_name']} "
                "and the default ouptut directory is "
                f"{pipeline_step_details[step_name]['output_directory_name']}. "
                "If this argument is not set, the default data directory is taken "
                "from the DATA_DIR environment variable, which is currently "
                f"{DATA_DIR}."
            ),
        )
    elif argument_name == "input-directory":
        parser.add_argument(
            "-i",
            "--input-directory",
            type=Path,
            default=None,
            help=(
                f"Path to directory containing raw data. Default is the "
                f"{pipeline_step_details[step_name]['input_directory_name']} "
                "directory in DATA_DIR. Setting this ignore --data-directory."
            ),
        )
    elif argument_name == "output-directory":
        parser.add_argument(
            "-o",
            "--output-directory",
            type=Path,
            default=None,
            help=(
                f"Path to directory to save data. Default is the "
                f"{pipeline_step_details[step_name]['output_directory_name']} "
                "directory in DATA_DIR. Setting this ignores --data-directory."
            ),
        )
    elif argument_name == "slurm":
        parser.add_argument(
            "--slurm",
            action="store_true",
            default=False,
            help="Run pipeline on an HPC cluster using SLURM",
        )
    elif argument_name == "database-path":
        parser.add_argument(
            "--database-path",
            type=Path,
            default=None,
            help="Path to DuckDB database to load/save data",
        )
    elif argument_name == "model-path":
        parser.add_argument(
            "--model-path",
            type=Path,
            default=None,
            help="Path to record linkage model. If training, this will be the path to save the model.",
        )
    elif argument_name == "threshold":
        parser.add_argument(
            "--threshold",
            type=float,
            default=0.95,
            help="Match probability threshold for to consider two records as a match.",
        )
    elif argument_name == "table-name":
        parser.add_argument(
            "--table-name",
            type=str,
            default="transactor_detailed_view",
            help="Table to perform record linkage on",
        )
    elif argument_name == "format":
        parser.add_argument(
            "-f",
            "--format",
            choices=["csv", "parquet"],
            default=None,
            help=(
                "Desired file format (csv or parquet). If a separate input and output"
                " format is desired, use --input-format and --output-format instead. "
                "Default is parquet"
            ),
        )
    elif argument_name == "input-format":
        parser.add_argument(
            "--input-format",
            choices=["csv", "parquet"],
            default=None,
            help="Input file format (csv or parquet). Default is parquet",
        )
    elif argument_name == "output-format":
        parser.add_argument(
            "--output-format",
            choices=["csv", "parquet"],
            default=None,
            help="Output file format (csv or parquet). Default is parquet",
        )
    elif argument_name == "schema":
        parser.add_argument(
            "--schema",
            type=Path,
            default=DEFAULT_SCHEMA_PATH,
            help="Path to data schema, defaulting to src/utils/table.yaml",
        )
    elif argument_name == "chunk-size":
        parser.add_argument(
            "--chunk-size",
            type=int,
            default=None,
            help="Maximum number of rows to process at once. If not specified, processes entire dataset in memory.",
        )
    elif argument_name == "override-existing-data":
        # only used for Arizona data collection, exclude from help
        # and always set to True to match behavoir of other states.
        # Leaving it in AZ in case its ever desired to be added to all
        # states.
        parser.add_argument(
            "--override-existing-data",
            action="store_true",
            default=True,
            help=argparse.SUPPRESS,
        )
    elif argument_name == "verbose":
        parser.add_argument("-v", "--verbose", action="count", default=0)
    elif argument_name == "train":
        parser.add_argument(
            "--train",
            action="store_true",
            default=False,
            help="Train a record linkage model",
        )
    elif argument_name == "overwrite":
        parser.add_argument(
            "--overwrite",
            action="store_true",
            default=False,
            help="Overwrite existing database and tables",
        )
    elif argument_name == "resume-from-checkpoint":
        parser.add_argument(
            "--resume-from-checkpoint",
            action="store_true",
            default=False,
            help="Resume training from a saved checkpoint, skipping the first EM training session",
        )
    else:
        raise ValueError(f"Argument {argument_name} not found")
    return parser


def validate_args(
    args: argparse.Namespace, input_directory_name: str, output_directory_name: str
) -> argparse.Namespace:
    """Validate arguments.

    Args:
        args: Arguments to validate
        input_directory_name: Name of the input directory
        output_directory_name: Name of the output directory
    Returns:
        args: Validated arguments
    """
    if "chunk_size" in args and args.chunk_size is not None and args.chunk_size <= 0:
        raise ValueError("Chunk size must be greater than 0")
    # handle format
    if (
        ("input_format" in args and args.input_format is not None)
        or ("output_format" in args and args.output_format is not None)
    ) and ("format" in args and args.format is not None):
        raise ValueError(
            "Cannot specify both --format and --input-format or --output-format"
        )
    if "format" in args:
        if args.format is not None:
            args.input_format = args.format
            args.output_format = args.format
        else:
            if "input_format" in args and args.input_format is None:
                args.input_format = "parquet"
            if "output_format" in args and args.output_format is None:
                args.output_format = "parquet"
    # validate directories
    if "input_directory" in args:
        if args.input_directory is not None:
            if not args.input_directory.exists():
                raise ValueError(
                    f"Input directory {args.input_directory} does not exist"
                )
        else:
            args.input_directory = args.data_directory / input_directory_name
    # its okay if the output directory doesn't exist
    if "output_directory" in args:
        if args.output_directory is None:
            args.output_directory = args.data_directory / output_directory_name
        args.output_directory.mkdir(parents=True, exist_ok=True)
    if args.states is None:
        if "input_directory" in args:
            args.states = [
                state_dir.stem for state_dir in args.input_directory.iterdir()
            ]
            print(
                f"States not provided, using all states in input directory: {args.states}"
            )
        else:
            args.states = list(get_state_collectors().keys())
            print(
                f"States not provided, using all states with registered collectors: {args.states}"
            )

    return args


def route_pipeline_step(
    args: argparse.Namespace,
) -> int:
    """Route pipeline step to either local execution or SLURM submission.

    Args:
        args: Arguments to validate. Must contain a pipeline_step_func attribute
            that should handle saving its outputs.

    Returns:
        int: 0 if successful
    """
    args = validate_args(
        args,
        input_directory_name=args.input_directory_name,
        output_directory_name=args.output_directory_name,
    )

    # Most steps run once per state. Steps like link operate on the whole
    # database and run once, regardless of how many states are requested.
    if getattr(args, "per_state", True):
        job_args_list = []
        for state in args.states:
            state_args = deepcopy(args)
            state_args.state = state
            job_args_list.append(state_args)
    else:
        job_args_list = [args]

    if args.slurm:
        import submitit

        print("Submitting to cluster compute nodes with submitit")
        executor = submitit.AutoExecutor(folder="logs")
        executor.update_parameters(
            slurm_time=600,
            slurm_cpus_per_task=1,
            slurm_mem_per_cpu=256000,
            slurm_array_parallelism=3,
            slurm_partition="general",
        )
        with executor.batch():
            for job_args in job_args_list:
                executor.submit(args.pipeline_step_func, job_args)
    else:
        for job_args in job_args_list:
            if "state" in job_args:
                print(f"Running pipeline step for {job_args.state}")
            args.pipeline_step_func(job_args)
    return 0


def create_subparsers(
    parser: argparse.ArgumentParser,
) -> dict[str, argparse.ArgumentParser]:
    """Create subparsers for all pipeline steps.

    Args:
        parser: ArgumentParser to add subparsers to
    Returns:
        dict[str, argparse.ArgumentParser]: Dictionary of step name to parser
    """
    subparsers = parser.add_subparsers(dest="command", required=True)
    step_parsers = {}
    for step in pipeline_step_details:
        step_parsers[step] = create_parser_for_step(subparsers, step)
    return step_parsers
