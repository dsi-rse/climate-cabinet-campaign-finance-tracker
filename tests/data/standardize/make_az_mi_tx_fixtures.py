"""Generate synthetic AZ, MI and TX raw files that mirror the real file layouts.

All names, ids, addresses and amounts are fake. Headers and value formats are
copied from the files each state publishes (checked against the files in
cft_test_data.zip and, for the Arizona Access export, the full 2025-04-15
export):
- AZ Advanced Search: dates as /Date(unix ms)/ and
  TransactionTypeDispositionID 1 for income, 2 for expenses. Amounts are
  positive except refunds and loan repayments, which are negative entries on
  their side (a refund the committee pays is a negative income).
- AZ Access export: dates with a midnight time (9/10/2023 0:00:00) and amounts
  like $50.00, with money leaving the committee in parentheses ($745.00).
- MI: the expenditure file names the payee column lname_or_org (receipts and
  contributions use l_name_or_org) and leaves column 29's header blank.

Each file has three rows, the number tests/test_state_configs.py expects. The
rows are chosen so tests/test_state_golden_rows.py can check who paid whom.

Run from the repository root:
    uv run python tests/data/standardize/make_az_mi_tx_fixtures.py
"""

import csv
from pathlib import Path

RAW_DIRECTORY = Path(__file__).parent / "raw"

AZ_ADVANCED_SEARCH_TRANSACTIONS = "TransactionDate,CommitteeID,CommitteeName,CommitteeGroupNumber,Amount,TransactionName,TransactionType,TransactionNameID,TransactionGroupNumber,TransactionTypeDispositionID,Occupation,Employer,City,State,ZipCode,EntityDescription,Memo,TransactionTypeId,FirstName,LastName,FilerName"
AZ_ADVANCED_SEARCH_COMMITTEES = "CommitteeName,PhoneNo,Email,Status,LastAmendedDate,LastFiledDate,MasterCommitteeId,CommitteeTypeName,RegistrationDate,CommitteeAddress,MailingAddress,Chairman,Treasurer,Candidate,CandidatePhoneNo,CandidateEmail,Designee,OfficeName,PartyName,CountyName,CommitteeID"
AZ_ACCESS_TRANSACTIONS = "TransactionID,ModifiesTransactionID,TransactionTypeID,CommitteeID,TransactionDate,Amount,NameID,IsForBenefit,SubjectCommitteeID,Memo,CategoryID,AccountType,Deleted"
AZ_ACCESS_COMMITTEES = "CommitteeID,NameID,ChairpersonNameID,TreasurerNameID,CandidateNameID,DesigneeNameID,SponsorNameID,BallotMeasureID,PhysicalAddress1,PhysicalAddress2,PhysicalCity,PhysicalState,PhysicalZipCode,SponsorType,SponsorRelationship,OrganizationDate,TerminationDate,BenefitsBallotMeasure,FinancialInstitution1,FinancialInstitution2,FinancialInstitution3,CandidatePartyID,CandidateOfficeID,CandidateCountyID,CandidateIsIncumbent,CandidateCycleID,CandidateOtherPartyName"
AZ_ACCESS_NAMES = "NameID,NameGroupID,EntityTypeID,LastName,FirstName,MiddleName,Suffix,Address1,Address2,City,State,ZipCode,CountyID,Occupation,Employer"

MI_CONTRIBUTIONS = "doc_seq_no,page_no,contribution_id,cont_detail_id,doc_stmnt_year,doc_type_desc,com_legal_name,common_name,cfr_com_id,com_type,can_first_name,can_last_name,contribtype,f_name,l_name_or_org,address,city,state,zip,occupation,employer,received_date,amount,aggregate,extra_desc,RUNTIME: 04-10-2025 05:37:19"
MI_RECEIPTS = "doc_seq_no,ik_code,gub_account_type,gub_elec_type,page_no,receipt_id,detail_id,doc_stmnt_year,doc_type_desc,com_legal_name,common_name,cfr_com_id,com_type,can_first_name,can_last_name,contribtype,f_name,l_name_or_org,address,city,state,zip,occupation,employer,received_date,amount,aggregate,extra_desc,receipttype,RUNTIME: 04-10-2025 05:00:22"
MI_EXPENDITURES = "doc_seq_no,expenditure_type,gub_account_type,gub_elec_type,page_no,expense_id,detail_id,doc_stmnt_year,doc_type_desc,com_legal_name,common_name,cfr_com_id,com_type,schedule_desc,exp_desc,purpose,extra_desc,f_name,lname_or_org,address,city,state,zip,exp_date,amount,state_loc,supp_opp,can_or_ballot,,county,debt_payment,vend_name,vend_addr,vend_city,vend_state,vend_zip,gotv_ink_ind,fundraiser,RUNTIME: 04-10-2025 06:29:45"

TX_CONTRIBUTIONS = "recordType,formTypeCd,schedFormTypeCd,reportInfoIdent,receivedDt,infoOnlyFlag,filerIdent,filerTypeCd,filerName,contributionInfoId,contributionDt,contributionAmount,contributionDescr,itemizeFlag,travelFlag,contributorPersentTypeCd,contributorNameOrganization,contributorNameLast,contributorNameSuffixCd,contributorNameFirst,contributorNamePrefixCd,contributorNameShort,contributorStreetCity,contributorStreetStateCd,contributorStreetCountyCd,contributorStreetCountryCd,contributorStreetPostalCode,contributorStreetRegion,contributorEmployer,contributorOccupation,contributorJobTitle,contributorPacFein,contributorOosPacFlag,contributorLawFirmName,contributorSpouseLawFirmName,contributorParent1LawFirmName,contributorParent2LawFirmName"
TX_EXPENDITURES = "recordType,formTypeCd,schedFormTypeCd,reportInfoIdent,receivedDt,infoOnlyFlag,filerIdent,filerTypeCd,filerName,expendInfoId,expendDt,expendAmount,expendDescr,expendCatCd,expendCatDescr,itemizeFlag,travelFlag,politicalExpendCd,reimburseIntendedFlag,srcCorpContribFlag,capitalLivingexpFlag,payeePersentTypeCd,payeeNameOrganization,payeeNameLast,payeeNameSuffixCd,payeeNameFirst,payeeNamePrefixCd,payeeNameShort,payeeStreetAddr1,payeeStreetAddr2,payeeStreetCity,payeeStreetStateCd,payeeStreetCountyCd,payeeStreetCountryCd,payeeStreetPostalCode,payeeStreetRegion,creditCardIssuer,repaymentDt"
TX_FILERS = "recordType,filerIdent,filerTypeCd,filerName,unexpendContribFilerFlag,modifiedElectCycleFlag,filerJdiCd,committeeStatusCd,ctaSeekOfficeCd,ctaSeekOfficeDistrict,ctaSeekOfficePlace,ctaSeekOfficeDescr,ctaSeekOfficeCountyCd,ctaSeekOfficeCountyDescr,filerPersentTypeCd,filerNameOrganization,filerNameLast,filerNameSuffixCd,filerNameFirst,filerNamePrefixCd,filerNameShort,filerStreetAddr1,filerStreetAddr2,filerStreetCity,filerStreetStateCd,filerStreetCountyCd,filerStreetCountryCd,filerStreetPostalCode,filerStreetRegion,filerMailingAddr1,filerMailingAddr2,filerMailingCity,filerMailingStateCd,filerMailingCountyCd,filerMailingCountryCd,filerMailingPostalCode,filerMailingRegion,filerPrimaryUsaPhoneFlag,filerPrimaryPhoneNumber,filerPrimaryPhoneExt,filerHoldOfficeCd,filerHoldOfficeDistrict,filerHoldOfficePlace,filerHoldOfficeDescr,filerHoldOfficeCountyCd,filerHoldOfficeCountyDescr,filerFilerpersStatusCd,filerEffStartDt,filerEffStopDt,contestSeekOfficeCd,contestSeekOfficeDistrict,contestSeekOfficePlace,contestSeekOfficeDescr,contestSeekOfficeCountyCd,contestSeekOfficeCountyDescr,treasPersentTypeCd,treasNameOrganization,treasNameLast,treasNameSuffixCd,treasNameFirst,treasNamePrefixCd,treasNameShort,treasStreetAddr1,treasStreetAddr2,treasStreetCity,treasStreetStateCd,treasStreetCountyCd,treasStreetCountryCd,treasStreetPostalCode,treasStreetRegion,treasMailingAddr1,treasMailingAddr2,treasMailingCity,treasMailingStateCd,treasMailingCountyCd,treasMailingCountryCd,treasMailingPostalCode,treasMailingRegion,treasPrimaryUsaPhoneFlag,treasPrimaryPhoneNumber,treasPrimaryPhoneExt,treasAppointorNameLast,treasAppointorNameFirst,treasFilerpersStatusCd,treasEffStartDt,treasEffStopDt,assttreasPersentTypeCd,assttreasNameOrganization,assttreasNameLast,assttreasNameSuffixCd,assttreasNameFirst,assttreasNamePrefixCd,assttreasNameShort,assttreasStreetAddr1,assttreasStreetAddr2,assttreasStreetCity,assttreasStreetStateCd,assttreasStreetCountyCd,assttreasStreetCountryCd,assttreasStreetPostalCode,assttreasStreetRegion,assttreasPrimaryUsaPhoneFlag,assttreasPrimaryPhoneNumber,assttreasPrimaryPhoneExt,assttreasAppointorNameLast,assttreasAppointorNameFirst,chairPersentTypeCd,chairNameOrganization,chairNameLast,chairNameSuffixCd,chairNameFirst,chairNamePrefixCd,chairNameShort,chairStreetAddr1,chairStreetAddr2,chairStreetCity,chairStreetStateCd,chairStreetCountyCd,chairStreetCountryCd,chairStreetPostalCode,chairStreetRegion,chairMailingAddr1,chairMailingAddr2,chairMailingCity,chairMailingStateCd,chairMailingCountyCd,chairMailingCountryCd,chairMailingPostalCode,chairMailingRegion,chairPrimaryUsaPhoneFlag,chairPrimaryPhoneNumber,chairPrimaryPhoneExt"


def write(path: Path, header: str, rows: list[dict], encoding: str = "utf-8") -> None:
    """Write rows (dicts of header name to value, others blank) under header"""
    columns = header.split(",")
    unknown = {key for row in rows for key in row} - set(columns)
    if unknown:
        raise KeyError(f"{path.name}: not in header: {sorted(unknown)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding=encoding) as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([row.get(column, "") for column in columns])


def az() -> None:
    """Arizona Advanced Search downloads and Access database export"""
    directory = RAW_DIRECTORY / "az"
    committee = {
        "CommitteeID": "900001",
        "CommitteeName": "Friends of Testperson",
        "CommitteeGroupNumber": "0",
        "TransactionNameID": "0",
        "TransactionGroupNumber": "0",
        "FilerName": "Testperson, Jane",
    }
    income = [
        {
            **committee,
            "TransactionDate": "/Date(1709794800000)/",  # 2024-03-07 UTC
            "Amount": "150.0",
            "TransactionName": "Donor, Alex",
            "TransactionType": "Contribution from Individuals",
            "TransactionTypeDispositionID": "1",
            "TransactionTypeId": "2",
            "FirstName": "Alex",
            "LastName": "Donor",
            "City": "Phoenix",
            "State": "AZ",
            "ZipCode": "85001",
        },
        {
            **committee,
            "TransactionDate": "/Date(1709881200000)/",
            "Amount": "40.0",
            "TransactionName": "Donor, Blair",
            "TransactionType": "In-State Contributions $100 or Less",
            "TransactionTypeDispositionID": "1",
            "TransactionTypeId": "5",
            "FirstName": "Blair",
            "LastName": "Donor",
        },
        {
            **committee,
            "TransactionDate": "/Date(1709967600000)/",
            "Amount": "500.0",
            "TransactionName": "Example Industry PAC",
            "TransactionType": "Contributions from PACs",
            "TransactionTypeDispositionID": "1",
            "TransactionTypeId": "115",
            "LastName": "Example Industry PAC",
        },
    ]
    expenses = [
        {
            **committee,
            "TransactionDate": "/Date(1673247600000)/",  # 2023-01-09 UTC
            "Amount": "250.0",
            "TransactionName": "Example Print Shop",
            "TransactionType": "Operating Expense - Pay Cash/Check",
            "TransactionTypeDispositionID": "2",
            "TransactionTypeId": "31",
            "LastName": "Example Print Shop",
            "City": "Tucson",
            "State": "AZ",
        },
        {
            **committee,
            "TransactionDate": "/Date(1673334000000)/",
            "Amount": "1000.0",
            "TransactionName": "Committee to Elect Sam Sample",
            "TransactionType": "Contribute to a Candidate Committee",
            "TransactionTypeDispositionID": "2",
            "TransactionTypeId": "231",
            "LastName": "Committee to Elect Sam Sample",
        },
        {
            **committee,
            "TransactionDate": "/Date(1673420400000)/",
            "Amount": "12.5",
            "TransactionName": "Example Diner",
            "TransactionType": "Small Expenses (Paid Cash)",
            "TransactionTypeDispositionID": "2",
            "TransactionTypeId": "227",
            "LastName": "Example Diner",
        },
    ]
    # Refunds and loans, written as in the real files: a negative amount is
    # money moving against its disposition (income or expense)
    refunds_and_loans = [
        {
            **committee,
            "TransactionDate": "/Date(1710054000000)/",  # 2024-03-10 UTC
            "Amount": "-275.0",
            "TransactionName": "Donor, Casey",
            "TransactionType": "Refund Contribution from Individual",
            "TransactionTypeDispositionID": "1",
            "TransactionTypeId": "43",
            "FirstName": "Casey",
            "LastName": "Donor",
        },
        {
            **committee,
            "TransactionDate": "/Date(1710140400000)/",  # 2024-03-11 UTC
            "Amount": "-42.5",
            "TransactionName": "Example Sign Company",
            "TransactionType": "Rebate or Refund from Operating Expense",
            "TransactionTypeDispositionID": "2",
            "TransactionTypeId": "22",
            "LastName": "Example Sign Company",
        },
        {
            **committee,
            "TransactionDate": "/Date(1710226800000)/",  # 2024-03-12 UTC
            "Amount": "7300.0",
            "TransactionName": "Example Holdings LLC",
            "TransactionType": "Loan to a Corporation/LLC",
            "TransactionTypeDispositionID": "2",
            "TransactionTypeId": "250",
            "LastName": "Example Holdings LLC",
        },
    ]
    write(
        directory / "AdvancedSearch" / "BallotMeasures-1-1.csv",
        AZ_ADVANCED_SEARCH_TRANSACTIONS,
        refunds_and_loans,
    )
    write(
        directory / "AdvancedSearch" / "Income-1-1.csv",
        AZ_ADVANCED_SEARCH_TRANSACTIONS,
        income,
    )
    write(
        directory / "AdvancedSearch" / "Expenditures-1-1.csv",
        AZ_ADVANCED_SEARCH_TRANSACTIONS,
        expenses,
    )
    write(
        directory / "AdvancedSearch" / "Committee.csv",
        AZ_ADVANCED_SEARCH_COMMITTEES,
        [
            {
                "CommitteeName": "Friends of Testperson",
                "Status": "Active",
                "MasterCommitteeId": "900001",
                "CommitteeTypeName": "Candidate",
                "Candidate": "Testperson, Jane",
                "OfficeName": "Governor",
                "PartyName": "Democratic",
                "CommitteeID": "900001",
            },
            {
                "CommitteeName": "Example Industry PAC",
                "Status": "Active",
                "MasterCommitteeId": "900002",
                "CommitteeTypeName": "Political Action Committee",
                "CommitteeID": "900002",
            },
            {
                "CommitteeName": "Committee to Elect Sam Sample",
                "Status": "Terminated",
                "MasterCommitteeId": "900003",
                "CommitteeTypeName": "Candidate",
                "Candidate": "Sample, Sam",
                "PartyName": "Republican",
                "CommitteeID": "900003",
            },
        ],
    )

    access = {"IsForBenefit": "0", "Deleted": "0"}
    write(
        directory / "AccessDatabase" / "Transactions.csv",
        AZ_ACCESS_TRANSACTIONS,
        [
            {
                **access,
                "TransactionID": "1",
                "ModifiesTransactionID": "1",
                "TransactionTypeID": "2",  # Contribution from Individuals
                "CommitteeID": "900001",
                "TransactionDate": "9/10/2003 0:00:00",
                "Amount": "$50.00",
                "NameID": "800001",
            },
            {
                **access,
                "TransactionID": "2",
                "ModifiesTransactionID": "2",
                "TransactionTypeID": "31",  # Operating Expense - Pay Cash/Check
                "CommitteeID": "900001",
                "TransactionDate": "12/31/1999 0:00:00",
                "Amount": "($745.00)",
                "NameID": "800002",
                "Memo": "Yard signs",
            },
            {
                **access,
                "TransactionID": "3",
                "ModifiesTransactionID": "3",
                "TransactionTypeID": "75",  # Pay a Bill - Operating Expenses
                "CommitteeID": "900001",
                "TransactionDate": "12/2/1996 0:00:00",
                "Amount": "$421.37",
                "NameID": "800002",
            },
        ],
        encoding="latin1",
    )
    write(
        directory / "AccessDatabase" / "Committees.csv",
        AZ_ACCESS_COMMITTEES,
        [
            {
                "CommitteeID": str(900001 + i),
                "NameID": str(800101 + i),
                "PhysicalCity": "Phoenix",
                "PhysicalState": "AZ",
                "BenefitsBallotMeasure": "FALSE",
                "CandidateIsIncumbent": "FALSE",
            }
            for i in range(3)
        ],
        encoding="latin1",
    )
    write(
        directory / "AccessDatabase" / "Names.csv",
        AZ_ACCESS_NAMES,
        [
            {
                "NameID": "800001",
                "NameGroupID": "1",
                "EntityTypeID": "1",
                "LastName": "DONOR",
                "FirstName": "ALEX",
                "City": "Phoenix",
                "State": "AZ",
            },
            {
                "NameID": "800002",
                "NameGroupID": "2",
                "EntityTypeID": "1",
                "LastName": "EXAMPLE PRINT SHOP",
            },
            {
                "NameID": "800101",
                "NameGroupID": "3",
                "EntityTypeID": "1",
                "LastName": "FRIENDS OF TESTPERSON",
            },
        ],
        encoding="latin1",
    )


def mi() -> None:
    """Michigan legacy downloads: contributions, receipts and expenditures"""
    directory = RAW_DIRECTORY / "mi" / "LegacyDownloads" / "2022"
    industry_pac = {
        "doc_stmnt_year": "2022",
        "doc_type_desc": "ANNUAL CS",
        "com_legal_name": "EXAMPLE INDUSTRY POLITICAL ACTION COMMITTEE",
        "common_name": "EXAMPLE INDUSTRY PAC",
        "cfr_com_id": "9001",
        "com_type": "IND",  # independent committee, a PAC
    }
    candidate_committee = {
        "doc_stmnt_year": "2022",
        "doc_type_desc": "PRE-PRIMARY CS",
        "com_legal_name": "FRIENDS OF SAM SAMPLE",
        "common_name": "FRIENDS OF SAM SAMPLE",
        "cfr_com_id": "9002",
        "com_type": "CAN",
    }
    # only the contribution and receipt files name the committee's candidate
    candidate = {"can_first_name": "SAM", "can_last_name": "SAMPLE"}
    county_party = {
        "doc_stmnt_year": "2022",
        "doc_type_desc": "ANNUAL CS",
        "com_legal_name": "EXAMPLE COUNTY REPUBLICAN COMMITTEE",
        "common_name": "EXAMPLE COUNTY REPUBLICAN",
        "cfr_com_id": "9003",
        "com_type": "COU",
    }
    donor = {
        "contribtype": "DIRECT                        ",
        "f_name": "JANE                ",
        "l_name_or_org": "TESTPERSON                          ",
        "address": "1 EXAMPLE ST",
        "city": "LANSING             ",
        "state": "MI",
        "zip": "48901-0000",
    }
    write(
        directory / "2022_mi_cfr_contributions.csv",
        MI_CONTRIBUTIONS,
        [
            {
                **industry_pac,
                **donor,
                "doc_seq_no": "1001",
                "page_no": "0",
                "contribution_id": "1",
                "cont_detail_id": "0",
                "occupation": "TEACHER",
                "employer": "EXAMPLE SCHOOLS",
                "received_date": "3/14/2022",
                "amount": "50",
                "aggregate": "50",
            },
            {
                **candidate_committee,
                **candidate,
                **donor,
                "doc_seq_no": "1002",
                "page_no": "0",
                "contribution_id": "2",
                "cont_detail_id": "0",
                "received_date": "4/1/2022",
                "amount": "25",
                "aggregate": "75",
            },
            {
                **county_party,
                **donor,
                "doc_seq_no": "1003",
                "page_no": "0",
                "contribution_id": "3",
                "cont_detail_id": "0",
                "received_date": "5/2/2022",
                "amount": "1,000.00",
                "aggregate": "1000",
            },
        ],
        encoding="mac_roman",
    )
    write(
        directory / "2022_mi_cfr_receipts.csv",
        MI_RECEIPTS,
        [
            {
                **industry_pac,
                **donor,
                "doc_seq_no": "2001",
                "ik_code": "I",
                "page_no": "0",
                "receipt_id": str(i + 1),
                "detail_id": "0",
                "contribtype": "INKIND",
                "received_date": f"6/{i + 1}/2022",
                "amount": "20.4",
                "extra_desc": "COPIES",
                "receipttype": "GOODS DONATED OR LOANED",
            }
            for i in range(3)
        ],
        encoding="mac_roman",
    )
    direct = {"expenditure_type": "1B", "page_no": "0", "detail_id": "0"}
    write(
        directory / "2022_mi_cfr_expenditures.csv",
        MI_EXPENDITURES,
        [
            {
                # a PAC contributing to a candidate committee
                **industry_pac,
                **direct,
                "doc_seq_no": "3001",
                "expense_id": "1",
                "schedule_desc": "DIRECT",
                "exp_desc": "DIRECT CONTRIBUTIONS",
                "purpose": "CONTRIBUTION",
                "lname_or_org": "FRIENDS OF SAM SAMPLE               ",
                "address": "2 EXAMPLE AVE",
                "city": "DETROIT             ",
                "state": "MI",
                "zip": "48201-0000",
                "exp_date": "10/10/2022",
                "amount": "25000",
                "can_or_ballot": "SAM SAMPLE",
                "": "STATE REPRESENTATIVE 42",
            },
            {
                # a candidate committee paying a vendor
                **candidate_committee,
                **direct,
                "doc_seq_no": "3002",
                "expense_id": "2",
                "schedule_desc": "DIRECT",
                "exp_desc": "PRINT ADVERTISING",
                "purpose": "YARD SIGNS",
                "lname_or_org": "EXAMPLE PRINT SHOP                  ",
                "exp_date": "6/13/2022",
                "amount": "68.67",
            },
            {
                # a candidate committee paying a person
                **candidate_committee,
                **direct,
                "doc_seq_no": "3003",
                "expense_id": "3",
                "schedule_desc": "DIRECT",
                "purpose": "LOAN PAYMENT",
                "f_name": "ALEX                ",
                "lname_or_org": "SAMPLEPERSON                        ",
                "exp_date": "7/1/2022",
                "amount": "65",
            },
        ],
        encoding="mac_roman",
    )


def tx() -> None:
    """Texas Ethics Commission bulk download"""
    directory = RAW_DIRECTORY / "tx"
    pac = {
        "formTypeCd": "MPAC",
        "infoOnlyFlag": "N",
        "filerIdent": "00090001",
        "filerTypeCd": "MPAC",
        "filerName": "Example Industry PAC",
        "itemizeFlag": "Y",
        "travelFlag": "N",
    }
    write(
        directory / "contribs_01.csv",
        TX_CONTRIBUTIONS,
        [
            {
                **pac,
                "recordType": "RCPT",
                "schedFormTypeCd": "A1",
                "reportInfoIdent": str(500 + i),
                # the report arrives weeks after the contribution
                "receivedDt": f"2009020{i + 1}",
                "contributionInfoId": str(700 + i),
                "contributionDt": f"2008122{i}",
                "contributionAmount": "100.00",
                "contributorPersentTypeCd": "INDIVIDUAL",
                "contributorNameLast": f"Testperson{i}",
                "contributorNameFirst": "Jane",
                "contributorStreetCity": "Austin",
                "contributorStreetStateCd": "TX",
            }
            for i in range(3)
        ],
    )
    write(
        directory / "expend_01.csv",
        TX_EXPENDITURES,
        [
            {
                **pac,
                "recordType": "EXPN",
                "schedFormTypeCd": "F1",
                "reportInfoIdent": str(600 + i),
                "receivedDt": f"2002110{i + 1}",
                "expendInfoId": str(800 + i),
                "expendDt": f"2002092{i}",
                "expendAmount": "500.00",
                "expendDescr": "Political Expenditure",
                "expendCatCd": "UNKNOWN",
                "payeePersentTypeCd": "ENTITY",
                "payeeNameOrganization": f"Sample Campaign {i}",
                "payeeStreetCity": "Dallas",
                "payeeStreetStateCd": "TX",
            }
            for i in range(3)
        ],
    )
    write(
        directory / "filers.csv",
        TX_FILERS,
        [
            {
                "recordType": "FILER",
                "filerIdent": f"0009000{i + 1}",
                "filerTypeCd": "MPAC",
                "filerName": f"Example PAC {i}",
                "filerPersentTypeCd": "ENTITY",
                "filerNameOrganization": f"Example PAC {i}",
            }
            for i in range(3)
        ],
    )


if __name__ == "__main__":
    az()
    mi()
    tx()
