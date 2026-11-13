"""Functions for cleaning and standardizing names"""

import re

import nameparser
import pandas as pd

from utils.normalize import combine_rows_sharing_an_id

# Words that mark a name as an organization's. Each is matched as a whole
# word, ignoring case, so "inc" matches "Acme Inc" but not "Vincent", and
# "pac" matches "Friends of Bob PAC" but not "Spacek".
company_name_patterns = [
    r"inc",
    r"incorporated",
    r"llc",
    r"llp",
    r"ltd",
    r"corp",
    r"corporation",
    r"company",
    r"committee",
    r"party",
    r"caucus",
    r"foundation",
    r"union",
    r"associat\w*",
    r"pac",
    r"fund",
    r"friends of",
    r"citizens for",
]
_ORGANIZATION_NAME_REGEX = rf"\b(?:{'|'.join(company_name_patterns)})\b"


def _clean_name_components(
    name_components: dict[str, str | None],
) -> dict[str, str | None]:
    """Clean name components

    Args:
        name_components: A dictionary with keys: prefix, first_name, middle_name, last_name, suffix, nickname

    """
    # keep letters in any alphabet ("José", "Nuñez"), spaces, hyphens and
    # apostrophes; drop digits, underscores and other symbols
    for key, value in name_components.items():
        if value is not None:
            value = re.sub(r"[^\w\s'\u2019-]|[\d_]", "", value)
        name_components[key] = value or None
    return name_components


def divide_full_name_nameparser(full_name: str) -> dict[str, str | None]:
    """Divide a full name into component parts using nameparser

    Args:
        full_name: A full name of a person

    Returns:
        dict: A dictionary with keys: prefix, first_name, middle_name, last_name, suffix, nickname
    """
    if pd.isna(full_name):
        return {
            "name_prefix": None,
            "first_name": None,
            "middle_name": None,
            "last_name": None,
            "name_suffix": None,
            "name_preferred": None,
        }
    name = nameparser.HumanName(full_name)
    name_components = {
        "name_prefix": name.title,
        "first_name": name.first,
        "middle_name": name.middle,
        "last_name": name.last,
        "name_suffix": name.suffix,
        "name_preferred": name.nickname,
    }
    if name.first is None:
        name_components["first_name"] = name.title
    return _clean_name_components(name_components)


def clean_individuals_names(names: pd.DataFrame) -> pd.DataFrame:
    """Fill in missing name parts by parsing the full name

    Name parts the state supplied are never replaced:
    - if both first_name and last_name are missing, every missing part
      (first, middle, last, prefix, suffix, preferred) is filled from the
      parsed full_name
    - if only one of them is missing, only that one is filled, so a supplied
      first name like "Mary Ann" isn't split into first and middle names

    Args:
        names: A dataframe with columns:
            full_name, first_name, middle_name, last_name, name_prefix,
            name_suffix, name_preferred

    Returns:
        pd.DataFrame: names with missing parts filled in.
    """
    names = names.copy()
    if names.empty:
        return names
    # handle misplaced last names
    likely_full_names = (
        names["full_name"].isna()
        & names["first_name"].isna()
        & names["last_name"].notna()
        & names["last_name"].str.contains(",", na=False)
    )
    names.loc[likely_full_names, "full_name"] = names.loc[
        likely_full_names, "last_name"
    ]
    first_missing = names["first_name"].isna().to_numpy()
    last_missing = names["last_name"].isna().to_numpy()
    to_parse = names["full_name"].notna().to_numpy() & (first_missing | last_missing)
    if not to_parse.any():
        return names

    parsed = pd.DataFrame(
        [divide_full_name_nameparser(name) for name in names["full_name"][to_parse]]
    )
    # positions within the parsed rows; matched by position because the
    # table's index can repeat labels
    both_missing = (first_missing & last_missing)[to_parse]
    only_first_missing = (first_missing & ~last_missing)[to_parse]
    only_last_missing = (last_missing & ~first_missing)[to_parse]
    name_columns = [
        "first_name",
        "middle_name",
        "last_name",
        "name_prefix",
        "name_suffix",
        "name_preferred",
    ]
    for column in name_columns:
        current = names[column].to_numpy(dtype=object)[to_parse]
        fill = pd.isna(current) & both_missing
        if column == "first_name":
            fill |= only_first_missing
        elif column == "last_name":
            fill |= only_last_missing
        current[fill] = parsed[column].to_numpy(dtype=object)[fill]
        values = names[column].to_numpy(dtype=object).copy()
        values[to_parse] = current
        names[column] = values
    return names


def fill_in_transactor_types(names: pd.DataFrame) -> pd.DataFrame:
    """Fill in missing transactor types from the name

    A type the state supplied is never changed. A missing type becomes
    "Organization" when full_name or last_name contains a word from
    company_name_patterns, and "Unknown" otherwise.

    Args:
        names: A dataframe with columns full_name, last_name and
            transactor_type.

    Returns:
        names with transactor_type filled in, and transactor_type_inferred
        True where the type came from the name rather than the state.
    """
    missing_type = names["transactor_type"].isna()
    looks_like_organization = (
        names[["full_name", "last_name"]]
        .apply(
            lambda col: col.astype("string").str.contains(
                _ORGANIZATION_NAME_REGEX, case=False, na=False, regex=True
            )
        )
        .any(axis=1)
    )
    inferred = missing_type & looks_like_organization
    names.loc[inferred, "transactor_type"] = "Organization"
    names["transactor_type"] = names["transactor_type"].fillna("Unknown")
    names["transactor_type_inferred"] = inferred.to_numpy()
    return names


def clean_phone_number(val: object) -> str | None:
    """Convert a phone number to a string of only its digits

    A phone number read as a number (3125551212.0) or written from one
    ("3125551212.0") loses the ".0" first, so it doesn't gain a digit.

    Args:
        val: A phone number as text or a number.

    Returns:
        The digits, or None if there are none.
    """
    if pd.isna(val):
        return None
    if isinstance(val, float) and val.is_integer():
        val = int(val)
    text = str(val).strip()
    if re.fullmatch(r"\d+\.0+", text):
        text = text.split(".")[0]
    return re.sub(r"\D", "", text) or None


def clean_names(transactors: pd.DataFrame) -> pd.DataFrame:
    """Clean names in a dataframe

    Args:
        transactors: A dataframe with columns:
            full_name, first_name, middle_name, last_name, name_prefix,
            name_suffix, name_preferred
    """
    # clean individuals names
    individuals_names_mask = transactors["transactor_type"] == "Individual"
    individuals_names = clean_individuals_names(
        transactors.loc[individuals_names_mask, :]
    )
    # concatenate cleaned individuals names with original dataframe
    cleaned_names = pd.concat(
        [transactors.loc[~individuals_names_mask, :], individuals_names]
    )
    return cleaned_names


def clean_transactors(transactors: pd.DataFrame) -> pd.DataFrame:
    """Clean transactors in a dataframe

    Args:
        transactors: A dataframe with columns:
            full_name, first_name, middle_name, last_name, name_prefix,
            name_suffix, name_preferred, phone_number
    """
    # combine rows sharing an id, keeping each column's first non-null value
    transactors = combine_rows_sharing_an_id(transactors, "Transactor")
    # fill in transactor types
    transactors = fill_in_transactor_types(transactors)
    transactors = clean_names(transactors)
    transactors["phone_number"] = transactors["phone_number"].apply(clean_phone_number)
    return transactors
