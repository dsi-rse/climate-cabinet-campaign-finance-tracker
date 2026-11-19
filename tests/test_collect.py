import datetime
import importlib
import io
import pkgutil
from unittest import mock

import pandas as pd
import py7zr
import pytest
import requests
import utils.collect.finance
from utils.cli.core import build_complete_parser
from utils.collect.downloads import DownloadError, read_download_records
from utils.collect.finance import arizona, michigan
from utils.collect.finance.arizona import (
    CATEGORY_TYPES,
    MAX_PAGE_SIZE,
    POSITIONS,
    ArizonaAPI,
    ArizonaDataProcessor,
)
from utils.collect.state_collection_registry import get_state_collectors
from utils.standardize.config import ConfigHandler

CYCLE_ID = "43~1/1/2023 12:00:00 AM~12/31/2024 11:59:59 PM"


def import_all_collectors():
    for module in pkgutil.iter_modules(utils.collect.finance.__path__):
        importlib.import_module(f"utils.collect.finance.{module.name}")
    return get_state_collectors()


def test_collect_cli_provides_every_collector_argument():
    """run_scrape passes each collector parameter by name from the parsed args"""
    collectors = import_all_collectors()
    args = build_complete_parser().parse_args(["collect"])
    for state, (_, collector_args) in collectors.items():
        missing = [arg for arg in collector_args if not hasattr(args, arg)]
        assert not missing, f"{state} collector arguments missing from CLI: {missing}"


class FakeArizonaAPI(ArizonaAPI):
    """ArizonaAPI that serves records from memory instead of the network

    Args:
        n_records: Transactions to serve, per position.
        max_page_size: Most records returned per page.
        fail_from: Offset from which every transaction page request raises.
        transactors: Transactor id to the record to return for it.
    """

    def __init__(
        self,
        n_records: int,
        max_page_size: int = 100,
        fail_from: int | None = None,
        transactors: dict | None = None,
    ):
        self.records = [{"TransactionID": i} for i in range(n_records)]
        self.max_page_size = max_page_size
        self.fail_from = fail_from
        self.transactors = transactors or {}
        self.calls = []
        self.transactor_calls = []

    def fetch_transaction_data_page(self, start: int = 0, length: int = 1000, **kw):
        self.calls.append({"start": start, "length": length, **kw})
        if self.fail_from is not None and start >= self.fail_from:
            raise requests.ConnectionError("connection dropped")
        page = self.records[start : start + min(length, self.max_page_size)]
        return {
            "data": page,
            "recordsTotal": len(self.records) * 2,
            "recordsFiltered": len(self.records),
        }

    def fetch_transactor_data(self, transactor_id, transactor_type):
        self.transactor_calls.append(transactor_id)
        if transactor_id not in self.transactors:
            raise requests.HTTPError(f"no such transactor {transactor_id}")
        return {
            **self.transactors[transactor_id],
            f"{transactor_type}ID": transactor_id,
        }


def make_processor(tmp_path, override_existing_data=False, **kwargs):
    return ArizonaDataProcessor(
        output_path=tmp_path,
        override_existing_data=override_existing_data,
        retry_wait=0,
        **kwargs,
    )


def test_date_range_is_clipped_to_cycle(tmp_path):
    api = FakeArizonaAPI(5)
    make_processor(tmp_path).process_transaction_data(
        api,
        "Income",
        CYCLE_ID,
        "130",
        start_date_override="2024-01-01",
        end_date_override="2025-06-30",
    )
    assert api.calls[0]["start_date"] == "2024-01-01"
    assert api.calls[0]["end_date"] == "2024-12-31"


def test_date_range_outside_cycle_makes_no_requests(tmp_path):
    api = FakeArizonaAPI(5)
    transactions = make_processor(tmp_path).process_transaction_data(
        api,
        "Income",
        CYCLE_ID,
        "130",
        start_date_override="2025-01-01",
    )
    assert transactions.empty
    assert api.calls == []


@pytest.mark.parametrize("max_page_size", [100, 30])
def test_pagination_collects_every_record_once(tmp_path, max_page_size):
    api = FakeArizonaAPI(250, max_page_size=max_page_size)
    transactions = make_processor(tmp_path).process_transaction_data(
        api, "Income", CYCLE_ID, "130"
    )
    assert transactions["TransactionID"].tolist() == list(range(250))
    assert [call["start"] for call in api.calls] == list(range(0, 250, max_page_size))


def test_pagination_without_batch_size(tmp_path):
    """cft collect passes chunk_size=None, so batch_size may be None"""
    processor = ArizonaDataProcessor(
        output_path=tmp_path, save_in_batches=False, batch_size=None
    )
    transactions = processor.process_transaction_data(
        FakeArizonaAPI(150), "Income", CYCLE_ID, "130"
    )
    assert len(transactions) == 150


@pytest.mark.parametrize("batch_size", [None, 50, MAX_PAGE_SIZE * 10])
def test_page_size_is_capped(tmp_path, batch_size):
    api = FakeArizonaAPI(5)
    ArizonaDataProcessor(
        output_path=tmp_path, save_in_batches=False, batch_size=batch_size
    ).process_transaction_data(api, "Income", CYCLE_ID, "130")
    assert api.calls[0]["length"] == min(batch_size or MAX_PAGE_SIZE, MAX_PAGE_SIZE)


@pytest.mark.parametrize("category", CATEGORY_TYPES)
def test_standardize_config_matches_collected_transaction_files(category):
    pattern = ConfigHandler(
        "advanced_search_transactions", state_code="az"
    ).raw_data_path_pattern
    for filename in [
        f"{category}-130-43-20240101-20241231.csv",
        f"{category}-130-43-20240101-20241231-Support.csv",
        f"{category}-130-43.csv",
    ]:
        assert pattern.fullmatch(f"AdvancedSearch/{filename}")


def test_output_file_name_includes_date_range(tmp_path):
    make_processor(tmp_path).process_transaction_data(
        FakeArizonaAPI(5),
        "Income",
        CYCLE_ID,
        "130",
        start_date_override="2024-01-01",
    )
    assert [f.name for f in tmp_path.glob("*.csv")] == [
        "Income-130-43-20240101-20241231.csv"
    ]


def test_resume_continues_same_date_range(tmp_path):
    saved = tmp_path / "Income-130-43-20230101-20241231.csv"
    pd.DataFrame({"TransactionID": range(40)}).to_csv(saved, index=False)
    api = FakeArizonaAPI(100)
    make_processor(tmp_path).process_transaction_data(api, "Income", CYCLE_ID, "130")
    assert api.calls[0]["start"] == 40
    assert pd.read_csv(saved)["TransactionID"].tolist() == list(range(100))


@pytest.mark.parametrize(
    "existing_name",
    ["Income-130-43-20240101-20241231.csv", "Income-130-43.csv"],
)
def test_resume_refuses_different_date_range(tmp_path, existing_name):
    pd.DataFrame({"TransactionID": range(40)}).to_csv(
        tmp_path / existing_name, index=False
    )
    api = FakeArizonaAPI(100)
    with pytest.raises(ValueError, match="different date range"):
        make_processor(tmp_path).process_transaction_data(
            api, "Income", CYCLE_ID, "130"
        )
    assert api.calls == []


def test_override_replaces_existing_files_without_duplicates(tmp_path):
    for name in [
        "Income-130-43-20240101-20241231.csv",
        "Income-130-43-20230101-20241231.csv",
        "Income-130-43.csv",
    ]:
        pd.DataFrame({"TransactionID": range(40)}).to_csv(tmp_path / name, index=False)
    # a different cycle whose number shares a prefix must be left alone
    other_cycle = tmp_path / "Income-130-4-20230101-20241231.csv"
    pd.DataFrame({"TransactionID": range(3)}).to_csv(other_cycle, index=False)

    make_processor(tmp_path, override_existing_data=True).process_transaction_data(
        FakeArizonaAPI(10), "Income", CYCLE_ID, "130"
    )

    assert sorted(f.name for f in tmp_path.glob("*.csv")) == [
        "Income-130-4-20230101-20241231.csv",
        "Income-130-43-20230101-20241231.csv",
    ]
    saved = pd.read_csv(tmp_path / "Income-130-43-20230101-20241231.csv")
    assert saved["TransactionID"].tolist() == list(range(10))
    assert not list(tmp_path.glob(".*"))


def test_an_interrupted_run_keeps_the_previous_files(tmp_path):
    """Until a query finishes, the earlier run's files are not touched"""
    previous = tmp_path / "Income-130-43-20230101-20241231.csv"
    pd.DataFrame({"TransactionID": range(40)}).to_csv(previous, index=False)
    api = FakeArizonaAPI(300, fail_from=200)

    with pytest.raises(DownloadError, match="offset 200 after 2 tries"):
        make_processor(
            tmp_path, override_existing_data=True, page_retries=1
        ).process_transaction_data(api, "Income", CYCLE_ID, "130")

    assert pd.read_csv(previous)["TransactionID"].tolist() == list(range(40))
    assert [f.name for f in tmp_path.iterdir()] == [previous.name]
    # the failing page was tried twice
    assert [call["start"] for call in api.calls] == [0, 100, 200, 200]


def test_a_failed_page_is_retried_before_the_run_fails(tmp_path):
    """A page that fails once and then loads doesn't fail the run"""
    api = FakeArizonaAPI(150)
    attempts = {"n": 0}
    original = api.fetch_transaction_data_page

    def flaky(start=0, **kw):
        if start == 100 and attempts["n"] == 0:
            attempts["n"] += 1
            raise requests.Timeout("timed out")
        return original(start=start, **kw)

    api.fetch_transaction_data_page = flaky
    transactions = make_processor(tmp_path).process_transaction_data(
        api, "Income", CYCLE_ID, "130"
    )
    assert transactions["TransactionID"].tolist() == list(range(150))


def test_independent_expenditures_are_fetched_for_both_positions(tmp_path):
    """Support and Oppose are separate queries, each with its own file"""
    api = FakeArizonaAPI(5)
    transactions = make_processor(
        tmp_path, override_existing_data=True
    ).process_transaction_data(
        api, "IndependentExpenditures", CYCLE_ID, "131", positions=POSITIONS
    )

    assert [call["position"] for call in api.calls] == ["Support", "Oppose"]
    assert sorted(f.name for f in tmp_path.glob("*.csv")) == [
        "IndependentExpenditures-131-43-20230101-20241231-Oppose.csv",
        "IndependentExpenditures-131-43-20230101-20241231-Support.csv",
    ]
    assert transactions["Position"].tolist() == ["Support"] * 5 + ["Oppose"] * 5


def test_only_independent_expenditures_are_queried_by_position(tmp_path):
    """The position filter changes nothing for the other categories"""
    api = FakeArizonaAPI(3)
    api.get_available_election_cycles = lambda: {
        "2023-2024": {
            "id": CYCLE_ID,
            "start_date": datetime.date(2023, 1, 1),
            "end_date": datetime.date(2024, 12, 31),
        }
    }
    make_processor(tmp_path).get_all_transaction_data(
        api, filer_types=["Committee"], report_categories=CATEGORY_TYPES
    )
    positions = {}
    for call in api.calls:
        positions.setdefault(call["category_type"], []).append(call["position"])
    assert positions == {
        "Income": [""],
        "Expenditures": [""],
        "IndependentExpenditures": ["Support", "Oppose"],
        "BallotMeasures": [""],
    }


def test_committees_are_saved_without_batches(tmp_path):
    """A run without --chunk-size writes the committee file at the end"""
    api = FakeArizonaAPI(0, transactors={"1": {"Name": "A"}, "2": {"Name": "B"}})
    processor = ArizonaDataProcessor(
        output_path=tmp_path, override_existing_data=True, save_in_batches=False
    )
    processor.process_transactor_data(api, {"Committee": {"1", "2"}})

    saved = pd.read_csv(tmp_path / "Committee.csv")
    assert sorted(saved["CommitteeID"].astype(str)) == ["1", "2"]
    assert sorted(saved["Name"]) == ["A", "B"]


def test_a_failed_committee_fetch_keeps_the_previous_file(tmp_path):
    """A request error mid-way leaves the earlier committee file as it was"""
    previous = tmp_path / "Committee.csv"
    pd.DataFrame({"CommitteeID": [9], "Name": ["Old"]}).to_csv(previous, index=False)
    api = FakeArizonaAPI(0, transactors={"1": {"Name": "A"}})

    def fetch(transactor_id, transactor_type):
        if transactor_id == "2":
            raise requests.ConnectionError("connection dropped")
        return {"Name": "A", f"{transactor_type}ID": transactor_id}

    api.fetch_transactor_data = fetch
    processor = ArizonaDataProcessor(
        output_path=tmp_path, override_existing_data=True, batch_size=1
    )
    with pytest.raises(requests.ConnectionError):
        processor.process_transactor_data(
            api, {"Committee": ["1", "2"]}, transactor_batch_size=1
        )

    assert pd.read_csv(previous)["Name"].tolist() == ["Old"]
    assert [f.name for f in tmp_path.iterdir()] == ["Committee.csv"]


def test_committees_the_server_rejects_are_skipped_and_the_rest_saved(tmp_path):
    api = FakeArizonaAPI(0, transactors={"1": {"Name": "A"}})
    processor = ArizonaDataProcessor(output_path=tmp_path, override_existing_data=True)
    processor.process_transactor_data(api, {"Committee": ["1", "missing"]})
    assert pd.read_csv(tmp_path / "Committee.csv")["Name"].tolist() == ["A"]


def test_rate_limit_retries_stop_after_the_cap(fake_response):
    """A 429 is retried a bounded number of times, then raised"""
    session = mock.MagicMock()
    session.post.return_value = fake_response(
        status_code=429, reason="Too Many Requests", url=arizona.ADVANCED_SEARCH_URL
    )
    with (
        mock.patch.object(arizona.requests, "Session", return_value=session),
        mock.patch.object(arizona.time, "sleep") as sleep,
    ):
        api = ArizonaAPI(wait_time=0, timeout=7, max_rate_limit_retries=2)
        with pytest.raises(requests.HTTPError, match="429"):
            api.fetch_transaction_data_page(
                "Income", CYCLE_ID, "2023-01-01", "2024-12-31", "130"
            )

    # one post to warm up the session, then the request and two retries
    assert session.post.call_count == 4
    assert [call.args[0] for call in sleep.call_args_list if call.args[0]] == [7, 14]
    assert session.post.call_args.kwargs["params"]["Position"] == ""


def test_each_query_is_logged(tmp_path):
    """The download log records what was asked for and what came back"""
    api = FakeArizonaAPI(5, transactors={"1": {"Name": "A"}})
    processor = make_processor(
        tmp_path / "AdvancedSearch",
        override_existing_data=True,
        records_directory=tmp_path,
    )
    processor.process_transaction_data(
        api, "IndependentExpenditures", CYCLE_ID, "131", positions=POSITIONS
    )
    processor.process_transactor_data(api, {"Committee": {"1"}})

    records = read_download_records(tmp_path)
    assert [record["query"].get("Position") for record in records] == [
        "Support",
        "Oppose",
        None,
    ]
    support = records[0]
    assert support["url"] == arizona.ADVANCED_SEARCH_URL
    assert support["query"]["CycleId"] == CYCLE_ID
    assert support["query"]["StartDate"] == "2023-01-01"
    assert support["records"] == 5
    assert (
        support["path"]
        == "AdvancedSearch/IndependentExpenditures-131-43-20230101-20241231-Support.csv"
    )
    assert support["size"] == (tmp_path / support["path"]).stat().st_size
    assert len(support["sha256"]) == 64
    assert records[2]["url"] == arizona.TRANSACTOR_DETAILS_URL
    assert records[2]["query"] == {"transactor_type": "Committee", "requested_ids": 1}
    assert records[2]["path"] == "AdvancedSearch/Committee.csv"


def make_7z(filename: str, content: str) -> bytes:
    buffer = io.BytesIO()
    with py7zr.SevenZipFile(buffer, mode="w") as archive:
        archive.writestr(content, filename)
    return buffer.getvalue()


MICHIGAN_PAGE = """
<a href="/-/media/Legacy-Data/2001_mi_cfr.7z">2001</a>
<a href="/-/media/Legacy-Data/2002_mi_cfr.7z">2002</a>
"""


@pytest.mark.parametrize(
    "first_year_result",
    [
        requests.Timeout("timed out"),
        "bad-archive",
    ],
    ids=["network-error", "bad-archive"],
)
def test_michigan_failure_skips_only_that_year(
    tmp_path, first_year_result, fake_response
):
    def fake_get(url, **kwargs):
        if "2001" in url:
            if isinstance(first_year_result, Exception):
                raise first_year_result
            return fake_response(content=b"not a 7z file", url=url)
        if "2002" in url:
            return fake_response(content=make_7z("2002.txt", "data"), url=url)
        return fake_response(content=MICHIGAN_PAGE.encode())

    with (
        mock.patch.object(michigan.requests, "get", side_effect=fake_get),
        pytest.raises(DownloadError, match="2001") as error,
    ):
        michigan.download_MI_data(output_directory=tmp_path)

    # the other year was still collected, and the error names only the bad one
    extracted = tmp_path.resolve() / "LegacyDownloads" / "2002" / "2002.txt"
    assert extracted.read_text() == "data"
    assert "2002" not in str(error.value).replace("2002_mi_cfr", "")
