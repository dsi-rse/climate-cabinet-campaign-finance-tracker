"""This module provides functions to scrape Texas campaign finance data.

Data is retrieved from the Texas Ethics Commission Campaign Finance CSV Database.
The bulk data is available as a ZIP file download from:
https://prd.tecprd.ethicsefile.com/public/cf/public/TEC_CF_CSV.zip

The database contains campaign finance reports filed electronically since July 2000,
including various types of records such as REPORT, CONTRIBUTION, EXPEND, LOAN, PLEDGE,
and related data.

CSV database format is documented at:
https://www.ethics.state.tx.us/data/search/cf/CampaignFinanceCSVFileFormat.pdf

For focused searches, the following page can be used:
https://www.ethics.state.tx.us/search/cf/AdvancedSearch.php
"""

import argparse
import zipfile
from http import HTTPStatus
from io import BytesIO
from pathlib import Path

import requests

from utils.collect.state_collection_registry import register_special_state_collector
from utils.constants import DATA_DIR


@register_special_state_collector("tx")
def download_TX_data(output_directory: Path = None) -> None:
    """Downloads Texas campaign finance CSV database to a local directory.

    Args:
        output_directory: desired output location. Defaults to 'data/raw/tx'
    Modifies:
        Downloads and extracts the bulk CSV database from Texas Ethics Commission
        to output_directory
    """
    if output_directory is None:
        output_directory = DATA_DIR / "raw" / "tx"
    else:
        output_directory = Path(output_directory).resolve()

    output_directory.mkdir(exist_ok=True, parents=True)

    # Direct URL to the bulk CSV database ZIP file
    zip_url = "https://prd.tecprd.ethicsefile.com/public/cf/public/TEC_CF_CSV.zip"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:139.0) Gecko/20100101 Firefox/139.0",
        "Accept": "application/zip,*/*",
        "Accept-Language": "en-US,en;q=0.5",
        "Connection": "keep-alive",
        "DNT": "1",
        "Sec-GPC": "1",
    }

    try:
        print("Downloading Texas campaign finance CSV database...")

        # Download the ZIP file
        response = requests.get(zip_url, headers=headers, timeout=120)
        if response.status_code != HTTPStatus.OK:
            print(f"Failed to download data: {response.status_code} {response.reason}")
            return

        print(f"Successfully downloaded ZIP file ({len(response.content):,} bytes)")
        print("Extracting CSV files...")

        # Extract ZIP file directly from memory
        with zipfile.ZipFile(BytesIO(response.content), "r") as zip_file:
            # Get list of files in the ZIP
            file_list = zip_file.namelist()
            print(f"Found {len(file_list)} files in archive:")

            extracted_files = []
            for filename in file_list:
                if filename.endswith("/"):  # Skip directories
                    continue

                print(f"  Extracting: {filename}")
                zip_file.extract(filename, output_directory)
                extracted_files.append(filename)

        print("Texas data download completed successfully!")
        print(f"Extracted {len(extracted_files)} files to {output_directory}")
        print("\nExtracted files:")
        for filename in extracted_files:
            file_path = output_directory / filename
            if file_path.exists():
                file_size = file_path.stat().st_size
                print(f"  - {filename} ({file_size:,} bytes)")

    except requests.RequestException as e:
        print(f"Network error occurred while downloading: {e}")
    except zipfile.BadZipFile as e:
        print(f"Error extracting ZIP file: {e}")
    except Exception as e:
        print(f"Unexpected error occurred: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Download Texas campaign finance CSV database"
    )
    parser.add_argument(
        "--output_directory",
        type=str,
        default=None,
        help="Output directory for downloaded data. Defaults to DATA_DIR/raw/TX",
    )
    args = parser.parse_args()

    download_TX_data(args.output_directory)
