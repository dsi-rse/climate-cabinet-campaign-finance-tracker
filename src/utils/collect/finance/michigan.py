"""This module provides functions to scrape Michigan campaign finance data.

Data is retireved form the Legacy Downloads provided by the Michigan Secretary of State.
It is accessed from the following URL:
https://www.michigan.gov/sos/elections/disclosure/cfr/committee-search/intro/welcome-to-the-michigan-campaign-finance-searchable-database

For full historical data, you can search here:
https://mi-boe.entellitrak.com/etk-mi-boe-prod/page.request.do?page=page.miboeContributionPublicSearch.

This has known issues and does not allow for large downloads at this time. Michigan SOS
is working on it with status being updated here:
https://www.michigan.gov/sos/elections/disclosure/mitn-information

Previously data was available here:
https://miboecfr.nictusa.com/cfr/dumpall/cfrdetail/

This is no longer available without a login.

Data notes:
- In legacy downloads split into multiple files, only the first file of each year
  has a header row. The header includes a RUNTIME field giving the export time.
- Since 2014, committees spending or receiving $5,000 or more in a calendar year
  must file electronically.
- Data includes federal and local races as well as state races.
"""

import argparse
import lzma
import re
from functools import partial
from http import HTTPStatus
from pathlib import Path
from urllib.parse import urljoin

import py7zr
import requests
from bs4 import BeautifulSoup
from py7zr.exceptions import ArchiveError
from tqdm import tqdm

from utils.collect.downloads import (
    DownloadError,
    add_extracted_files,
    append_download_record,
    archive_path,
    replace_extracted_files,
    save_response,
)
from utils.collect.state_collection_registry import register_special_state_collector
from utils.constants import DATA_DIR
from utils.provenance import utc_now


def _extract_7z(seven_zip_path: Path, staging: Path) -> None:
    """Extract a 7z archive into a folder

    Args:
        seven_zip_path: The archive.
        staging: Folder to extract into.

    Raises:
        ArchiveError: if the file is not a 7z archive or can't be read.
        lzma.LZMAError: if the compressed bytes are corrupt.
    """
    try:
        with py7zr.SevenZipFile(seven_zip_path, mode="r") as archive:
            archive.extractall(path=staging)
    except PermissionError as e:
        # likely harmless: failure to set file times on some mounts
        print(f"Permission error while extracting Michigan data: {e}")


@register_special_state_collector("mi")
def download_MI_data(
    output_directory: Path = None, start_year: int = None, end_year: int = None
) -> None:
    """Downloads Michigan legacy campaign finance datasets to a local directory

    Args:
        output_directory: desired output location. Defaults to 'data/raw/mi'
        start_year: the first year to download data for
        end_year: the last year to download data for
    Modifies:
        Saves extracted files from michigan.gov to output_directory with a separate
        directory for each year's files. Each year's 7z archive is kept in
        output_directory/_archives with its retrieval time in the name, and each
        download is logged in output_directory/_downloads.jsonl. Files an
        earlier download of a year extracted that this one doesn't have are
        removed (see utils.collect.downloads.replace_extracted_files).

    Raises:
        DownloadError: if the page listing the archives can't be read, or
            after every year has been tried, if any year's archive could not
            be downloaded or extracted. The error names each year.
        requests.RequestException: if fetching the page fails.
    """
    if output_directory is None:
        output_directory = DATA_DIR / "raw" / "mi"
    else:
        output_directory = Path(output_directory).resolve()

    # put everything in a "LegacyDownloads" to signify where from MI Secretary of State
    # the data came from. Since there other sources (currently being worked on by MI SOS)
    # the scraper may be expanded in the future.
    state_directory = output_directory
    output_directory = output_directory / "LegacyDownloads"

    base_url = "https://www.michigan.gov"
    search_url = (
        "https://www.michigan.gov/sos/elections/disclosure/cfr/committee-search/"
        "intro/welcome-to-the-michigan-campaign-finance-searchable-database"
    )

    cookies = {
        "shell#lang": "en",
        "sxa_site": "sos",
        "browserChecked": "True",
        "sos#lang": "en",
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:139.0) Gecko/20100101 Firefox/139.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": "https://www.michigan.gov/en/sos/elections/Disclosure/cfr/committee-search",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "DNT": "1",
        "Sec-GPC": "1",
    }

    # Get the page containing legacy data links
    response = requests.get(search_url, cookies=cookies, headers=headers, timeout=30)
    if response.status_code != HTTPStatus.OK:
        raise DownloadError(
            f"The Michigan legacy data page {search_url} returned"
            f" {response.status_code} {response.reason}"
        )

    # Parse HTML to find legacy data links
    soup = BeautifulSoup(response.text, "html.parser")
    legacy_links = []

    # Find all links that contain 'Legacy-Data' and end with '.7z'
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "Legacy-Data" in href and ".7z" in href:
            # Extract year from filename using regex
            year_match = re.search(r"(\d{4})_mi_cfr\.7z", href)
            if year_match:
                year = year_match.group(1)
                full_url = urljoin(base_url, href)
                legacy_links.append((year, full_url))

    if not legacy_links:
        raise DownloadError(f"No legacy data links found on {search_url}")
    if start_year is None:
        start_year = min(int(year) for year, _ in legacy_links)
    if end_year is None:
        end_year = max(int(year) for year, _ in legacy_links)
    legacy_links = [
        (year, url)
        for year, url in legacy_links
        if int(year) >= start_year and int(year) <= end_year
    ]

    print(
        f"Found {len(legacy_links)} legacy data files in the range of {start_year} to {end_year}"
    )

    # Download and extract each file. A year that fails doesn't stop the
    # others; the failures are raised together at the end.
    failures = []
    for year, url in tqdm(
        sorted(legacy_links),
        desc="Downloading Michigan legacy finance data",
        total=len(legacy_links),
    ):
        print(f"Processing Michigan data for {year}...")

        # Stream the 7z file to disk; it is kept as the original download
        retrieved_at = utc_now()
        try:
            with requests.get(
                url, timeout=60, headers=headers, stream=True
            ) as file_response:
                if file_response.status_code != HTTPStatus.OK:
                    raise DownloadError(
                        f"{url} returned {file_response.status_code}"
                        f" {file_response.reason}"
                    )
                seven_zip_path = archive_path(state_directory, url, retrieved_at)
                record = save_response(
                    file_response,
                    seven_zip_path,
                    state_directory,
                    retrieved_at=retrieved_at,
                )
        except (requests.RequestException, DownloadError) as error:
            print(f"Could not download Michigan data for {year}: {error}")
            failures.append(f"{year}: {error}")
            continue

        # the files replace those of the year's previous download. They are
        # listed as written, not by the archive's stored names: a stored
        # name can be absolute or hold "..", and py7zr cleans it
        year_directory = output_directory / year
        extracted_paths = []
        try:
            extracted_paths = replace_extracted_files(
                year_directory, state_directory, partial(_extract_7z, seven_zip_path)
            )
            print(f"Successfully extracted Michigan data for {year}")
        except (ArchiveError, lzma.LZMAError, EOFError, OSError) as e:
            # py7zr raises LZMAError for corrupt bytes and Bad7zFile (an
            # ArchiveError) only for a truncated or non-7z body
            print(f"Could not extract Michigan data from {year}: {e}")
            failures.append(f"{year}: could not extract {seven_zip_path.name}: {e}")
        finally:
            # log the download even if extraction fails, since the 7z is kept
            add_extracted_files(record, extracted_paths, state_directory)
            append_download_record(state_directory, record)

    if failures:
        raise DownloadError(
            f"Michigan data for {len(failures)} year(s) could not be collected:\n"
            + "\n".join(failures)
        )
    print(f"Michigan data download completed. Files saved to {output_directory}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Download Michigan campaign finance legacy data"
    )
    parser.add_argument(
        "--output_directory",
        type=str,
        default=None,
        help="Output directory for downloaded data. Defaults to DATA_DIR/raw/MI",
    )
    args = parser.parse_args()

    download_MI_data(args.output_directory)
