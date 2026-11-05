"""Generate synthetic MN raw files that mirror the real file layouts.

All names, ids, addresses and amounts are fake. Headers and value formats are
copied from the "All" downloads at cfb.mn.gov (checked November 2026). Since
2026 the expenditure and independent expenditure files no longer include the
vendor street address (Vendor address 1 and 2); the config reads columns by
header name so the older files with those columns still load. The older
layout is tested in tests/test_state_golden_rows.py with MN_OLD_EXPENDITURES.

Each file has three rows, the number tests/test_state_configs.py expects.

Run from the repository root:
    uv run python tests/data/standardize/make_mn_fixtures.py
"""

import csv
from pathlib import Path

RAW_DIRECTORY = Path(__file__).parent / "raw"

MN_CONTRIBUTIONS = '"Recipient reg num",Recipient,"Recipient type","Recipient subtype",Amount,"Receipt date",Year,Contributor,"Contrib Reg Num","Contrib type","Receipt type","In kind?","In-kind descr","Contrib zip","Contrib Employer name"'
MN_EXPENDITURES = '"Committee reg num","Committee name","Entity type","Entity sub-type","Vendor name","Vendor city","Vendor state","Vendor zip",Amount,"Unpaid amount",Date,Purpose,Year,Type,"In-kind descr",In-kind?,"Affected committee name","Affected committee reg num"'
# before 2026, with the vendor street address
MN_OLD_EXPENDITURES = '"Committee reg num","Committee name","Entity type","Entity sub-type","Vendor name","Vendor address 1","Vendor address 2","Vendor city","Vendor state","Vendor zip",Amount,"Unpaid amount",Date,Purpose,Year,Type,"In-kind descr",In-kind?,"Affected committee name","Affected committee reg num"'
MN_INDEPENDENT_EXPENDITURES = 'Spender,"Spender Reg Num","Spender type","Spender sub-type","Affected Comte Name","Affected Cmte Reg Num","For /Against",Year,Date,Type,Amount,"Unpaid amount","In kind?","In kind descr",Purpose,"Vendor name","Vendor city","Vendor State","Vendor zip"'

# rows written in the real files' value formats (amounts with four decimals)
EXPENDITURE_ROWS = [
    {
        "Committee reg num": "90001",
        "Committee name": "Testperson, Jane House Committee",
        "Entity type": "PCC",
        "Vendor name": "Example Print Shop",
        "Vendor address 1": "100 Main St",
        "Vendor city": "Duluth",
        "Vendor state": "MN",
        "Vendor zip": "55802",
        "Amount": "250.0000",
        "Unpaid amount": ".0000",
        "Date": "2026-08-14",
        "Purpose": "Lawn signs",
        "Year": "2026",
        "Type": "Campaign Expenditure",
        "In-kind?": "No",
    },
    {
        "Committee reg num": "90002",
        "Committee name": "Example Industry Political Fund",
        "Entity type": "PCF",
        "Entity sub-type": "PF",
        "Vendor name": "Example Consulting LLC",
        "Vendor city": "Saint Paul",
        "Vendor state": "MN",
        "Vendor zip": "55101",
        "Amount": "1200.5000",
        "Unpaid amount": ".0000",
        "Date": "2026-09-02",
        "Purpose": "Consulting",
        "Year": "2026",
        "Type": "Non-Campaign Disbursement",
        "In-kind?": "No",
    },
    {
        "Committee reg num": "90003",
        "Committee name": "Example County Party Unit",
        "Entity type": "PTU",
        "Vendor name": "Example Diner",
        "Vendor city": "Rochester",
        "Vendor state": "MN",
        "Vendor zip": "55901",
        "Amount": "45.1000",
        "Unpaid amount": ".0000",
        "Date": "2026-10-01",
        "Purpose": "Volunteer meal",
        "Year": "2026",
        "Type": "Campaign Expenditure",
        "In-kind?": "No",
    },
]


def write(path: Path, header: str, rows: list[dict]) -> None:
    """Write rows (dicts of header name to value, others blank) under header

    Columns in a row but not in the header are left out, so one set of rows
    can be written in both expenditure layouts.
    """
    columns = next(csv.reader([header]))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([row.get(column, "") for column in columns])


def mn() -> None:
    """Minnesota Campaign Finance Board "All" downloads"""
    directory = RAW_DIRECTORY / "mn"
    write(
        directory / "contributions_all.csv",
        MN_CONTRIBUTIONS,
        [
            {
                "Recipient reg num": "90001",
                "Recipient": "Testperson, Jane House Committee",
                "Recipient type": "PCC",
                "Amount": f"{100 + i}.0000",
                "Receipt date": f"2026-07-1{i}",
                "Year": "2026",
                "Contributor": f"Donor, Alex{i}",
                "Contrib type": "Individual",
                "Receipt type": "Contribution",
                "In kind?": "No",
                "Contrib zip": "55401",
            }
            for i in range(3)
        ],
    )
    write(directory / "expenditures_all.csv", MN_EXPENDITURES, EXPENDITURE_ROWS)
    write(
        directory / "independent_expenditures_all.csv",
        MN_INDEPENDENT_EXPENDITURES,
        [
            {
                "Spender": "Example Action Fund",
                "Spender Reg Num": f"8000{i}",
                "Spender type": "PCF",
                "Spender sub-type": "IEF",
                "Affected Comte Name": "Sample, Sam Senate Committee",
                "Affected Cmte Reg Num": "90004",
                "For /Against": "Against" if i else "For",
                "Year": "2026",
                "Date": f"2026-09-2{i}",
                "Type": "Independent Expenditure",
                "Amount": f"{5000 + i}.00",
                "Unpaid amount": ".00",
                "In kind?": "No",
                "Purpose": "Mail",
                "Vendor name": f"Example Mailhouse {i}",
                "Vendor city": "Des Moines",
                "Vendor State": "IA",
                "Vendor zip": "50309",
            }
            for i in range(3)
        ],
    )


if __name__ == "__main__":
    mn()
