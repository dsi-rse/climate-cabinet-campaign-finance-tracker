import pandas as pd
import pytest
from utils.io import load_database, load_table_chunked, save_database


@pytest.fixture
def sample_database():
    """Create sample database for testing."""
    return {
        "transactions": pd.DataFrame(
            {
                "id": [1, 2, 3, 4, 5],
                "amount": [100.0, 200.0, 150.0, 300.0, 250.0],
                "date": [
                    "2023-01-01",
                    "2023-01-02",
                    "2023-01-03",
                    "2023-01-04",
                    "2023-01-05",
                ],
                "description": [
                    "Purchase A",
                    "Purchase B",
                    "Purchase C",
                    "Purchase D",
                    "Purchase E",
                ],
            }
        ),
        "accounts": pd.DataFrame(
            {
                "account_id": [1, 2, 3],
                "account_name": ["Checking", "Savings", "Investment"],
                "balance": [1000.0, 5000.0, 10000.0],
            }
        ),
    }


@pytest.fixture
def large_sample_database():
    """Create larger sample database for chunking tests."""
    return {
        "large_table": pd.DataFrame(
            {
                "id": range(1, 101),
                "value": [f"value_{i}" for i in range(1, 101)],
                "amount": [i * 10.5 for i in range(1, 101)],
            }
        )
    }


@pytest.fixture
def indexed_database():
    """Create database with named index for testing index handling."""
    transactions = pd.DataFrame(
        {
            "transaction_id": [1, 2, 3],
            "amount": [100.0, 200.0, 150.0],
            "type": ["debit", "credit", "debit"],
        }
    )
    transactions = transactions.set_index("transaction_id")

    return {"indexed_transactions": transactions}


def test_save_and_load_database_csv(tmp_path, sample_database):
    """Test saving and loading database in CSV format."""
    save_database(sample_database, tmp_path, format="csv")

    # Verify files were created
    assert (tmp_path / "transactions.csv").exists()
    assert (tmp_path / "accounts.csv").exists()

    # Load and verify
    loaded_db = next(load_database(tmp_path, format="csv"))

    assert set(loaded_db.keys()) == {"transactions", "accounts"}
    pd.testing.assert_frame_equal(
        loaded_db["transactions"], sample_database["transactions"]
    )
    pd.testing.assert_frame_equal(loaded_db["accounts"], sample_database["accounts"])


def test_save_and_load_database_parquet(tmp_path, sample_database):
    """Test saving and loading database in Parquet format."""
    save_database(sample_database, tmp_path, format="parquet")

    # Verify files were created
    assert (tmp_path / "transactions.parquet").exists()
    assert (tmp_path / "accounts.parquet").exists()

    # Load and verify
    loaded_db = next(load_database(tmp_path, format="parquet"))

    assert set(loaded_db.keys()) == {"transactions", "accounts"}
    pd.testing.assert_frame_equal(
        loaded_db["transactions"], sample_database["transactions"]
    )
    pd.testing.assert_frame_equal(loaded_db["accounts"], sample_database["accounts"])


def test_save_database_creates_directory(tmp_path, sample_database):
    """Test that save_database creates output directory if it doesn't exist."""
    output_path = tmp_path / "new_directory"
    assert not output_path.exists()

    save_database(sample_database, output_path, format="csv")

    assert output_path.exists()
    assert (output_path / "transactions.csv").exists()


def test_load_database_empty_directory(tmp_path):
    """Test loading from empty directory returns empty dict."""
    result = next(load_database(tmp_path, format="csv"))
    assert result == {}


def test_load_database_mixed_files(tmp_path, sample_database):
    """Test loading ignores files with wrong extension."""
    save_database(sample_database, tmp_path, format="csv")

    # Add file with wrong extension
    (tmp_path / "other_file.txt").write_text("not a csv")

    loaded_db = next(load_database(tmp_path, format="csv"))
    assert set(loaded_db.keys()) == {"transactions", "accounts"}


def test_save_database_append_mode_csv(tmp_path):
    """Test appending to existing CSV files."""
    # Save initial data
    initial_data = {"test_table": pd.DataFrame({"id": [1, 2], "value": ["a", "b"]})}
    save_database(initial_data, tmp_path, format="csv", mode="overwrite")

    # Append additional data
    additional_data = {"test_table": pd.DataFrame({"id": [3, 4], "value": ["c", "d"]})}
    save_database(additional_data, tmp_path, format="csv", mode="append")

    # Verify combined result
    loaded_db = next(load_database(tmp_path, format="csv"))
    expected = pd.DataFrame({"id": [1, 2, 3, 4], "value": ["a", "b", "c", "d"]})
    pd.testing.assert_frame_equal(loaded_db["test_table"], expected)


def test_save_database_append_mode_parquet(tmp_path):
    """Test appending to existing Parquet files."""
    # Save initial data
    initial_data = {"test_table": pd.DataFrame({"id": [1, 2], "value": ["a", "b"]})}
    save_database(initial_data, tmp_path, format="parquet", mode="overwrite")

    # Append additional data
    additional_data = {"test_table": pd.DataFrame({"id": [3, 4], "value": ["c", "d"]})}
    save_database(additional_data, tmp_path, format="parquet", mode="append")

    # Verify combined result
    loaded_db = next(load_database(tmp_path, format="parquet"))
    expected = pd.DataFrame({"id": [1, 2, 3, 4], "value": ["a", "b", "c", "d"]})
    pd.testing.assert_frame_equal(loaded_db["test_table"], expected)


def test_save_database_overwrite_mode(tmp_path):
    """Test overwriting existing files."""
    # Save initial data
    initial_data = {
        "test_table": pd.DataFrame({"id": [1, 2], "value": ["old", "data"]})
    }
    save_database(initial_data, tmp_path, format="csv")

    # Overwrite with new data
    new_data = {"test_table": pd.DataFrame({"id": [3, 4], "value": ["new", "data"]})}
    save_database(new_data, tmp_path, format="csv", mode="overwrite")

    # Verify only new data exists
    loaded_db = next(load_database(tmp_path, format="csv"))
    pd.testing.assert_frame_equal(loaded_db["test_table"], new_data["test_table"])


def test_indexed_dataframe_handling(tmp_path, indexed_database):
    """Test handling of DataFrames with named indices."""
    save_database(indexed_database, tmp_path, format="csv")

    loaded_db = next(load_database(tmp_path, format="csv"))

    # CSV should preserve the index as a column
    expected_columns = {"transaction_id", "amount", "type"}
    assert set(loaded_db["indexed_transactions"].columns) == expected_columns


def test_load_database_chunked_csv(tmp_path, large_sample_database):
    """Test chunked loading with CSV format."""
    save_database(large_sample_database, tmp_path, format="csv")

    chunks = list(load_database(tmp_path, format="csv", chunk_size=25))

    # Should have 4 chunks (100 rows / 25 chunk_size)
    assert len(chunks) == 4

    # Verify each chunk has the expected structure
    for chunk_db in chunks:
        assert "large_table" in chunk_db
        assert len(chunk_db["large_table"]) <= 25

    # Verify total rows when concatenated
    all_chunks = pd.concat(
        [chunk["large_table"] for chunk in chunks], ignore_index=True
    )
    assert len(all_chunks) == 100


def test_load_database_chunked_parquet(tmp_path, large_sample_database):
    """Test chunked loading with Parquet format."""
    save_database(large_sample_database, tmp_path, format="parquet")

    chunks = list(load_database(tmp_path, format="parquet", chunk_size=30))

    # Should have 4 chunks (100 rows / 30 chunk_size, with remainder)
    assert len(chunks) == 4

    # Verify chunk sizes
    chunk_sizes = [len(chunk["large_table"]) for chunk in chunks]
    assert chunk_sizes == [30, 30, 30, 10]


def test_load_table_chunked_csv(tmp_path, large_sample_database):
    """Test loading single table in chunks from CSV."""
    save_database(large_sample_database, tmp_path, format="csv")
    table_path = tmp_path / "large_table.csv"

    chunks = list(load_table_chunked(table_path, chunk_size=20, format="csv"))

    assert len(chunks) == 5  # 100 rows / 20 chunk_size

    # Verify total rows
    total_rows = sum(len(chunk) for chunk in chunks)
    assert total_rows == 100


def test_load_table_chunked_parquet(tmp_path, large_sample_database):
    """Test loading single table in chunks from Parquet."""
    save_database(large_sample_database, tmp_path, format="parquet")
    table_path = tmp_path / "large_table.parquet"

    chunks = list(load_table_chunked(table_path, chunk_size=15, format="parquet"))

    assert len(chunks) == 7  # 100 rows / 15 chunk_size (with remainder)

    # Verify chunk sizes
    chunk_sizes = [len(chunk) for chunk in chunks]
    assert chunk_sizes == [15, 15, 15, 15, 15, 15, 10]


def test_load_database_chunked_multiple_tables(tmp_path, sample_database):
    """Test chunked loading handles multiple tables correctly."""
    save_database(sample_database, tmp_path, format="csv")

    chunks = list(load_database(tmp_path, format="csv", chunk_size=2))

    # Should process both tables in chunks
    assert len(chunks) == 3  # Max(5/2, 3/2) = 3 chunks needed

    for chunk_db in chunks:
        # Each chunk should contain data from available tables
        if "transactions" in chunk_db:
            assert len(chunk_db["transactions"]) <= 2
        if "accounts" in chunk_db:
            assert len(chunk_db["accounts"]) <= 2


def test_load_database_no_matching_files(tmp_path):
    """Test loading database when no files match the format."""
    # Create files with wrong extensions
    (tmp_path / "data.txt").write_text("not a csv")
    (tmp_path / "other.json").write_text("{}")

    result = next(load_database(tmp_path, format="csv"))
    assert result == {}


def test_load_database_chunked_empty_directory(tmp_path):
    """Test chunked loading from empty directory."""
    chunks = list(load_database(tmp_path, format="csv", chunk_size=10))
    assert chunks == []
