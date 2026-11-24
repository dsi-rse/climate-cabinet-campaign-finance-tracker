"""Scripts to scrape Arizona Campaign Finance data

Data is retrieved from the Arizona Campaign Finance website.
https://seethemoney.az.gov/Reporting/AdvancedSearch/

For bulk data, a Microsoft Access database is available for purchase from
the Arizona Secretary of State: https://azsos.nextrequest.com/requests/new

Data notes:
- Only contributions over $100 or from out of state, and expenses over $250, must
  be itemized. Smaller in-state contributions may appear under pseudonyms such as
  'Multiple Donors'.
- Negative amounts (refunds, loan payments) are shown in parentheses.
- Lobbyist spending is not distinguished, and spending by political organizations
  is sometimes listed as payments to vendors.
- The search's StartDate and EndDate are ignored for BallotMeasures (a
  one-week request returned 894,606 rows, the whole category), so the
  collector drops rows dated outside the requested range itself, for every
  category, and says how many it dropped. The server still sends them all.
- The search's Position filter (Support or Oppose) only applies to independent
  expenditures; the other categories return the same rows whatever it is set
  to, and the rows themselves don't say which it was. Independent expenditures
  are fetched once per position, into files named ...-Support.csv and
  ...-Oppose.csv.

Each search is logged as one line in <raw>/az/_downloads.jsonl (see
utils.collect.downloads): the query (category, cycle, dates, filer type and
position), when it ran, how many records it returned and how many of those
were dropped for their date, and the file's size and sha256. A run writes to
hidden temporary files and replaces the previous files only when it finishes,
so a failed or interrupted run leaves them as they were. The server offset
each file's query reached is kept in _progress.json beside the files, so a
run without override_existing_data resumes where the last one stopped even
when rows were dropped.
"""

import argparse
import datetime
import json
import time
from pathlib import Path
from typing import NamedTuple

import pandas as pd
import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

from utils.collect.downloads import DownloadError, append_download_record
from utils.collect.state_collection_registry import register_special_state_collector
from utils.constants import DATA_DIR
from utils.provenance import describe_file, utc_now

ADVANCED_SEARCH_URL = "https://seethemoney.az.gov/Reporting/AdvancedSearch/"
TRANSACTOR_DETAILS_URL = "https://seethemoney.az.gov/Reporting/GetDetailedInformation"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:139.0) Gecko/20100101 Firefox/139.0",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.5",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://seethemoney.az.gov",
    "Referer": "https://seethemoney.az.gov/Reporting/AdvancedSearch/",
    "Connection": "keep-alive",
}

# Category mappings
CATEGORY_TYPES = [
    "Income",
    "Expenditures",
    "IndependentExpenditures",
    "BallotMeasures",
]

# Filer type mappings
FILER_TYPES = {
    "Candidate": "130",
    "Committee": "131",
    "Party": "132",
    "Office Holder": "96",
}

TRANSACTOR_TYPES_TO_DETAILED_INFO_ID = {
    "Candidate": 4,
    "Committee": 3,
    "Party": 5,
    "Office Holder": 6,
}

TOO_MANY_REQUESTS = 429
# How many times a rate-limited (429) request is retried before giving up
MAX_RATE_LIMIT_RETRIES = 5
# How many times a page that fails to load is retried before the run fails
PAGE_RETRIES = 3
# The server answers some transactor ids with an error or an unexpected body.
# A few are skipped; if more than this share of a type's ids fail, the run
# fails and the earlier file is kept, since a rate limit or outage would
# otherwise leave a nearly empty file that looks complete.
MAX_SKIPPED_TRANSACTOR_SHARE = 0.05
# Position values the search accepts. Only independent expenditures differ by
# position; the other categories return the same rows for any value.
POSITIONS = ("Support", "Oppose")
POSITIONS_BY_CATEGORY = {"IndependentExpenditures": POSITIONS}
NO_POSITION = ("",)
# Server offset reached by each file's query, by file name, beside the files
PROGRESS_FILE = "_progress.json"
# Largest page size requested from the advanced search endpoint. Requests take
# about the same time up to at least 2500 rows, but the server returns HTTP 500
# for 5000 rows (tested September 2026).
MAX_PAGE_SIZE = 1000


class ArizonaAPI:
    """Handles all Arizona Campaign Finance API interactions."""

    def __init__(
        self,
        wait_time: float = 0.2,
        timeout: float = 30,
        max_rate_limit_retries: int = MAX_RATE_LIMIT_RETRIES,
    ) -> None:
        """Initialize the API client with a session.

        Args:
            wait_time: Seconds to wait before each request.
            timeout: Seconds to wait for a response. Also the base of the
                wait between retries of a rate-limited request.
            max_rate_limit_retries: How many times to retry a request the
                server rate-limits (HTTP 429) before raising.
        """
        self.wait_time = wait_time
        self.timeout = timeout
        self.max_rate_limit_retries = max_rate_limit_retries
        self.session = self._create_session()
        self._simulate_form_interaction()

    def _create_session(self) -> requests.Session:
        """Create and initialize a requests session with proper headers and cookies."""
        session = requests.Session()
        session.headers.update(HEADERS)
        time.sleep(self.wait_time)
        session.get(ADVANCED_SEARCH_URL, timeout=self.timeout)
        return session

    def _simulate_form_interaction(self) -> None:
        """Simulate form interaction to establish proper session state."""
        form_data = {
            "JurisdictionId": "0",
            "CommiteeReportId": "",
            "CategoryType": "Income",
            "CycleId": "",
            "StartDate": "",
            "EndDate": "",
            "FilerName": "",
            "FilerId": "",
            "BallotName": "",
            "BallotMeasureId": "",
            "FilerTypeId": "130",
            "OfficeTypeId": "",
            "OfficeId": "",
            "PartyId": "",
            "ContributorName": "",
            "VendorName": "",
            "StateId": "",
            "City": "",
            "Employer": "",
            "Occupation": "",
            "CandidateName": "",
            "CandidateFilerId": "",
            "Position": "Support",
            "LowAmount": "",
            "HighAmount": "",
        }
        time.sleep(self.wait_time)
        self.session.post(ADVANCED_SEARCH_URL, data=form_data, timeout=self.timeout)

    def get_available_election_cycles(self) -> dict[str, dict[str, str]]:
        """Get available election cycles from the Arizona website.

        Returns:
            Dictionary mapping cycle names to cycle info containing 'id' and date range
        """
        time.sleep(self.wait_time)
        response = self.session.get(ADVANCED_SEARCH_URL, timeout=self.timeout)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, "html.parser")
        cycle_select = soup.find("select", {"id": "CycleId"})

        if not cycle_select:
            raise ValueError("Could not find CycleId select element on page")

        cycles = {}
        for option in cycle_select.find_all("option"):
            value = option.get("value", "").strip()
            text = option.get_text().strip()

            if value and text and text != "Please Select an Election Cycle":
                start_date, end_date = self._extract_date_range_from_cycle_id(value)
                cycles[text] = {
                    "id": value,
                    "start_date": start_date,
                    "end_date": end_date,
                }

        return cycles

    def get_available_filer_types(self) -> dict[str, str]:
        """Get all available filer types from the Arizona website."""
        time.sleep(self.wait_time)
        response = self.session.get(ADVANCED_SEARCH_URL, timeout=self.timeout)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, "html.parser")
        filer_type_select = soup.find("select", {"id": "FilerTypeId"})

        if not filer_type_select:
            raise ValueError("Could not find FilerTypeId select element on page")

        filer_types = {}
        for option in filer_type_select.find_all("option"):
            value = option.get("value", "").strip()
            if not value:  # Skip 'All' option
                continue
            text = option.get_text().strip()
            filer_types[text] = value

        return filer_types

    def _extract_date_range_from_cycle_id(
        self, cycle_id: str
    ) -> tuple[datetime.date, datetime.date]:
        """Extract start and end dates from a cycle ID string.

        Args:
            cycle_id: Cycle ID in format "44~1/1/2025 12:00:00 AM~12/31/2026 11:59:59 PM"

        Returns:
            Tuple of start and end dates
        """
        expected_tilde_sections = 2
        if cycle_id.count("~") != expected_tilde_sections:
            raise ValueError(f"Invalid cycle ID: {cycle_id}")

        parts = cycle_id.split("~")
        start_part = parts[1].strip()
        end_part = parts[2].strip()

        start_date = self._parse_date_from_datetime_string(start_part)
        end_date = self._parse_date_from_datetime_string(end_part)

        return start_date, end_date

    def _parse_date_from_datetime_string(self, datetime_str: str) -> datetime.date:
        """Parse date from datetime string like '1/1/2025 12:00:00 AM'."""
        try:
            date_part = datetime_str.split(" ")[0]
            month, day, year = date_part.split("/")
            return datetime.date(int(year), int(month), int(day))
        except ValueError as e:
            raise ValueError(f"Invalid datetime string: {datetime_str}") from e

    def fetch_transaction_data_page(
        self,
        category_type: str,
        cycle_id: str,
        start_date: str,
        end_date: str,
        filer_type_id: str,
        start: int = 0,
        length: int = 1000,
        position: str = "",
    ) -> dict:
        """Fetch a single page of Arizona Campaign Finance transaction data.

        Args:
            category_type: Category type (Income, Expenditures, etc.)
            cycle_id: Election cycle ID with date range
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
            filer_type_id: ID of the filer type to filter by
            start: Start index for pagination
            length: Number of records to fetch
            position: "Support", "Oppose", or "" for both. Only independent
                expenditures differ by position.

        Raises:
            requests.HTTPError: if the server answers with an error.
            DownloadError: if a rate limit (429) persists after
                max_rate_limit_retries retries.
        """
        # Cap page size; the server errors on very large pages
        page_length = min(int(length), MAX_PAGE_SIZE)

        # Build DataTables form data with up-to-date columns
        datatables_form = (
            f"draw=1&"
            f"columns[0][data]=TransactionDate&columns[0][name]=&columns[0][searchable]=true&columns[0][orderable]=true&columns[0][search][value]=&columns[0][search][regex]=false&"
            f"columns[1][data]=CommitteeName&columns[1][name]=&columns[1][searchable]=true&columns[1][orderable]=true&columns[1][search][value]=&columns[1][search][regex]=false&"
            f"columns[2][data]=Amount&columns[2][name]=&columns[2][searchable]=true&columns[2][orderable]=true&columns[2][search][value]=&columns[2][search][regex]=false&"
            f"columns[3][data]=TransactionName&columns[3][name]=&columns[3][searchable]=true&columns[3][orderable]=true&columns[3][search][value]=&columns[3][search][regex]=false&"
            f"columns[4][data]=TransactionType&columns[4][name]=&columns[4][searchable]=true&columns[4][orderable]=true&columns[4][search][value]=&columns[4][search][regex]=false&"
            f"columns[5][data]=Occupation&columns[5][name]=&columns[5][searchable]=true&columns[5][orderable]=true&columns[5][search][value]=&columns[5][search][regex]=false&"
            f"columns[6][data]=Employer&columns[6][name]=&columns[6][searchable]=true&columns[6][orderable]=true&columns[6][search][value]=&columns[6][search][regex]=false&"
            f"columns[7][data]=City&columns[7][name]=&columns[7][searchable]=true&columns[7][orderable]=true&columns[7][search][value]=&columns[7][search][regex]=false&"
            f"columns[8][data]=State&columns[8][name]=&columns[8][searchable]=true&columns[8][orderable]=true&columns[8][search][value]=&columns[8][search][regex]=false&"
            f"columns[9][data]=ZipCode&columns[9][name]=&columns[9][searchable]=true&columns[9][orderable]=true&columns[9][search][value]=&columns[9][search][regex]=false&"
            f"order[0][column]=0&order[0][dir]=asc&"
            f"start={start}&length={page_length}&"
            f"search[value]=&search[regex]=false"
        )

        # Place search criteria in query string to match browser behavior
        search_query_params = {
            "JurisdictionId": "0",
            "CommiteeReportId": "",
            "CategoryType": category_type,
            "CycleId": cycle_id,
            "StartDate": start_date,
            "EndDate": end_date,
            "FilerName": "",
            "FilerId": "",
            "BallotName": "",
            "BallotMeasureId": "",
            "FilerTypeId": filer_type_id,
            "OfficeTypeId": "",
            "OfficeId": "",
            "PartyId": "",
            "ContributorName": "",
            "VendorName": "",
            "StateId": "",
            "City": "",
            "Employer": "",
            "Occupation": "",
            "CandidateName": "",
            "CandidateFilerId": "",
            "Position": position,
            "LowAmount": "",
            "HighAmount": "",
        }

        response = self._post_with_rate_limit_retries(
            ADVANCED_SEARCH_URL, params=search_query_params, data=datatables_form
        )
        response.raise_for_status()
        return response.json()

    def _post_with_rate_limit_retries(self, url: str, **kwargs) -> requests.Response:
        """Post a request, retrying while the server rate-limits it

        Args:
            url: Endpoint to post to.
            **kwargs: Passed to the session's post, with the timeout.

        Returns:
            The response, which is not a 429.

        Raises:
            DownloadError: if the server is still rate-limiting after the
                last retry. Raised here rather than left to raise_for_status
                so that a caller which skips an HTTPError for one record
                doesn't keep hitting a server that has asked it to stop.
        """
        for attempt in range(self.max_rate_limit_retries + 1):
            time.sleep(self.wait_time)
            response = self.session.post(url, timeout=self.timeout, **kwargs)
            if response.status_code != TOO_MANY_REQUESTS:
                return response
            if attempt < self.max_rate_limit_retries:
                # wait longer each time
                wait = self.timeout * (attempt + 1)
                print(
                    f"Rate limited. Waiting {wait:.0f}s before retry"
                    f" {attempt + 1} of {self.max_rate_limit_retries}..."
                )
                time.sleep(wait)
        raise DownloadError(
            f"{url} still answered 429 Too Many Requests after"
            f" {self.max_rate_limit_retries} retries"
        )

    def fetch_transactor_data(self, transactor_id: str, transactor_type: str) -> dict:
        """Get Arizona Campaign Finance transactor data by transactor ID.

        Args:
            transactor_id: ID of the transactor to get data for
            transactor_type: Type of the transactor (Committee, Candidate, etc.)

        Returns:
            Dictionary containing transactor data

        Raises:
            requests.HTTPError: if the server answers with an error.
            DownloadError: if a rate limit (429) persists after
                max_rate_limit_retries retries.
            ValueError: if the body isn't the expected JSON.
        """
        response = self._post_with_rate_limit_retries(
            TRANSACTOR_DETAILS_URL,
            params={
                "Page": 11,
                "startYear": 2000,
                "endYear": 2100,
                "JurisdictionId": 0,
                "TablePage": 1,
                "TableLength": 10,
                "Name": f"3~{transactor_id}",
            },
            headers=HEADERS,
        )
        response.raise_for_status()

        if "ReportFilerInfo" not in response.json():
            raise ValueError(
                f"Unexpected response from API for {transactor_id}: {response.json()}"
            )

        transactor_data = response.json()["ReportFilerInfo"]
        transactor_data[f"{transactor_type}ID"] = transactor_id
        return transactor_data


class ArizonaDataProcessor:
    """Handles Arizona Campaign Finance data processing and file I/O operations."""

    def __init__(
        self,
        output_path: Path | str,
        override_existing_data: bool = False,
        save_in_batches: bool = True,
        batch_size: int = 1000,
        early_stop: int | None = None,
        records_directory: Path | str | None = None,
        page_retries: int = PAGE_RETRIES,
        retry_wait: float = 5,
    ) -> None:
        """Initialize the data processor.

        Args:
            output_path: Directory to save processed data
            override_existing_data: If True, the files a query wrote before
                are replaced once the query finishes. Until then the new
                results go to a hidden temporary file, so a failed run leaves
                the previous files as they were. If False, a query resumes
                from the rows already in its file.
            save_in_batches: If True, save data in batches during processing
            batch_size: Number of records per batch when save_in_batches is True
            early_stop: If not None, stop processing for each type after this many records
            records_directory: Where to log each query, in _downloads.jsonl
                (the state's raw data directory). None logs nothing.
            page_retries: How many times a page that fails to load is retried
                before the run fails.
            retry_wait: Seconds to wait before retrying a page.
        """
        self.output_path = Path(output_path)
        self.override_existing_data = override_existing_data
        self.save_in_batches = save_in_batches
        self.batch_size = batch_size
        self.early_stop = early_stop
        self.records_directory = (
            Path(records_directory) if records_directory is not None else None
        )
        self.page_retries = page_retries
        self.retry_wait = retry_wait
        self.output_path.mkdir(parents=True, exist_ok=True)

    def _get_output_file_path(
        self,
        report_category: str,
        filer_type_id: str,
        cycle_id: str,
        start_date: datetime.date,
        end_date: datetime.date,
        position: str = "",
    ) -> Path:
        """Generate output file path for transaction data.

        The requested date range, and the position when there is one, are
        part of the name so that a file only ever holds the results of a
        single query, which keeps resuming safe.
        """
        filename = (
            f"{self._get_output_file_prefix(report_category, filer_type_id, cycle_id)}"
            f"-{start_date:%Y%m%d}-{end_date:%Y%m%d}"
            + (f"-{position}" if position else "")
            + ".csv"
        )
        return self.output_path / filename

    def _get_output_file_prefix(
        self, report_category: str, filer_type_id: str, cycle_id: str
    ) -> str:
        """Generate the filename prefix shared by all date ranges of a cycle."""
        return f"{report_category}-{filer_type_id}-{cycle_id.split('~')[0]}"

    def _existing_query_files(
        self, report_category: str, filer_type_id: str, cycle_id: str
    ) -> list[Path]:
        """Find the files earlier runs wrote for a category, filer type and cycle

        Returns:
            Files for any date range or position, including files saved
            before the date range was added to the name.
        """
        prefix = self._get_output_file_prefix(report_category, filer_type_id, cycle_id)
        return [
            *self.output_path.glob(f"{prefix}-*.csv"),
            *self.output_path.glob(f"{prefix}.csv"),
        ]

    def _check_for_conflicting_files(
        self,
        output_files: set[Path],
        report_category: str,
        filer_type_id: str,
        cycle_id: str,
    ) -> None:
        """Refuse to resume next to files from a different query

        Files for the same category, filer type and cycle but a different
        date range would overlap with the new results. With
        override_existing_data they are replaced when the query finishes;
        otherwise an error is raised.

        Raises:
            ValueError: if resuming would mix results from different queries.
        """
        if self.override_existing_data:
            return
        conflicting_files = [
            file
            for file in self._existing_query_files(
                report_category, filer_type_id, cycle_id
            )
            if file not in output_files
        ]
        if conflicting_files:
            names = ", ".join(sorted(file.name for file in output_files))
            raise ValueError(
                f"Found data for a different date range than {names}: "
                f"{', '.join(file.name for file in conflicting_files)}. Resuming "
                "would mix results from different queries. Use "
                "override_existing_data or a different output directory."
            )

    @staticmethod
    def _temporary_path(output_file: Path) -> Path:
        """Where a query's results go until it finishes (override mode)"""
        return output_file.with_name(f".{output_file.name}.new")

    def _replace_query_files(
        self,
        written: dict[Path, Path],
        report_category: str,
        filer_type_id: str,
        cycle_id: str,
    ) -> None:
        """Put a finished query's files in place of the earlier run's files

        Args:
            written: Final path of each of the query's files, mapped to the
                temporary file its results were written to. A query with no
                results has no temporary file; the earlier files still go.
            report_category: Category type (Income, Expenditures, etc.)
            filer_type_id: ID of the filer type the query was for
            cycle_id: Election cycle the query was for
        """
        progress = self._read_progress()
        changes: dict[str, int | None] = {}
        for file in self._existing_query_files(
            report_category, filer_type_id, cycle_id
        ):
            file.unlink()
            changes[file.name] = None
        for output_file, temporary_file in written.items():
            changes[temporary_file.name] = None
            if temporary_file.exists():
                temporary_file.replace(output_file)
                changes[output_file.name] = progress.get(temporary_file.name)
        self._record_progress(changes)

    def _log_query(
        self,
        output_file: Path,
        url: str,
        query: dict,
        records: int,
        retrieved_at: str,
        dropped_outside_dates: int = 0,
    ) -> None:
        """Record a finished query in the download log, if one is kept

        Args:
            output_file: File the results were written to.
            url: Endpoint queried.
            query: The query's parameters.
            records: How many records were kept from the query.
            retrieved_at: When the query started.
            dropped_outside_dates: How many records the server returned that
                were dated outside the query's range and dropped.
        """
        if self.records_directory is None:
            return
        record = {
            "url": url,
            "retrieved_at": retrieved_at,
            "finished_at": utc_now(),
            "query": query,
            "records": records,
            "dropped_outside_dates": dropped_outside_dates,
        }
        if output_file.exists():
            record.update(
                describe_file(output_file, relative_to=self.records_directory)
            )
        else:
            record.update({"path": output_file.name, "size": 0, "sha256": None})
        append_download_record(self.records_directory, record)

    def _get_transactor_file_path(self, transactor_type: str) -> Path:
        """Generate output file path for transactor data."""
        return self.output_path / f"{transactor_type}.csv"

    def _get_resume_position(self, file_path: Path) -> int:
        """Server offset to resume a file's query from

        The offset recorded in PROGRESS_FILE when the file was last written.
        A file with no recorded offset was written before offsets were kept,
        when every row the server returned was saved, so its row count is the
        offset.
        """
        if self.override_existing_data or not file_path.exists():
            return 0
        offset = self._read_progress().get(file_path.name)
        if offset is not None:
            return offset
        try:
            existing_df = pd.read_csv(file_path)
            return len(existing_df)
        except Exception as e:
            print(f"Error reading existing file {file_path}: {e}")
            return 0

    def _read_progress(self) -> dict[str, int]:
        """Server offset reached by each file's query, by file name"""
        path = self.output_path / PROGRESS_FILE
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError) as error:
            print(f"Ignoring unreadable {path}: {error}")
            return {}

    def _record_progress(self, changes: dict[str, int | None]) -> None:
        """Update PROGRESS_FILE: a file's new offset, or None to forget it"""
        progress = self._read_progress()
        for name, offset in changes.items():
            if offset is None:
                progress.pop(name, None)
            else:
                progress[name] = offset
        path = self.output_path / PROGRESS_FILE
        if not progress:
            path.unlink(missing_ok=True)
            return
        temporary = path.with_name(f".{path.name}.new")
        temporary.write_text(json.dumps(progress, indent=1, sort_keys=True))
        temporary.replace(path)

    def _save_batch_data(self, data: list[dict], file_path: Path) -> None:
        """Save a batch of data to file."""
        if not data:
            return

        batch_df = pd.DataFrame(data)
        batch_df.to_csv(
            file_path,
            index=False,
            mode="a",
            header=not file_path.exists(),
        )

    def process_transaction_data(
        self,
        api: ArizonaAPI,
        report_category: str,
        election_cycle: str,
        filer_type_id: str,
        start_date_override: str | None = None,
        end_date_override: str | None = None,
        positions: tuple[str, ...] = NO_POSITION,
    ) -> pd.DataFrame:
        """Process transaction data for given parameters.

        Each position is one query with its own file. With
        override_existing_data, the results are written to hidden temporary
        files and replace the earlier run's files for this category, filer
        type and cycle only when every position has finished, so a failed
        run leaves the earlier files as they were.

        Args:
            api: ArizonaAPI instance for making API calls
            report_category: Category type (Income, Expenditures, etc.)
            election_cycle: Election cycle ID with date range
            filer_type_id: ID of the filer type to filter by
            start_date_override: Optional YYYY-MM-DD to override cycle start (intersected)
            end_date_override: Optional YYYY-MM-DD to override cycle end (intersected)
            positions: Position values to query, one file each. ("",) for
                categories the position doesn't apply to.

        Returns:
            DataFrame containing the transactions fetched by this call, with
            a Position column when positions were queried.

        Raises:
            DownloadError: if a page could not be fetched after page_retries
                retries.
        """
        cycle_start_date, cycle_end_date = api._extract_date_range_from_cycle_id(
            election_cycle
        )
        # Apply optional precise-date overrides (intersect with cycle bounds)
        request_start_date = cycle_start_date
        request_end_date = cycle_end_date
        if start_date_override is not None:
            try:
                override_start = datetime.datetime.strptime(
                    start_date_override, "%Y-%m-%d"
                ).date()
                request_start_date = max(cycle_start_date, override_start)
            except ValueError:
                print(
                    f"Invalid start_date_override '{start_date_override}', using cycle start"
                )
        if end_date_override is not None:
            try:
                override_end = datetime.datetime.strptime(
                    end_date_override, "%Y-%m-%d"
                ).date()
                request_end_date = min(cycle_end_date, override_end)
            except ValueError:
                print(
                    f"Invalid end_date_override '{end_date_override}', using cycle end"
                )

        if request_start_date > request_end_date:
            # No overlap between requested date range and this cycle
            return pd.DataFrame()
        output_files = {
            position: self._get_output_file_path(
                report_category,
                filer_type_id,
                election_cycle,
                request_start_date,
                request_end_date,
                position,
            )
            for position in positions
        }
        self._check_for_conflicting_files(
            set(output_files.values()), report_category, filer_type_id, election_cycle
        )

        frames = []
        queries = []
        written = {}
        try:
            for position, output_file in output_files.items():
                if self.override_existing_data:
                    write_path = self._temporary_path(output_file)
                    write_path.unlink(missing_ok=True)
                    start_position = 0
                else:
                    write_path = output_file
                    start_position = self._get_resume_position(output_file)
                written[output_file] = write_path
                query = {
                    "CategoryType": report_category,
                    "CycleId": election_cycle,
                    "StartDate": request_start_date.strftime("%Y-%m-%d"),
                    "EndDate": request_end_date.strftime("%Y-%m-%d"),
                    "FilerTypeId": filer_type_id,
                    "Position": position,
                }
                retrieved_at = utc_now()
                fetched = self._fetch_all_pages(api, query, start_position, write_path)
                frame = pd.DataFrame(fetched.records)
                if position:
                    frame["Position"] = position
                frames.append(frame)
                queries.append((output_file, query, fetched, retrieved_at))
        except BaseException:
            if self.override_existing_data:
                for write_path in written.values():
                    write_path.unlink(missing_ok=True)
                self._record_progress({path.name: None for path in written.values()})
            raise

        if self.override_existing_data:
            self._replace_query_files(
                written, report_category, filer_type_id, election_cycle
            )
        for output_file, query, fetched, retrieved_at in queries:
            self._log_query(
                output_file,
                ADVANCED_SEARCH_URL,
                query,
                len(fetched.records),
                retrieved_at,
                dropped_outside_dates=fetched.dropped_outside_dates,
            )

        complete_data = pd.concat(frames, ignore_index=True)
        if complete_data.empty:
            print("No records found")
            return pd.DataFrame()
        print(f"Retrieved {len(complete_data)} total records")
        return complete_data

    def _fetch_all_pages(
        self, api: ArizonaAPI, query: dict, start_position: int, write_path: Path
    ) -> "FetchedPages":
        """Page through one query's results, saving batches as they arrive

        The server offset advances by the rows the server sent, not the rows
        kept: a page may hold rows dated outside the query's range, which are
        dropped. The offset reached is recorded in PROGRESS_FILE under the
        file's name after each save, so an interrupted run can resume.

        Args:
            api: ArizonaAPI instance for making API calls
            query: CategoryType, CycleId, StartDate, EndDate, FilerTypeId and
                Position for the search.
            start_position: Server offset to start from.
            write_path: File to append batches to.

        Returns:
            The records kept by this call, how many were dropped for their
            date, and the server offset reached.
        """
        all_records = []
        dropped_outside_dates = 0
        # batch_size is None when data is not saved in batches
        page_size = min(self.batch_size or MAX_PAGE_SIZE, MAX_PAGE_SIZE)
        progress_bar = None

        while True:
            response_data = self._fetch_page_with_retries(
                api, query, start_position, page_size
            )

            page = response_data["data"]
            if not page:
                break

            # the server ignores the dates for some categories (BallotMeasures)
            kept, outside = _within_dates(page, query["StartDate"], query["EndDate"])
            dropped_outside_dates += outside
            all_records.extend(kept)
            start_position += len(page)

            # Save in batches if enabled
            if self.save_in_batches:
                self._save_batch_data(kept, write_path)
                self._record_progress({write_path.name: start_position})

            # Initialize progress bar after first successful request
            total_records = response_data.get(
                "recordsFiltered", response_data.get("recordsTotal", 0)
            )
            if progress_bar is None and total_records > 0:
                progress_bar = tqdm(
                    total=total_records - start_position,
                    desc=(
                        f"Processing {query['CategoryType']} data for cycle"
                        f" {query['CycleId']} and filer type {query['FilerTypeId']}"
                        + (f" ({query['Position']})" if query["Position"] else "")
                    ),
                    unit="records",
                )
            if progress_bar:
                progress_bar.update(len(page))

            # Stop once we've reached the total number of records
            if total_records and start_position >= total_records:
                break

            if self.early_stop and len(all_records) >= self.early_stop:
                break

        if progress_bar:
            progress_bar.close()
        if dropped_outside_dates:
            print(
                f"Dropped {dropped_outside_dates:,} {query['CategoryType']} records"
                f" dated outside {query['StartDate']} to {query['EndDate']}; the"
                " server ignored the date range"
            )

        # Save final data if not saving in batches
        if not self.save_in_batches:
            self._save_batch_data(all_records, write_path)
            self._record_progress({write_path.name: start_position})
        return FetchedPages(all_records, dropped_outside_dates, start_position)

    def _fetch_page_with_retries(
        self, api: ArizonaAPI, query: dict, start: int, length: int
    ) -> dict:
        """Fetch one page, retrying a failed request before giving up

        Args:
            api: ArizonaAPI instance for making API calls
            query: The search parameters (see _fetch_all_pages).
            start: Record to start the page at.
            length: Records per page.

        Returns:
            The page, as the server returned it. It has a "data" list, which
            is empty once the results are exhausted.

        Raises:
            DownloadError: if the page still fails after page_retries
                retries, or the server keeps answering with a body that has
                no "data" (such as an error message sent with status 200).
        """
        for attempt in range(self.page_retries + 1):
            try:
                page = api.fetch_transaction_data_page(
                    category_type=query["CategoryType"],
                    cycle_id=query["CycleId"],
                    start_date=query["StartDate"],
                    end_date=query["EndDate"],
                    filer_type_id=query["FilerTypeId"],
                    start=start,
                    length=length,
                    position=query["Position"],
                )
                if not isinstance(page, dict) or not isinstance(page.get("data"), list):
                    raise ValueError(
                        f"the response has no data list: {str(page)[:200]}"
                    )
                return page
            except (requests.RequestException, ValueError) as error:
                if attempt == self.page_retries:
                    raise DownloadError(
                        f"Could not fetch {query['CategoryType']} records at offset"
                        f" {start} after {attempt + 1} tries: {error}"
                    ) from error
                print(
                    f"Error fetching data at offset {start} (try {attempt + 1} of"
                    f" {self.page_retries + 1}): {error}. Retrying in"
                    f" {self.retry_wait:g}s..."
                )
                time.sleep(self.retry_wait)
        raise AssertionError("unreachable")  # pragma: no cover

    def process_transactor_data(
        self,
        api: ArizonaAPI,
        transactor_ids_by_type: dict[str, set[str]],
        transactor_batch_size: int = 100,
    ) -> dict[str, pd.DataFrame]:
        """Process transactor data for given transactor IDs.

        Each type's results are written to its file (<type>.csv) when the
        type finishes, or in batches along the way with save_in_batches.
        With override_existing_data, they go to a hidden temporary file that
        replaces the earlier file only once the type finishes; a run that
        fails leaves the earlier file as it was. Ids the server rejects are
        printed and skipped, but if more than MAX_SKIPPED_TRANSACTOR_SHARE
        of a type's ids are skipped the run fails, since a rate limit or
        outage would otherwise leave a nearly empty file that looks complete.

        Args:
            api: ArizonaAPI instance for making API calls
            transactor_ids_by_type: Dictionary of transactor type to set of transactor IDs
            transactor_batch_size: Number of transactor IDs to process at a time

        Returns:
            Dictionary of transactor type to the records fetched

        Raises:
            DownloadError: if more than MAX_SKIPPED_TRANSACTOR_SHARE of a
                type's ids could not be fetched.
        """
        all_transactor_data = {}
        total_transactors = sum(len(ids) for ids in transactor_ids_by_type.values())

        with tqdm(
            total=total_transactors,
            desc="Processing all transactor types",
            unit="transactor",
        ) as overall_pbar:
            for transactor_type, transactor_ids in transactor_ids_by_type.items():
                if not transactor_ids:
                    continue

                output_file = self._get_transactor_file_path(transactor_type)
                if self.override_existing_data:
                    write_path = self._temporary_path(output_file)
                    write_path.unlink(missing_ok=True)
                else:
                    write_path = output_file
                    if output_file.exists():
                        print("Appending results to existing file")

                retrieved_at = utc_now()
                results = []
                skipped = 0
                transactor_ids_list = list(transactor_ids)
                try:
                    with tqdm(
                        total=len(transactor_ids_list),
                        desc=f"Processing {transactor_type} transactors",
                        unit="transactor",
                        leave=False,
                    ) as type_pbar:
                        for i in range(
                            0, len(transactor_ids_list), transactor_batch_size
                        ):
                            batch_results, batch_skipped = self._fetch_transactors(
                                api,
                                transactor_ids_list[i : i + transactor_batch_size],
                                transactor_type,
                                i // transactor_batch_size + 1,
                                (type_pbar, overall_pbar),
                            )
                            skipped += batch_skipped
                            # checked after every batch: once too many ids
                            # have been skipped the type can't finish, so
                            # stop asking the server
                            self._check_skipped_share(
                                skipped, len(transactor_ids_list), transactor_type
                            )
                            if batch_results and self.save_in_batches:
                                self._save_batch_data(batch_results, write_path)
                            results.extend(batch_results)
                    if not self.save_in_batches:
                        self._save_batch_data(results, write_path)
                except BaseException:
                    if self.override_existing_data:
                        write_path.unlink(missing_ok=True)
                    raise

                if self.override_existing_data:
                    write_path.replace(output_file)
                all_transactor_data[transactor_type] = results
                self._log_query(
                    output_file,
                    TRANSACTOR_DETAILS_URL,
                    {
                        "transactor_type": transactor_type,
                        "requested_ids": len(transactor_ids_list),
                        "skipped_ids": skipped,
                    },
                    len(results),
                    retrieved_at,
                )

        return all_transactor_data

    def _check_skipped_share(self, skipped: int, requested: int, kind: str) -> None:
        """Fail the run once too many of a type's ids have been skipped

        Args:
            skipped: Ids the server rejected so far.
            requested: Ids asked for in total.
            kind: The transactor type, for the message.

        Raises:
            DownloadError: if skipped is more than MAX_SKIPPED_TRANSACTOR_SHARE
                of requested.
        """
        if skipped > MAX_SKIPPED_TRANSACTOR_SHARE * requested:
            kept = (
                f", so {kind}.csv was not replaced"
                if self.override_existing_data
                else ""
            )
            raise DownloadError(
                f"{skipped} of {requested} {kind} ids could not be fetched (more"
                f" than {MAX_SKIPPED_TRANSACTOR_SHARE:.0%}){kept}"
            )

    def _fetch_transactors(
        self,
        api: ArizonaAPI,
        transactor_ids: list[str],
        transactor_type: str,
        batch_number: int,
        progress_bars: tuple[tqdm, ...],
    ) -> tuple[list[dict], int]:
        """Fetch one batch of transactors, skipping ones the server rejects

        Args:
            api: ArizonaAPI instance for making API calls
            transactor_ids: IDs in this batch
            transactor_type: Type of the transactors (Committee, Candidate, etc.)
            batch_number: Which batch this is, for the progress bar
            progress_bars: Bars to advance by one per transactor

        Returns:
            The records fetched, and how many ids were skipped. A transactor
            the server answers with an error or an unexpected body is
            printed and skipped; a failed request is raised.
        """
        batch_results = []
        skipped = 0
        with tqdm(
            total=len(transactor_ids),
            desc=f"Processing batch {batch_number}",
            unit="transactor",
            leave=False,
        ) as batch_pbar:
            for transactor_id in transactor_ids:
                try:
                    batch_results.append(
                        api.fetch_transactor_data(transactor_id, transactor_type)
                    )
                except (ValueError, requests.HTTPError) as e:
                    print(f"Error fetching data for {transactor_id}: {e}")
                    skipped += 1
                finally:
                    batch_pbar.update(1)
                    for progress_bar in progress_bars:
                        progress_bar.update(1)
        return batch_results, skipped

    def get_transactor_ids_from_transactions(self) -> dict[str, set[str]]:
        """Get transactor IDs from existing transaction files."""
        committee_ids = set()
        for file in self.output_path.glob("*.csv"):
            if file.name.startswith(tuple(CATEGORY_TYPES)):
                try:
                    transaction_df = pd.read_csv(file)
                    if "CommitteeID" in transaction_df.columns:
                        committee_ids.update(set(transaction_df["CommitteeID"]))
                except Exception as e:
                    print(f"Error reading file {file}: {e}")
        return {"Committee": committee_ids}

    def get_previously_scraped_transactor_ids(self) -> dict[str, set[str]]:
        """Get previously scraped transactor IDs from existing files."""
        previously_scraped = {}
        for filer_type in FILER_TYPES.keys():
            file_path = self._get_transactor_file_path(filer_type)
            if file_path.exists():
                try:
                    transactions_df = pd.read_csv(file_path)
                    id_column = f"{filer_type}ID"
                    if id_column in transactions_df.columns:
                        previously_scraped[filer_type] = set(transactions_df[id_column])
                except Exception as e:
                    print(f"Error reading file {file_path}: {e}")
                    previously_scraped[filer_type] = set()
            else:
                previously_scraped[filer_type] = set()
        return previously_scraped

    def load_existing_transaction_data(self) -> dict[str, pd.DataFrame]:
        """Load existing transaction data from files."""
        transaction_data = {}
        for category in CATEGORY_TYPES:
            for file in self.output_path.glob(f"{category}*.csv"):
                try:
                    transaction_data[category] = pd.read_csv(file)
                except Exception as e:
                    print(f"Error loading {file}: {e}")
                    transaction_data[category] = pd.DataFrame()

        return transaction_data

    def get_all_transaction_data(
        self,
        api: ArizonaAPI,
        start_date: str | None = None,
        end_date: str | None = None,
        filer_types: list[str] | None = None,
        report_categories: list[str] | None = None,
    ) -> dict[str, pd.DataFrame]:
        """Get all Arizona Campaign Finance transaction data.

        Args:
            api: ArizonaAPI instance for making API calls
            start_date: Start date for data collection in YYYY-MM-DD format
            end_date: End date for data collection in YYYY-MM-DD format
            filer_types: Filer types to filter by. If None, all filer types will be included
            report_categories: Report categories to filter by. If None, all categories included

        Returns:
            Dictionary with report category as key and DataFrame as value
        """
        if filer_types is None:
            filer_types = api.get_available_filer_types()
        else:
            filer_types = {
                filer_type: FILER_TYPES[filer_type] for filer_type in filer_types
            }

        if report_categories is None:
            report_categories = CATEGORY_TYPES

        all_data = {}
        cycle_ids = self._get_cycle_ids_in_range(start_date, end_date, api)
        print(f"Found {len(cycle_ids)} election cycles in the date range")

        for category_name in report_categories:
            all_category_data = []
            for filer_type, filer_type_id in filer_types.items():
                for cycle_id in cycle_ids:
                    partial_df = self.process_transaction_data(
                        api=api,
                        report_category=category_name,
                        election_cycle=cycle_id,
                        filer_type_id=filer_type_id,
                        start_date_override=start_date,
                        end_date_override=end_date,
                        positions=POSITIONS_BY_CATEGORY.get(category_name, NO_POSITION),
                    )
                    partial_df["filer_type"] = filer_type
                    all_category_data.append(partial_df)
                    time.sleep(1)  # Avoid hitting rate limits

            all_data[category_name] = pd.concat(all_category_data, ignore_index=True)

        return all_data

    def _get_cycle_ids_in_range(
        self, start_date: str | None, end_date: str | None, api: ArizonaAPI
    ) -> list[str]:
        """Get cycle IDs in the range of start_date and end_date."""
        if start_date is not None:
            start_date = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
        if end_date is not None:
            end_date = datetime.datetime.strptime(end_date, "%Y-%m-%d").date()

        cycles = api.get_available_election_cycles()
        cycle_ids = []
        for _, cycle_info in cycles.items():
            if (end_date is None or cycle_info["start_date"] <= end_date) and (
                start_date is None or cycle_info["end_date"] >= start_date
            ):
                cycle_ids.append(cycle_info["id"])
        return cycle_ids


class FetchedPages(NamedTuple):
    """What _fetch_all_pages fetched for one query"""

    records: list[dict]
    dropped_outside_dates: int
    offset: int


def _within_dates(
    records: list[dict], start_date: str, end_date: str
) -> tuple[list[dict], int]:
    """Keep the records whose TransactionDate is in the requested range

    The search returns dates as "/Date(<milliseconds since 1970>)/". A record
    with no readable date is kept, since the range can't be checked.

    Args:
        records: One page of records from the search.
        start_date: First day to keep, YYYY-MM-DD.
        end_date: Last day to keep, YYYY-MM-DD.

    Returns:
        The records in range, and how many were dropped.
    """
    first = datetime.date.fromisoformat(start_date)
    last = datetime.date.fromisoformat(end_date)
    kept = []
    for record in records:
        transaction_date = _parse_search_date(record.get("TransactionDate"))
        if transaction_date is None or first <= transaction_date <= last:
            kept.append(record)
    return kept, len(records) - len(kept)


def _parse_search_date(value: object) -> datetime.date | None:
    """Read a search result's "/Date(1714028400000)/" as a date, or None"""
    if not isinstance(value, str):
        return None
    digits = "".join(
        character for character in value if character.isdigit() or character == "-"
    )
    try:
        return datetime.datetime.fromtimestamp(
            int(digits) / 1000, tz=datetime.timezone.utc
        ).date()
    except (ValueError, OverflowError, OSError):
        return None


def get_cycle_info_by_year(year: int, api: ArizonaAPI) -> dict[str, str] | None:
    """Get cycle information for a specific year."""
    cycles = api.get_available_election_cycles()

    for cycle_name, cycle_info in cycles.items():
        if str(year) in cycle_name:
            return cycle_info

    return None


@register_special_state_collector("az")
def get_all_arizona_data(
    start_year: str | None = None,
    end_year: str | None = None,
    output_directory: str | None = None,
    override_existing_data: bool = False,
    chunk_size: int = 1000,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, pd.DataFrame]:
    """Get all Arizona Campaign Finance data.

    Args:
        start_year: Start year for data collection
        end_year: End year for data collection
        output_directory: Directory to save the dataframes to
        override_existing_data: If True, each query's earlier files are
            replaced when the query finishes (see ArizonaDataProcessor)
        chunk_size: If set, data will be saved in chunks of this size
        start_date: Optional YYYY-MM-DD to override year-based start date
        end_date: Optional YYYY-MM-DD to override year-based end date

    Returns:
        Dictionary containing both transaction and transactor data

    Raises:
        DownloadError: if a page of results could not be fetched after
            retries. Files from earlier runs are left as they were.
    """
    if output_directory is None:
        output_directory = DATA_DIR / "raw" / "az" / "AdvancedSearch"
    else:
        output_directory = Path(output_directory).resolve() / "AdvancedSearch"

    api = ArizonaAPI()
    processor = ArizonaDataProcessor(
        output_path=output_directory,
        override_existing_data=override_existing_data,
        save_in_batches=chunk_size is not None,
        batch_size=chunk_size,
        # the query log sits next to the other states' download logs
        records_directory=output_directory.parent,
    )

    # Get transaction data
    print("Collecting Arizona transaction data...")
    effective_start_date = (
        start_date
        if start_date is not None
        else (f"{start_year}-01-01" if start_year is not None else None)
    )
    effective_end_date = (
        end_date
        if end_date is not None
        else (f"{end_year}-12-31" if end_year is not None else None)
    )
    transaction_data = processor.get_all_transaction_data(
        api=api,
        start_date=effective_start_date,
        end_date=effective_end_date,
    )

    # Get transactor data
    print("Collecting Arizona transactor data...")
    transactor_ids = processor.get_transactor_ids_from_transactions()
    transactor_data = processor.process_transactor_data(
        api=api, transactor_ids_by_type=transactor_ids
    )

    return {**transaction_data, **transactor_data}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape Arizona Campaign Finance data")
    parser.add_argument("--start-year", type=int, required=True)
    parser.add_argument("--end-year", type=int, required=True)
    parser.add_argument(
        "--start-date",
        type=str,
        required=False,
        default=None,
        help="Optional YYYY-MM-DD to override start-year",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        required=False,
        default=None,
        help="Optional YYYY-MM-DD to override end-year",
    )
    parser.add_argument("--output-directory", type=str, required=False)
    parser.add_argument("--override-existing-data", action="store_true", default=False)
    parser.add_argument("--chunk-size", type=int, default=1000)
    args = parser.parse_args()

    get_all_arizona_data(
        start_year=args.start_year,
        end_year=args.end_year,
        output_directory=args.output_directory,
        override_existing_data=args.override_existing_data,
        chunk_size=args.chunk_size,
        start_date=args.start_date,
        end_date=args.end_date,
    )
