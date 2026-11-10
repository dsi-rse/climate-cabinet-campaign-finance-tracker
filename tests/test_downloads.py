"""Collectors keep the original archives and log where each download came from"""

import hashlib
import io
import zipfile
from pathlib import Path
from unittest import mock

import py7zr
import pytest
import requests
from utils.collect.downloads import (
    ARCHIVE_DIRECTORY,
    DOWNLOAD_MANIFEST_NAME,
    archive_path,
    read_download_records,
    save_response,
)
from utils.collect.election import minnesota as mn_election
from utils.collect.finance import michigan, pennsylvania, texas
from utils.collect.finance import minnesota as mn_finance
from utils.standardize.source import DataSourceStandardizationPipeline

CONFIG_DIRECTORY = (
    Path(__file__).parents[1] / "src" / "utils" / "standardize" / "finance" / "config"
)
RAW_FIXTURE_DIRECTORY = Path(__file__).parent / "data" / "standardize" / "raw"
TX_URL = "https://prd.tecprd.ethicsefile.com/public/cf/public/TEC_CF_CSV.zip"
SERVER_HEADERS = {
    "ETag": '"abc123"',
    "Last-Modified": "Mon, 09 Nov 2026 06:00:00 GMT",
    "Content-Type": "application/zip",
}


def sha256(data: bytes) -> str:
    """Hash bytes

    Args:
        data: Bytes to hash.

    Returns:
        The sha256 as a hex string.
    """
    return hashlib.sha256(data).hexdigest()


def make_zip(files: dict[str, bytes]) -> bytes:
    """Build a zip archive in memory

    Args:
        files: Mapping of names inside the archive to their contents.

    Returns:
        The zip file's bytes.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def tx_fixture_zip() -> tuple[bytes, dict[str, bytes]]:
    """Zip the Texas fixture files like the commission's bulk download

    Returns:
        The zip's bytes, and the files inside it by name.
    """
    files = {
        path.name: path.read_bytes()
        for path in sorted((RAW_FIXTURE_DIRECTORY / "tx").iterdir())
    }
    return make_zip(files), files


def run_tx_collector(output_directory: Path, zip_bytes: bytes, fake_response, now):
    """Run the Texas collector against a faked download

    Args:
        output_directory: Directory to download to.
        zip_bytes: Body the fake server returns.
        fake_response: Builds a requests.Response (fixture).
        now: Retrieval time the collector records.
    """
    response = fake_response(content=zip_bytes, headers=SERVER_HEADERS, url=TX_URL)
    with (
        mock.patch.object(texas.requests, "get", return_value=response),
        mock.patch.object(texas, "utc_now", return_value=now),
    ):
        texas.download_TX_data(output_directory=output_directory)


def test_archive_path_adds_the_retrieval_time(tmp_path):
    """Archives are named after the URL's file with the time before the extension

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).
    """
    path = archive_path(tmp_path, TX_URL, "2026-11-10T16:32:25+00:00")
    assert path == tmp_path / ARCHIVE_DIRECTORY / "TEC_CF_CSV.20261110T163225Z.zip"
    path.parent.mkdir()
    path.write_bytes(b"first")
    again = archive_path(tmp_path, TX_URL, "2026-11-10T16:32:25+00:00")
    assert again.name == "TEC_CF_CSV.20261110T163225Z-1.zip"


def test_save_response_writes_and_describes_the_body(tmp_path, fake_response):
    """The body is written whole and its size, hash and headers are recorded

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).
        fake_response: Builds a requests.Response (fixture).
    """
    body = b"x" * 3_000_000
    response = fake_response(content=body, headers=SERVER_HEADERS, url=TX_URL)
    record = save_response(response, tmp_path / "sub" / "file.zip", tmp_path)

    assert (tmp_path / "sub" / "file.zip").read_bytes() == body
    assert not list(tmp_path.rglob("*.part"))
    assert record["path"] == "sub/file.zip"
    assert record["size"] == len(body)
    assert record["sha256"] == sha256(body)
    assert record["etag"] == '"abc123"'
    assert record["last_modified"] == "Mon, 09 Nov 2026 06:00:00 GMT"
    assert record["status"] == 200


def test_texas_keeps_each_daily_zip_and_logs_it(tmp_path, fake_response):
    """Each run keeps its zip and logs the URL, headers and extracted files

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).
        fake_response: Builds a requests.Response (fixture).
    """
    zip_bytes, files = tx_fixture_zip()
    run_tx_collector(tmp_path, zip_bytes, fake_response, "2026-11-09T06:00:00+00:00")
    run_tx_collector(tmp_path, zip_bytes, fake_response, "2026-11-10T06:00:00+00:00")

    archives = sorted(path.name for path in (tmp_path / ARCHIVE_DIRECTORY).iterdir())
    assert archives == [
        "TEC_CF_CSV.20261109T060000Z.zip",
        "TEC_CF_CSV.20261110T060000Z.zip",
    ]
    records = read_download_records(tmp_path)
    assert [record["retrieved_at"] for record in records] == [
        "2026-11-09T06:00:00+00:00",
        "2026-11-10T06:00:00+00:00",
    ]
    record = records[-1]
    assert record["url"] == TX_URL
    assert record["path"] == f"{ARCHIVE_DIRECTORY}/{archives[-1]}"
    assert record["sha256"] == sha256(zip_bytes)
    assert record["etag"] == '"abc123"'
    assert {file["path"]: file["sha256"] for file in record["extracted"]} == {
        name: sha256(content) for name, content in files.items()
    }


def test_standardize_ignores_archives_and_the_download_log(tmp_path, fake_response):
    """The kept zips and the log don't match any form, so standardize skips them

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).
        fake_response: Builds a requests.Response (fixture).
    """
    zip_bytes, files = tx_fixture_zip()
    run_tx_collector(tmp_path, zip_bytes, fake_response, "2026-11-10T06:00:00+00:00")
    assert (tmp_path / DOWNLOAD_MANIFEST_NAME).exists()

    matched = set()
    for form in ("contributions", "expenditures", "filers"):
        pipeline = DataSourceStandardizationPipeline(
            state_code="tx", form_code=form, config_file=CONFIG_DIRECTORY / "tx.yaml"
        )
        matched |= {path.name for path in pipeline._raw_data_file_paths(tmp_path)}
    assert matched == set(files)


def test_pennsylvania_keeps_each_year_zip_and_logs_it(tmp_path, fake_response):
    """Each year's zip is kept and logged with the files it held

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).
        fake_response: Builds a requests.Response (fixture).
    """
    zip_bytes = make_zip({"contrib_2024.txt": b"a,b\n1,2\n"})

    def fake_get(url, **kwargs):
        return fake_response(content=zip_bytes, url=url)

    with mock.patch.object(pennsylvania.requests, "get", side_effect=fake_get):
        pennsylvania.download_PA_data(2024, 2024, output_directory=tmp_path)

    (record,) = read_download_records(tmp_path)
    assert record["url"].endswith("/2024.zip")
    assert (tmp_path / record["path"]).read_bytes() == zip_bytes
    assert [file["path"] for file in record["extracted"]] == ["2024/contrib_2024.txt"]


@pytest.mark.parametrize("first_year_is_valid", [True, False])
def test_michigan_logs_every_download_even_a_bad_archive(
    tmp_path, fake_response, first_year_is_valid
):
    """A 7z that fails to extract is still kept and logged, with nothing extracted

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).
        fake_response: Builds a requests.Response (fixture).
        first_year_is_valid: Whether the 2001 archive is a real 7z file.
    """
    page = '<a href="/-/media/Legacy-Data/2001_mi_cfr.7z">2001</a>'
    buffer = io.BytesIO()
    with py7zr.SevenZipFile(buffer, mode="w") as archive:
        archive.writestr("data", "2001_mi_cfr_contributions.csv")
    body = buffer.getvalue() if first_year_is_valid else b"not a 7z file"

    def fake_get(url, **kwargs):
        if url.endswith(".7z"):
            return fake_response(content=body, url=url)
        return fake_response(content=page.encode())

    with mock.patch.object(michigan.requests, "get", side_effect=fake_get):
        michigan.download_MI_data(output_directory=tmp_path)

    (record,) = read_download_records(tmp_path.resolve())
    assert record["path"].startswith(f"{ARCHIVE_DIRECTORY}/2001_mi_cfr.")
    assert record["sha256"] == sha256(body)
    expected = (
        ["LegacyDownloads/2001/2001_mi_cfr_contributions.csv"]
        if first_year_is_valid
        else []
    )
    assert [file["path"] for file in record["extracted"]] == expected


def test_minnesota_election_file_is_logged(tmp_path, fake_response):
    """Each election results file is logged where it was saved

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).
        fake_response: Builds a requests.Response (fixture).
    """
    body = b"MN;;;;\n"
    response = fake_response(content=body, headers={"Content-Type": "text/plain"})
    with mock.patch.object(mn_election.requests, "get", return_value=response):
        mn_election.download_result_file(
            mn_election.Election("20221108", "general"), "stsenate.txt", tmp_path
        )

    (record,) = read_download_records(tmp_path)
    assert record["path"] == "20221108/stsenate.txt"
    assert record["sha256"] == sha256(body)


def interrupted_response(fake_response, first_chunk: bytes):
    """Build a response whose body fails after its first chunk

    Args:
        fake_response: Builds a requests.Response (fixture).
        first_chunk: Bytes sent before the connection drops.

    Returns:
        The response.
    """
    response = fake_response(url=TX_URL)

    def iter_content(chunk_size=1):
        yield first_chunk
        raise requests.ConnectionError("connection dropped")

    response.iter_content = iter_content
    return response


def test_interrupted_download_leaves_no_partial_file(tmp_path, fake_response):
    """A download that fails partway keeps the earlier file and no .part file

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).
        fake_response: Builds a requests.Response (fixture).
    """
    path = tmp_path / "file.csv"
    path.write_bytes(b"earlier download")
    with pytest.raises(requests.ConnectionError):
        save_response(interrupted_response(fake_response, b"new"), path, tmp_path)

    assert path.read_bytes() == b"earlier download"
    assert not list(tmp_path.rglob("*.part"))


def test_redirected_download_records_the_requested_url(tmp_path, fake_response):
    """url is the address asked for and final_url the one redirected to

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).
        fake_response: Builds a requests.Response (fixture).
    """
    requested = "https://example.com/data/2024.zip"
    final = "https://cdn.example.com/data/2024.zip"
    redirect = fake_response(status_code=302, url=requested)
    redirect.request = requests.Request("GET", requested).prepare()
    response = fake_response(content=b"zip", url=final)
    response.request = requests.Request("GET", final).prepare()
    response.history = [redirect]

    record = save_response(response, tmp_path / "2024.zip", tmp_path)
    assert record["url"] == requested
    assert record["final_url"] == final


def test_texas_logs_a_download_whose_zip_is_bad(tmp_path, fake_response):
    """A zip that can't be opened is still kept and logged, with no files

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).
        fake_response: Builds a requests.Response (fixture).
    """
    run_tx_collector(tmp_path, b"not a zip", fake_response, "2026-11-10T06:00:00+00:00")

    (record,) = read_download_records(tmp_path)
    assert record["sha256"] == sha256(b"not a zip")
    assert record["extracted"] == []
    assert (tmp_path / record["path"]).read_bytes() == b"not a zip"


def test_pennsylvania_logs_a_download_whose_zip_is_bad(tmp_path, fake_response):
    """A bad zip is logged before the error stops the collector

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).
        fake_response: Builds a requests.Response (fixture).
    """

    def fake_get(url, **kwargs):
        return fake_response(content=b"not a zip", url=url)

    with (
        mock.patch.object(pennsylvania.requests, "get", side_effect=fake_get),
        pytest.raises(zipfile.BadZipFile),
    ):
        pennsylvania.download_PA_data(2024, 2024, output_directory=tmp_path)

    (record,) = read_download_records(tmp_path)
    assert record["sha256"] == sha256(b"not a zip")
    assert record["extracted"] == []


MN_FINANCE_PAGE = b"""
<html><body>
<h1>Itemized contributions received of over $200</h1>
<table><tr><td>All</td><td>2015-2026</td><td><a href="/files/contribs.csv">CSV</a></td></tr></table>
</body></html>
"""


def test_minnesota_finance_files_are_logged(tmp_path, fake_response):
    """Each Minnesota finance file is saved and logged with its hash

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).
        fake_response: Builds a requests.Response (fixture).
    """
    body = b"Recipient reg num,Amount\n1234,250\n"

    def fake_get(url, **kwargs):
        if url.endswith("contribs.csv"):
            return fake_response(content=body, url=url)
        return fake_response(content=MN_FINANCE_PAGE, url=url)

    with mock.patch.object(mn_finance.requests, "get", side_effect=fake_get):
        mn_finance.download_MN_data(output_directory=tmp_path)

    assert (tmp_path / "contributions_all.csv").read_bytes() == body
    (record,) = read_download_records(tmp_path)
    assert record["url"].endswith("/files/contribs.csv")
    assert record["path"] == "contributions_all.csv"
    assert record["sha256"] == sha256(body)
