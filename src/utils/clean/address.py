"""Functions for cleaning and standardizing addresses"""

import re

import pandas as pd
import usaddress
from usaddress import RepeatedLabelError

from utils.clean.mappings import directionals, occupancy_types, street_types


def clean_zipcode(val: object) -> str | None:
    """Convert zipcodes to strings of length 5"""
    standard_zipcode_length = 5
    if pd.isna(val):
        return None
    # make zipcode a string and if it is zip+4, take just zip.
    # if it was stored as a float, take the integer part.
    string_zipcode = str(val).split("-")[0].split(".")[0]
    if len(string_zipcode) == standard_zipcode_length:
        return string_zipcode
    # zipcode+4 without a dash
    elif len(string_zipcode) == standard_zipcode_length + 4:
        return string_zipcode[:standard_zipcode_length]
    # zipcode with leading 0 stripped when stored as number
    elif len(string_zipcode) == standard_zipcode_length - 1:
        return "0" + string_zipcode
    # unknown zipcode format
    else:
        return None


def convert_nulls(val: object | None) -> str:
    """Convert None or NaN to empty string"""
    if isinstance(val, (pd.Series | pd.DataFrame)):
        raise ValueError("Cannot convert Series or DataFrame to string")

    if pd.isna(val):
        return ""
    return str(val).strip()


def clean_address_string(address_string: str) -> str:
    """Remove leading/trailing whitespace, cut html tags

    Args:
        address_string (str): An address string.
    """
    if pd.isna(address_string):
        return ""
    address_string = address_string.strip()
    address_string = re.sub(r"<br\s*/?>", "\n", address_string)
    address_string = re.sub(r"<[^>]*>", "", address_string)
    return address_string


def get_address_string(address_row: pd.Series) -> str:
    """Get a string representation of an address row.

    Args:
        address_row (pd.Series): A row from a DataFrame containing address columns.
            Columns should include: line_1, line_2, city, state, zipcode, full_address

    Returns:
        str: A string representation of the address.
    """
    address_row = address_row.fillna("").astype(str)
    if "full_address" in address_row and address_row["full_address"] != "":
        return address_row["full_address"]

    address_row = address_row.fillna("").astype(str)

    line_1 = address_row["line_1"]
    line_2 = address_row["line_2"]
    city = address_row["city"]
    state = address_row["state"]
    zipcode = address_row["zipcode"]

    if line_2:
        return f"{line_1}\n{line_2}\n{city}, {state} {zipcode}".strip()
    if line_1 and city and state and zipcode:
        return f"{line_1}\n{city}, {state} {zipcode}".strip()

    # fallback: join whatever is non-empty
    raw_fields = [line_1, line_2, city, state, zipcode]
    return " ".join(f for f in raw_fields if f)


def expand_usaddress_simple(parsed_address: dict) -> dict:
    """Expand a parsed address with simple mappings.

    Args:
        parsed_address (dict): A dictionary of parsed address components.
    """
    for directional_type in [
        "StreetPreDirectional",
        "StreetNamePreDirectional",
        "StreetNamePostDirectional",
    ]:
        if directional_type in parsed_address:
            parsed_address[directional_type] = directionals.get(
                parsed_address[directional_type], parsed_address[directional_type]
            )
    for street_type in ["StreetNamePostType", "StreetNamePreType"]:
        # replace current street type with standardized version if mapping exists
        if (
            street_type in parsed_address
            and parsed_address[street_type] in street_types
        ):
            parsed_address[street_type] = street_types[parsed_address[street_type]]
    for occupancy_type in ["OccupancyType", "OccupancyIdentifier"]:
        # replace current occupancy type with standardized version if mapping exists
        if (
            occupancy_type in parsed_address
            and parsed_address[occupancy_type] in occupancy_types
        ):
            parsed_address[occupancy_type] = occupancy_types[
                parsed_address[occupancy_type]
            ]
    return parsed_address


def clean_usaddress(parsed_address: dict) -> dict:
    """Clean a parsed address.

    Args:
        parsed_address (dict): A dictionary of parsed address components.
    """
    for key, value in parsed_address.items():
        parsed_address[key] = value.lower().strip()
        parsed_address[key] = parsed_address[key].replace(".", "")
    return parsed_address


def parse_usaddress_simple_expand(address_row: pd.Series) -> dict:
    """Parse address with usaddress and expand it with simple mappings.

    Args:
        address_row (pd.Series): A row from a DataFrame containing address columns.
            Columns should include: line_1, line_2, city, state, zipcode, full_address

    Returns:
        dict: A dictionary of parsed address components.
    """
    address_str = get_address_string(address_row)
    try:
        parsed, address_type = usaddress.tag(address_str)
    except RepeatedLabelError:
        return {}
    parsed = clean_usaddress(parsed)
    if address_type == "Street Address":
        parsed = expand_usaddress_simple(parsed)
    return parsed


def clean_address(address_df: pd.DataFrame) -> pd.DataFrame:
    """Clean an address dataframe.

    Args:
        address_df: A DataFrame containing address columns.
            Columns should include: line_1, line_2, city, state, zipcode, full_address

    Returns:
        pd.DataFrame: A DataFrame with cleaned address components.
    """
    address_df["full_address"] = address_df["full_address"].apply(clean_address_string)
    if "zipcode" in address_df.columns:
        address_df["zipcode"] = address_df["zipcode"].apply(clean_zipcode)

    expanded_addresses_df = address_df.apply(
        parse_usaddress_simple_expand, axis=1, result_type="expand"
    )
    # column mapping
    address_part_name_mapping = {
        "Recipient": "recipient",
        "BuildingName": "building_name",
        "USPSBoxType": "usps_box_type",
        "USPSBoxID": "usps_box_id",
        # Don't use: "USPSBoxGroupType",  "USPSBoxGroupID"
        # Line 1
        "AddressNumber": "building_number",
        "StreetName": "street_name",
        "StreetNamePreDirectional": "street_predirectional",
        "StreetNamePostDirectional": "street_postdirectional",
        "StreetNamePreType": "street_pre_type",
        "StreetNamePostType": "street_post_type",
        # Line 2
        "OccupancyType": "occupancy_type",
        "OccupancyIdentifier": "occupancy_identifier",
        # Line 3
        "PlaceName": "city",
        "StateName": "state",
        "ZipCode": "zipcode",
    }

    expanded_addresses_df = expanded_addresses_df.drop(
        columns=[
            col
            for col in expanded_addresses_df.columns
            if col not in address_part_name_mapping
        ]
    )
    expanded_addresses_df = expanded_addresses_df.rename(
        columns=address_part_name_mapping
    )

    # For columns in both, use parsed_address_columns value if notna, else address_df
    for col in expanded_addresses_df.columns:
        if col in address_df.columns:
            address_df[col] = expanded_addresses_df[col].combine_first(address_df[col])
        else:
            address_df[col] = expanded_addresses_df[col]
    return address_df
