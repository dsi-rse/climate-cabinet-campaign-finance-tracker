"""Functions for cleaning and standardizing names"""

import re

import nameparser
import numpy as np
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

# Titles and suffixes a state writes after a surname ("KOSKE MR.", "WRIGHT JR")
_TITLE_WORDS = {"mr", "mrs", "ms", "miss", "dr", "rev", "hon"}
_SUFFIX_WORDS = {"jr", "sr", "ii", "iii", "iv", "esq"}


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


def _peel_title_and_suffix(last_name: str) -> dict[str, str | None]:
    """Take a trailing title, suffix or stray marker off a last name

    States write them after the surname: "KOSKE MR.", "WRIGHT JR", and
    Michigan's "OLDHAM ^". A one-word value is left alone, so a surname that
    is itself a title word stays.

    Args:
        last_name: The last name as the state wrote it.

    Returns:
        last_name, name_prefix and name_suffix, cleaned; missing ones None.
    """
    words = re.sub(r"\s*\^\s*$", "", last_name).split()
    prefix = suffix = None
    while len(words) > 1:
        word = words[-1].rstrip(".,").lower()
        if word in _TITLE_WORDS and prefix is None:
            prefix = words.pop()
        elif word in _SUFFIX_WORDS and suffix is None:
            suffix = words.pop()
        else:
            break
    return _clean_name_components(
        {"last_name": " ".join(words), "name_prefix": prefix, "name_suffix": suffix}
    )


def clean_individuals_names(names: pd.DataFrame) -> pd.DataFrame:
    """Fill in missing name parts by parsing the full name

    Name parts the state supplied are never replaced:
    - a trailing title, suffix or " ^" marker is taken off a supplied
      last_name ("KOSKE MR.^", "WRIGHT JR") and fills name_prefix or
      name_suffix if those are missing (see _peel_title_and_suffix); a
      last_name with no letters left becomes null
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
    # that last name was the whole name, so it is missing too and gets filled
    names.loc[likely_full_names, "last_name"] = None
    # a title or suffix written after the last name is not part of it
    has_last_name = names["last_name"].notna().to_numpy()
    if has_last_name.any():
        peeled = [
            _peel_title_and_suffix(str(last_name))
            for last_name in names["last_name"].to_numpy()[has_last_name]
        ]
        for column in ("last_name", "name_prefix", "name_suffix"):
            values = names[column].to_numpy(dtype=object).copy()
            current = values[has_last_name]
            parsed = np.array([parts[column] for parts in peeled], dtype=object)
            if column == "last_name":
                # the peeled surname replaces the field it came from; a field
                # with nothing else in it (" ", "^", digits) becomes null
                # rather than an empty string that would match other empties
                current[:] = parsed
            else:
                missing = pd.isna(current)
                current[missing] = parsed[missing]
            values[has_last_name] = current
            names[column] = values
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
    company_name_patterns, "Individual" when the state gave a first name
    and the name has no such word (a state that gives donors no type still
    gives people a first name), and "Unknown" otherwise.

    Args:
        names: A dataframe with columns full_name, last_name and
            transactor_type, and first_name if the state supplies one.

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
    has_first_name = (
        names["first_name"].notna()
        if "first_name" in names.columns
        else pd.Series(False, index=names.index)
    )
    organization = missing_type & looks_like_organization
    individual = missing_type & ~looks_like_organization & has_first_name
    names.loc[organization, "transactor_type"] = "Organization"
    names.loc[individual, "transactor_type"] = "Individual"
    names["transactor_type"] = names["transactor_type"].fillna("Unknown")
    names["transactor_type_inferred"] = (organization | individual).to_numpy()
    return names


def use_lone_last_name_as_full_name(transactors: pd.DataFrame) -> pd.DataFrame:
    """Treat a last name with no first name as the whole name, except for a person

    Some states put a person's last name and an organization's name in the
    same column (Michigan's l_name_or_org, Arizona's LastName), which the
    config maps to last_name. For a record not typed Individual, a last_name
    with no first_name and no full_name is the whole name and moves to
    full_name, where organization names are compared in linkage. An
    organization whose name was split across the two columns ("MIDLAND
    COUNTY" / "DEMOCRATIC PARTY") gets both, joined, as its full_name. A
    person the state recorded by last name only keeps it.

    Args:
        transactors: A table with transactor_type, first_name, last_name and
            full_name (first_name may be absent).

    Returns:
        transactors with those names moved to full_name.
    """
    first_name = (
        transactors["first_name"]
        if "first_name" in transactors.columns
        else pd.Series(None, index=transactors.index, dtype=object)
    )
    unnamed = transactors["last_name"].notna() & transactors["full_name"].isna()
    lone_last_name = (
        unnamed & (transactors["transactor_type"] != "Individual") & first_name.isna()
    )
    split_organization = (
        unnamed
        & (transactors["transactor_type"] == "Organization")
        & first_name.notna()
    )
    transactors.loc[lone_last_name, "full_name"] = transactors.loc[
        lone_last_name, "last_name"
    ]
    transactors.loc[split_organization, "full_name"] = (
        first_name[split_organization].astype(str).str.strip()
        + " "
        + transactors.loc[split_organization, "last_name"].astype(str).str.strip()
    )
    moved = lone_last_name | split_organization
    transactors.loc[moved, "last_name"] = None
    if "first_name" in transactors.columns:
        transactors.loc[split_organization, "first_name"] = None
    return transactors


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
    transactors = use_lone_last_name_as_full_name(transactors)
    transactors = clean_names(transactors)
    transactors["phone_number"] = transactors["phone_number"].apply(clean_phone_number)
    return transactors
