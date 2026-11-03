import importlib
import io
import pkgutil
from types import SimpleNamespace
from unittest import mock

import pandas as pd
import py7zr
import pytest
import requests
import utils.collect.finance
from utils.cli.core import build_complete_parser
from utils.collect.finance import michigan
from utils.collect.finance.arizona import (
    CATEGORY_TYPES,
    MAX_PAGE_SIZE,
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
    """ArizonaAPI that serves records from memory instead of the network"""

    def __init__(self, n_records: int, max_page_size: int = 100):
        self.records = [{"TransactionID": i} for i in range(n_records)]
        self.max_page_size = max_page_size
        self.calls = []

    def fetch_transaction_data_page(self, start: int = 0, length: int = 1000, **kw):
        self.calls.append({"start": start, "length": length, **kw})
        page = self.records[start : start + min(length, self.max_page_size)]
        return {
            "data": page,
            "recordsTotal": len(self.records) * 2,
            "recordsFiltered": len(self.records),
        }


def make_processor(tmp_path, override_existing_data=False):
    return ArizonaDataProcessor(
        output_path=tmp_path, override_existing_data=override_existing_data
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
        SimpleNamespace(status_code=200, reason="OK", content=b"not a 7z file"),
    ],
    ids=["network-error", "bad-archive"],
)
def test_michigan_failure_skips_only_that_year(tmp_path, first_year_result):
    def fake_get(url, **kwargs):
        if "2001" in url:
            if isinstance(first_year_result, Exception):
                raise first_year_result
            return first_year_result
        if "2002" in url:
            return SimpleNamespace(
                status_code=200, reason="OK", content=make_7z("2002.txt", "data")
            )
        return SimpleNamespace(status_code=200, reason="OK", text=MICHIGAN_PAGE)

    with mock.patch.object(michigan.requests, "get", side_effect=fake_get):
        michigan.download_MI_data(output_directory=tmp_path)

    extracted = tmp_path.resolve() / "LegacyDownloads" / "2002" / "2002.txt"
    assert extracted.read_text() == "data"
