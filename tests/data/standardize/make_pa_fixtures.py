"""Generate synthetic PA raw files that mirror the real file layouts.

All names, addresses and amounts are fake. Layouts (verified against the files
published by the PA Department of State):
- 2002: legacy layout, no header row.
- all other years: legacy columns plus CampaignFinanceID and SubmittedDate,
  with a header row.
"""

import csv
from pathlib import Path

HEADERS = {
    "contrib": "CampaignFinanceID,FilerID,EYEAR,SubmittedDate,CYCLE,Section,CONTRIBUTOR,ADDRESS1,ADDRESS2,CITY,STATE,ZIPCODE,OCCUPATION,ENAME,EADDRESS1,EADDRESS2,ECITY,ESTATE,EZIPCODE,CONTDATE1,CONTAMT1,CONTDATE2,CONTAMT2,CONTDATE3,CONTAMT3,CONTDESC",
    "filer": "CampaignfinanceID,FILERID,EYEAR,SubmittedDate,CYCLE,AMMEND,TERMINATE,FILERTYPE,FILERNAME,OFFICE,DISTRICT,PARTY,ADDRESS1,ADDRESS2,CITY,STATE,ZIPCODE,COUNTY,PHONE,BEGINNING,MONETARY,INKIND",
    "expense": "CampaignFinanceID,FILERID,EYEAR,SubmittedDate,CYCLE,EXPNAME,ADDRESS1,ADDRESS2,CITY,STATE,ZIPCODE,EXPDATE,EXPAMT,EXPDESC",
}


def new_format_rows(kind: str, year: int) -> list[list[str]]:
    """Three fake rows in the current (headered) layout"""
    rows = []
    for i in range(3):
        cfid, filer_id, submitted = (
            f"{year}{i:03d}",
            f"9900{i:03d}",
            f"{year}-05-0{i + 1}",
        )
        if kind == "contrib":
            rest = [
                "2",
                "IB",
                f"Jane Q Testperson{i}",
                f"{i + 1} Example St",
                "",
                "Springfield",
                "PA",
                "17000",
                "Teacher",
                "Example School",
                "",
                "",
                "",
                "",
                "",
                f"{year}040{i + 1}",
                "25.0000",
                "0",
                "0",
                "0",
                "0",
                "",
            ]
        elif kind == "filer":
            rest = [
                "1",
                "N",
                "N",
                "2",
                f"FRIENDS OF TESTPERSON {i}",
                "STH",
                "4",
                "DEM",
                f"{i + 1} MAIN ST",
                "",
                "SPRINGFIELD",
                "PA",
                "17000",
                "",
                "",
                "100.0000",
                "0.0000",
                ".0000",
            ]
        else:
            rest = [
                "2",
                f"Example Vendor {i}",
                f"{i + 1} Market St",
                "",
                "Springfield",
                "PA",
                "17000",
                f"{year}030{i + 1}",
                "50.0000",
                "Printing",
            ]
        rows.append([cfid, filer_id, str(year), submitted, *rest])
    return rows


def write_fixtures(root: Path) -> None:
    """Write fixture files for one legacy and two current-layout years"""
    for year in (2002, 2003, 2024):
        year_dir = root / "pa" / str(year)
        year_dir.mkdir(parents=True, exist_ok=True)
        for kind, header in HEADERS.items():
            rows = new_format_rows(kind, year)
            with (year_dir / f"{kind}_{year}.txt").open("w", newline="") as f:
                writer = csv.writer(f, lineterminator="\n")
                if year == 2002:
                    # legacy layout lacks CampaignFinanceID and SubmittedDate
                    writer.writerows([r[1:3] + r[4:] for r in rows])
                else:
                    f.write(header + "\n")
                    writer.writerows(rows)


if __name__ == "__main__":
    write_fixtures(Path(__file__).parent / "raw")
