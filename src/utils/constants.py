"""Constants to be used in various parts of the project."""

import os
from pathlib import Path

import dotenv

dotenv.load_dotenv()
# repository root; only meaningful when running from a source checkout
BASE_FILEPATH = Path(__file__).resolve().parent.parent.parent
# installed utils package; use for resources bundled as package data
PACKAGE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_FILEPATH / "data")).resolve()
RAW_DATA_DIRECTORY = DATA_DIR / "raw"
STATE_FINANCE_CONFIG_DIRECTORY = PACKAGE_DIR / "standardize" / "finance" / "config"
# returns the base_path to the directory

source_metadata_directory = PACKAGE_DIR / "static"

SPLIT = "--"
ID_SUFFIX = "_id"
DEFAULT_SCHEMA_PATH = PACKAGE_DIR / "table.yaml"
