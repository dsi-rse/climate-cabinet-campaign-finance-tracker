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
from http import HTTPStatus
from io import BytesIO
from pathlib import Path

import requests
from tqdm import tqdm

from utils.collect.state_collection_registry import register_special_state_collector
from utils.constants import DATA_DIR

EARLIEST_YEAR = 2000


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
        for each year's files.
    """
    if output_directory is None:
        output_directory = DATA_DIR / "raw" / "pa"
    if start_year is None:
        start_year = EARLIEST_YEAR
    if end_year is None:
        end_year = datetime.datetime.now().year

    else:
        output_directory = Path(output_directory).resolve()
    pa_url = "https://www.pa.gov/content/dam/copapwp-pagov/en/dos/resources/voting-and-elections/campaign-finance/campaign-finance-data/"  # noqa

    total_years = end_year - start_year + 1
    for year in tqdm(
        range(start_year, end_year + 1),
        total=total_years,
        desc="Downloading PA finance data",
    ):
        link = f"{pa_url}{year}.zip"

        response = requests.get(link, timeout=10)
        if response.status_code != HTTPStatus.OK:
            print(f"Pennsylvania data from {year} returned {response.reason}")
            continue

        year_directory = output_directory / str(year)
        year_directory.mkdir(exist_ok=True, parents=True)
        zippedfiles = zipfile.ZipFile(BytesIO(response.content))
        for zippedfile in zippedfiles.infolist():
            # some years have all contents in a single directory named after the
            # year by default
            if zippedfile.filename.startswith(f"{year}/"):
                zippedfiles.extract(zippedfile, output_directory)
            else:
                zippedfiles.extract(zippedfile, year_directory)


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
