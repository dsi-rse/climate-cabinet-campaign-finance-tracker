"""This modules provides functions to scrape Pennsylvannia campaign finance data

Data is retrieved from the Pennsylvania Department of State Full Campaign Finance
Export. Each year has contrib, debt, expense, filer, and receipt files. Column
definitions are in the technical specifications:
https://www.pa.gov/agencies/dos/resources/voting-and-elections-resources/campaign-finance-resources/technical-specifications-for-electronic-filing-of-campaign-expen.html

Data notes:
- 2002 files use a legacy layout with no header row. All other years use the
  current layout (adding CampaignFinanceID and SubmittedDate) with a header row.
- Filers whose contributions, expenditures, and liabilities each stay under $250
  in a reporting period can file a statement instead of a full report.
- Contributions of $50 or less per contributor need not be itemized, so itemized
  totals can understate what a filer received.
- Forgiven debts count as contributions.
- For cross-checking, aggregated data is available from Transparency USA and from
  the PA campaign finance search:
  https://www.campaignfinanceonline.pa.gov/Pages/CFReportSearch.aspx
"""

import datetime
import zipfile
import zlib
from http import HTTPStatus
from pathlib import Path

import requests
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

EARLIEST_YEAR = 2000
# What can go wrong with one year's download or extraction without meaning
# the other years will fail too. zipfile raises zlib.error for corrupt
# compressed bytes and BadZipFile only for a truncated or non-zip body;
# requests' errors are OSErrors.
_YEAR_ERRORS = (
    DownloadError,
    zipfile.BadZipFile,
    zlib.error,
    EOFError,
    OSError,
    NotImplementedError,
)


@register_special_state_collector("pa")
def download_PA_data(
    start_year: int = None, end_year: int = None, output_directory: Path = None
) -> None:
    """Downloads PA datasets from specified years to a local directory

    Args:
        start_year: The first year in the range of desired years to extract data
        end_year: The last year in the range of desired years to extract data.
        output_directory: desired output location. Defaults to 'data/raw/PA'
    Modifies:
        Saves raw files from dos.pa.gov to output_directory with a separate directory
        for each year's files. Each year's zip is kept in output_directory/_archives
        with its retrieval time in the name, and each download is logged in
        output_directory/_downloads.jsonl. Files an earlier download of a year
        extracted that this one doesn't have are removed (see
        utils.collect.downloads.replace_extracted_files).

    Raises:
        DownloadError: after every year has been tried, if any year's zip
            could not be downloaded or extracted. The error names each year.
            A year the department has no file for (HTTP 404, such as the
            current year before it is published) is skipped, not failed.
    """
    if output_directory is None:
        output_directory = DATA_DIR / "raw" / "pa"
    else:
        output_directory = Path(output_directory).resolve()
    if start_year is None:
        start_year = EARLIEST_YEAR
    if end_year is None:
        end_year = datetime.datetime.now().year
    pa_url = "https://www.pa.gov/content/dam/copapwp-pagov/en/dos/resources/voting-and-elections/campaign-finance/campaign-finance-data/"  # noqa

    total_years = end_year - start_year + 1
    failures = []
    for year in tqdm(
        range(start_year, end_year + 1),
        total=total_years,
        desc="Downloading PA finance data",
    ):
        link = f"{pa_url}{year}.zip"
        try:
            _download_year(link, year, output_directory)
        except _YEAR_ERRORS as error:
            print(f"Could not collect Pennsylvania data for {year}: {error}")
            failures.append(f"{year}: {error}")
    if failures:
        raise DownloadError(
            f"Pennsylvania data for {len(failures)} year(s) could not be"
            " collected:\n" + "\n".join(failures)
        )


def _download_year(link: str, year: int, output_directory: Path) -> None:
    """Download one year's zip, keep it, extract it and log it

    Args:
        link: URL of the year's zip.
        year: The year.
        output_directory: The state's raw data directory.

    Raises:
        DownloadError: if the server returns an error other than 404.
        requests.RequestException: if the request fails.
        zipfile.BadZipFile, zlib.error: if the download is not a zip or its
            contents are corrupt. It is still kept and logged, with no
            extracted files.
    """
    retrieved_at = utc_now()
    with requests.get(link, timeout=10, stream=True) as response:
        if response.status_code == HTTPStatus.NOT_FOUND:
            print(f"Pennsylvania has no file for {year} ({link} returned 404)")
            return
        if response.status_code != HTTPStatus.OK:
            raise DownloadError(
                f"{link} returned {response.status_code} {response.reason}"
            )
        zip_path = archive_path(output_directory, link, retrieved_at)
        record = save_response(
            response, zip_path, output_directory, retrieved_at=retrieved_at
        )

    def extract(staging: Path) -> None:
        with zipfile.ZipFile(zip_path) as zippedfiles:
            zippedfiles.extractall(staging)
        # some years have all contents in a single directory named after
        # the year; its files belong directly in the year's folder
        nested = staging / str(year)
        if nested.is_dir():
            for path in list(nested.iterdir()):
                path.rename(staging / path.name)
            nested.rmdir()

    year_directory = output_directory / str(year)
    extracted_paths = []
    try:
        extracted_paths = replace_extracted_files(
            year_directory, output_directory, extract
        )
    finally:
        # log the download even if extraction fails, since the zip is kept
        add_extracted_files(record, extracted_paths, output_directory)
        append_download_record(output_directory, record)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--start_year", type=int, default=2015, help="Start year YYYY")
    parser.add_argument("--end_year", type=int, default=2025, help="End year YYYY")
    parser.add_argument(
        "--output_directory",
        type=str,
        default=None,
        help="Output directory for downloaded data. Defaults to DATA_DIR/raw/PA",
    )
    args = parser.parse_args()

    download_PA_data(args.start_year, args.end_year, args.output_directory)
