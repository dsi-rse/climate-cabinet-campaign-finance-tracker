import pandas as pd
import pytest
from utils.database import (
    connect_duckdb,
    create_transactor_detailed_view,
    table_exists,
)


@pytest.fixture
def db_path(tmp_path):
    """Path for a tiny DuckDB file."""
    return tmp_path / "test.duckdb"


@pytest.fixture
def con(db_path):
    """DuckDB connection to a tiny on-disk database; closes after use."""
    con = connect_duckdb(db_path)
    try:
        yield con
    finally:
        con.close()


@pytest.fixture
def tiny_duckdb(con):
    """Create minimal schema and data for Transactor, Membership, Address."""
    # Core tables
    con.execute(
        """
        CREATE TABLE Transactor (
            id INTEGER,
            name VARCHAR,
            title VARCHAR
        )
        """
    )
    con.execute(
        """
        CREATE TABLE Membership (
            member_id INTEGER,
            organization_id INTEGER,
            membership_type VARCHAR,
            position VARCHAR,
            department VARCHAR
        )
        """
    )
    con.execute(
        """
        CREATE TABLE Address (
            transactor_id INTEGER,
            street VARCHAR,
            city VARCHAR,
            state VARCHAR,
            zip VARCHAR
        )
        """
    )

    # Transactors: 2 members (1,2) and 2 orgs (10,20)
    con.execute(
        """
        INSERT INTO Transactor VALUES
            (1,  'Alice', 'Engineer'),
            (2,  'Bob',   'Analyst'),
            (10, 'OrgA',  'Employer'),
            (20, 'OrgB',  'Employer')
        """
    )

    # Memberships (duplicate row for member 1 to exercise max count selection)
    con.execute(
        """
        INSERT INTO Membership VALUES
            (1, 10, 'Employee', 'Full-time',  'R&D'),
            (1, 10, 'Employee', 'Full-time',  'R&D'),
            (2, 20, 'Employee', 'Contractor', 'Ops')
        """
    )

    # Addresses (duplicate for transactor 1; address for org 10)
    con.execute(
        """
        INSERT INTO Address VALUES
            (1,  '1 Main',   'Town', 'TX', '11111'),
            (1,  '1 Main',   'Town', 'TX', '11111'),
            (10, '100 Corp', 'City', 'CA', '90000')
        """
    )


def test_connect_duckdb_creates_file(db_path):
    """Connecting to a DuckDB path creates a persistent file."""
    con = connect_duckdb(db_path)
    try:
        assert db_path.exists()
    finally:
        con.close()


def test_table_exists_reports_presence(con, tiny_duckdb):
    """table_exists reflects actual table presence."""
    assert table_exists(con, "Transactor")
    assert table_exists(con, "Membership")
    assert table_exists(con, "Address")
    assert not table_exists(con, "DoesNotExist")


def test_create_transactor_detailed_view_contents(con, tiny_duckdb):
    """Detailed table contains base, employer_*, and address_* with expected values."""
    create_transactor_detailed_view(con)
    assert table_exists(con, "transactor_detailed_view")

    # Columns include aliased employer/address fields
    cols = [
        c[1]
        for c in con.execute("PRAGMA table_info('transactor_detailed_view')").fetchall()
    ]
    expected_subset = {
        "id",
        "name",
        "title",
        "employer_position",
        "employer_department",
        "employer_name",
        "employer_title",
        "address_street",
        "address_city",
        "address_state",
        "address_zip",
    }
    assert expected_subset.issubset(set(cols))

    transactor_detailed_view = con.execute(
        """
        SELECT
            id, name,
            employer_name, employer_title, employer_position, employer_department,
            address_street
        FROM transactor_detailed_view
        ORDER BY id
        """
    ).fetchdf()

    # Member 1: has employer OrgA and an address
    row1 = transactor_detailed_view[transactor_detailed_view["id"] == 1].iloc[0]
    assert row1["name"] == "Alice"
    assert row1["employer_name"] == "OrgA"
    assert row1["employer_title"] == "Employer"
    assert row1["employer_position"] == "Full-time"
    assert row1["employer_department"] == "R&D"
    assert row1["address_street"] == "1 Main"

    # Member 2: has employer OrgB, no address
    row2 = transactor_detailed_view[transactor_detailed_view["id"] == 2].iloc[0]
    assert row2["name"] == "Bob"
    assert row2["employer_name"] == "OrgB"
    assert row2["employer_position"] == "Contractor"
    assert pd.isna(row2["address_street"])

    # Org 10: no employer_*, has its own address
    row10 = transactor_detailed_view[transactor_detailed_view["id"] == 10].iloc[0]
    assert row10["name"] == "OrgA"
    assert pd.isna(row10["employer_name"])
    assert row10["address_street"] == "100 Corp"


def test_create_transactor_detailed_view_is_idempotent(con, tiny_duckdb):
    """Running the builder twice keeps content consistent."""
    create_transactor_detailed_view(con)
    first = con.execute("SELECT COUNT(*) FROM transactor_detailed_view").fetchone()[0]

    # Run again; should drop and recreate without error
    create_transactor_detailed_view(con)
    second = con.execute("SELECT COUNT(*) FROM transactor_detailed_view").fetchone()[0]

    assert first == second


def test_tied_addresses_are_picked_whole_and_the_same_way_every_time(con):
    """With two equally common addresses, one whole address is picked, consistently

    Args:
        con: DuckDB connection to an empty database.
    """
    con.execute("CREATE TABLE Transactor (id VARCHAR, first_name VARCHAR)")
    con.execute(
        "CREATE TABLE Membership (member_id VARCHAR, organization_id VARCHAR,"
        " membership_type VARCHAR)"
    )
    con.execute(
        "CREATE TABLE Address (transactor_id VARCHAR, street_name VARCHAR,"
        " city VARCHAR, latest_known_date DATE)"
    )
    con.execute("INSERT INTO Transactor VALUES ('p1', 'Ann'), ('p2', 'Bob')")
    con.execute(
        """
        INSERT INTO Address VALUES
            ('p1', 'oak ave', 'Austin', NULL),
            ('p1', 'main st', 'Reno',   NULL),
            ('p2', 'elm rd',  'Denver', DATE '2020-01-01'),
            ('p2', 'pine ct', 'Boise',  DATE '2024-01-01')
        """
    )
    picks = []
    for _ in range(3):
        create_transactor_detailed_view(con)
        picks.append(
            con.execute(
                "SELECT id, address_street_name, address_city"
                " FROM transactor_detailed_view ORDER BY id"
            ).fetchall()
        )
    # p1: no dates, so alphabetical; p2: the latest address
    assert picks == [[("p1", "main st", "reno"), ("p2", "pine ct", "boise")]] * 3


def test_compared_text_columns_are_lowercased(con):
    """Names and address fields linkage compares are lowercased and trimmed

    Args:
        con: DuckDB connection to an empty database.
    """
    con.execute(
        "CREATE TABLE Transactor (id VARCHAR, first_name VARCHAR, last_name VARCHAR,"
        " party VARCHAR)"
    )
    con.execute(
        "CREATE TABLE Membership (member_id VARCHAR, organization_id VARCHAR,"
        " membership_type VARCHAR)"
    )
    con.execute("CREATE TABLE Address (transactor_id VARCHAR, city VARCHAR)")
    con.execute("INSERT INTO Transactor VALUES ('p1', ' ANN ', 'Smith', 'Democrat')")
    con.execute("INSERT INTO Address VALUES ('p1', 'CHICAGO')")
    create_transactor_detailed_view(con)
    assert con.execute(
        "SELECT first_name, last_name, address_city, party FROM transactor_detailed_view"
    ).fetchall() == [("ann", "smith", "chicago", "Democrat")]
