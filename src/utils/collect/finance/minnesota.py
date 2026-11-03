"""This module provides functions to scrape Minnesota campaign finance data

Data notes:
- Only contributions from a single source over $200 in a calendar year are
  itemized. Smaller contributions are reported only as aggregate totals and do not
  appear in the data.
- In-kind contributions (donations other than money) are reported separately from
  monetary contributions.
"""

from http import HTTPStatus
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from utils.collect.state_collection_registry import register_special_state_collector
from utils.constants import DATA_DIR


def _extract_all_download_links(soup: BeautifulSoup, base_url: str) -> dict:
    """Extract the 'All' download links from each table on the page

    Args:
        soup: BeautifulSoup object of the parsed HTML
        base_url: Base URL for resolving relative links

    Returns:
        Dictionary mapping descriptive filenames to download URLs
    """
    downloads = {}

    # Find all tables on the page
    tables = soup.find_all("table")

    # Expected table headers to identify each dataset type
    table_identifiers = [
        ("contributions", "Itemized contributions received"),
        ("expenditures", "Itemized general expenditures"),
        ("independent_expenditures", "Itemized independent expenditures"),
    ]

    for table in tables:
        # Find the preceding heading to identify table type
        heading = table.find_previous("h1")
        if not heading:
            continue

        heading_text = heading.get_text().strip().lower()

        # Match heading to table type
        table_type = None
        for identifier, header_text in table_identifiers:
            if header_text.lower() in heading_text:
                table_type = identifier
                break

        if not table_type:
            continue

        # Find the 'All' row in this table
        expected_num_columns = 3
        rows = table.find_all("tr")
        for row in rows:
            cells = row.find_all(["td", "th"])
            if len(cells) >= expected_num_columns:
                # First cell should contain 'All'
                first_cell = cells[0].get_text().strip()
                if first_cell.lower() == "all":
                    # Last cell should contain the download link
                    download_cell = cells[-1]
                    link = download_cell.find("a")
                    if link and link.get("href"):
                        download_url = urljoin(base_url, link["href"])
                        filename = f"{table_type}_all.csv"
                        downloads[filename] = download_url
                        break

    return downloads


@register_special_state_collector("mn")
def download_MN_data(
    output_directory: Path = None,
) -> None:
    """Downloads MN campaign finance datasets to a local directory

    Downloads the three main "All" datasets from the Minnesota Campaign Finance Board:
    - Itemized contributions received of over $200
    - Itemized general expenditures and contributions made of over $200
    - Itemized independent expenditures of over $200

    Args:
        output_directory: desired output location. Defaults to 'data/raw/MN'
    Modifies:
        Saves raw files from cfb.mn.gov to output_directory
    """
    if output_directory is None:
        output_directory = DATA_DIR / "raw" / "mn"
    else:
        output_directory = Path(output_directory).resolve()

    output_directory.mkdir(exist_ok=True, parents=True)

    base_url = (
        "https://cfb.mn.gov/reports-and-data/self-help/data-downloads/campaign-finance/"
    )

    print("Fetching download page...")
    try:
        response = requests.get(base_url, timeout=30)
        if response.status_code != HTTPStatus.OK:
            print(f"Failed to fetch download page: {response.reason}")
            return
    except requests.RequestException as e:
        print(f"Error fetching download page: {e}")
        return

    # Parse the HTML to extract download links
    soup = BeautifulSoup(response.content, "html.parser")
    downloads = _extract_all_download_links(soup, base_url)

    if not downloads:
        print("No 'All' download links found on the page")
        return

    print(f"Found {len(downloads)} datasets to download")

    for filename, download_url in downloads.items():
        print(f"Downloading {filename}...")

        try:
            response = requests.get(download_url, timeout=60)
            if response.status_code != HTTPStatus.OK:
                print(f"Failed to download {filename}: {response.reason}")
                continue
            file_path = output_directory / filename
            with file_path.open("wb") as f:
                f.write(response.content)

            print(f"Successfully downloaded {filename} to {file_path}")

        except requests.RequestException as e:
            print(f"Error downloading {filename}: {e}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output_directory",
        type=str,
        default=None,
        help="Output directory for downloaded data. Defaults to DATA_DIR/raw/MN",
    )
    args = parser.parse_args()

    download_MN_data(args.output_directory)
