"""Functions for cleaning and standardizing names"""

import re

import nameparser
import pandas as pd

company_name_patterns = [
    r"inc",
    r"llc",
    r"ltd",
    r"corp",
    r"committee",
    r"party",
    r"foundation",
    r"union",
    r"associat",
    r"[0-9]",
    r"\wPAC\w",
]


def _clean_name_components(
    name_components: dict[str, str | None],
) -> dict[str, str | None]:
    """Clean name components

    Args:
        name_components: A dictionary with keys: prefix, first_name, middle_name, last_name, suffix, nickname

    """
    # remove non standard name characters
    # (only letters, spaces, hyphens, and apostrophes)
    for key, value in name_components.items():
        if value is not None:
            name_components[key] = re.sub(r"[^a-zA-Z\s'-]", "", value)
        if value == "":
            name_components[key] = None
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
    """Attempt to properly parse names into components

    Args:
        names: A dataframe with columns:
            full_name, first_name, middle_name, last_name, name_prefix,
            name_suffix, name_preferred

    Returns:
        pd.DataFrame: A dataframe with columns:
            full_name, first_name, middle_name, last_name, name_prefix,
            name_suffix, name_preferred
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
    # use a name parser to get components of full names where they are
    # not already provided
    missing_name_parts = names["full_name"].notna() & (
        names["first_name"].isna() | names["last_name"].isna()
    )

    # divide full names into components
    name_components = names[missing_name_parts]["full_name"].apply(
        divide_full_name_nameparser,  # result_type="expand"
    )

    name_components = pd.DataFrame(name_components.tolist())
    name_columns = [
        "first_name",
        "middle_name",
        "last_name",
        "name_prefix",
        "name_suffix",
        "name_preferred",
    ]
    for col in name_columns:
        if col in name_components.columns:
            names.loc[missing_name_parts, col] = name_components[col].to_numpy()

    return names


def fill_in_transactor_types(names: pd.DataFrame) -> pd.DataFrame:
    """Fill in transactor types based on name components

    Args:
        names: A dataframe with columns:
            full_name, first_name, middle_name, last_name, name_prefix,
            name_suffix, name_preferred
    """
    possible_org_names = names[["full_name", "last_name"]]
    pattern = "|".join(company_name_patterns)
    contains_any = possible_org_names.apply(
        lambda col: col.astype("string").str.contains(pattern, case=False, na=False)
    )
    probable_organization_mask = contains_any.any(axis=1)
    names.loc[probable_organization_mask, "transactor_type"] = "Organization"
    names["transactor_type"] = names["transactor_type"].fillna("Unknown")
    # TODO: improve transactor_type prediction
    return names


def clean_phone_number(val: object) -> str | None:
    """Convert phone numbers to strings of only digits or None"""
    if pd.isna(val):
        return None
    string_phone_number = str(val)
    # strip non-numeric characters
    string_phone_number = re.sub(r"\D", "", string_phone_number)
    return string_phone_number


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
    transactors = transactors.drop_duplicates(subset=["id"], keep="first")
    # fill in transactor types
    transactors = fill_in_transactor_types(transactors)
    transactors = clean_names(transactors)
    transactors["phone_number"] = transactors["phone_number"].apply(clean_phone_number)
    return transactors
