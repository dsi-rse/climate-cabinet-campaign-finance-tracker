"""Download election results from Pennsylvania."""

import argparse
import json
import time
from io import StringIO

import pandas as pd
import requests

from utils.constants import BASE_FILEPATH

BASE_URL = "https://www.electionreturns.pa.gov/api/Reports/GenerateReport"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:136.0) Gecko/20100101 Firefox/136.0",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://www.electionreturns.pa.gov",
    "Referer": "https://www.electionreturns.pa.gov/ReportCenter/Reports",
}
ALL_ELECTION_URL = "https://www.electionreturns.pa.gov/api/ElectionReturn/GetAllElections?methodName=GetAllElections"
TOO_MANY_REQUESTS = 429


def get_all_election_ids(start_year: int = None, end_year: int = None) -> list[dict]:
    """Get all valid election IDs and subtypes using the API.

    Args:
        start_year: The starting year for the elections to fetch.
            If None, defaults to the earliest year available.
        end_year: The ending year for the elections to fetch.
            If None, defaults to the latest year available.

    Returns:
        A list of dictionaries containing: "EclectionID", "Subtype", and "Year".
        "ElectionID" and "Subtype" are used to fetch election data.
    """
    response = requests.get(ALL_ELECTION_URL, headers=HEADERS, timeout=10)
    response.raise_for_status()
    # the data is returned in a horrible triple json escaped format
    raw_data = response.json()
    election_data_json = json.loads(raw_data)[0]["ElectionData"]
    election_dicts = json.loads(election_data_json)
    valid_elections = [
        {
            "ElectionID": int(election["Electionid"]),
            "Subtype": election["ElectionType"],
            "Year": int(election["ElectionYear"]),
        }
        for election in election_dicts
        if (
            (start_year is None or int(election["ElectionYear"]) >= start_year)
            and (end_year is None or int(election["ElectionYear"]) <= end_year)
        )
    ]
    return valid_elections


def fetch_election_data(election_id: int, subtype: str) -> dict:
    """Fetch election data for a given election ID and subtype.

    Args:
        election_id: The ID of the election to fetch data for.
        subtype: The subtype of the election to fetch data for.
            "G" for General, "P" for Primary, "S" for Special.

    Returns:
        A pandas DataFrame containing the election data.
    """
    payload = {
        "ElectionID": election_id,
        "ElectionsubType": subtype,
        "ExportType": "C",
        "ReportType": "S",
        "FileName": "Official",
    }
    response = requests.post(BASE_URL, headers=HEADERS, json=payload, timeout=10)
    if response.status_code == TOO_MANY_REQUESTS:
        print("Rate limited. Retrying after a delay...")
        time.sleep(10)
        return fetch_election_data(election_id, subtype)
    response.raise_for_status()
    # Convert the response text to a pandas DataFrame
    csv_data = StringIO(response.text)
    election_details = pd.read_csv(csv_data)
    return election_details


def fetch_all_results(elections: list[dict]) -> pd.DataFrame:
    """Fetch results for all valid elections.

    Args:
        elections: A list of dictionaries containing election IDs and subtypes.

    Returns:
        A dataframe containing the election results for all valid elections.
    """
    results = []
    for election in elections:
        print(
            f"Fetching results for ElectionID {election['ElectionID']} Subtype {election['Subtype']}..."
        )
        try:
            election_df = fetch_election_data(
                election["ElectionID"], election["Subtype"]
            )
            election_df.loc[:, "election--year"] = election["Year"]
            results.append(election_df)
        except Exception as e:
            print(f"Failed to fetch data for ElectionID {election['ElectionID']}: {e}")
        time.sleep(1)  # Avoid hitting rate limits
    return pd.concat(results, ignore_index=True)


def standardize_election_data(election_results: pd.DataFrame) -> pd.DataFrame:
    """Standardize the election data to a common format.

    Args:
        election_results: A pandas DataFrame containing the election data.
            each row is a candidate result for a specific election and
            county.

    Returns:
        A pandas DataFrame with standardized column names and types.
    """
    # drop missing data
    required_columns = ["Candidate Name", "Votes", "Office Name", "District Name"]
    num_missing = len(
        election_results[election_results.loc[:, required_columns].isna().any(axis=1)]
    )
    print(f"Dropping {num_missing} missing values from {len(election_results)} rows...")
    election_results = election_results.dropna(subset=required_columns)
    # Remove commas and convert "Votes" column to integers
    election_results.loc[:, "Votes"] = (
        election_results["Votes"].str.replace(",", "").astype(int)
    )
    # aggregate election results by candidate
    aggregated_election_results = (
        election_results.groupby(
            [
                "Office Name",
                "District Name",
                "Candidate Name",
                "Party Name",
                "election--year",
                "Election Name",
            ]
        )
        .agg(
            {
                "Votes": "sum",
            }
        )
        .reset_index()
    )

    renaming_dict = {
        "Office Name": "office_sought",
        "District Name": "district",
        "Candidate Name": "candidate--full_name",
        "Party": "candidate--party",
        "Votes": "votes_received",
        "Election Name": "election--name",
    }
    aggregated_election_results = aggregated_election_results.rename(
        columns=renaming_dict
    )
    return aggregated_election_results


def main(start_year: int = None, end_year: int = None) -> None:
    """Main function to fetch and save election results."""
    print("Discovering valid elections...")
    elections = get_all_election_ids(start_year=start_year, end_year=end_year)
    print(f"Found {len(elections)} valid elections.")

    print("Fetching election results...")
    raw_election_results = fetch_all_results(elections)

    print("Saving results...")
    standardized_election_results = standardize_election_data(raw_election_results)
    standardized_election_results.to_csv(
        BASE_FILEPATH / "output" / "ElectionResults.csv"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start_year", type=int, default=2020)
    parser.add_argument("--end_year", type=int, default=2022)
    args = parser.parse_args()
    main(start_year=args.start_year, end_year=args.end_year)
