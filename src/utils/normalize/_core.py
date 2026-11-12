"""Abstract base class representing database tables

For each table, the maximal set of allowable column names in unnormalized form
includes:
  - each attribute possessed by the entity type the table represents,
  - each attribute possessed by any foreign key relations of the table,
    prefixed by the name of the foreign key relation (i.e transaction ->
    transactor may be named either donor or recipient), separated by a '-'.
  - each attribute that is a multivalued fact about the entity. Since data
    is mostly retrieved from state agency documents for transactions, info
    about other entity types is often from a particular instance in time.
    Therefore, facts about an individual that may change like address or
    employer can be multivalued but are intially presented with the individual.


#   - each attribute possessed by a limited set of reverse foreign key relations
#     of the table, prefixed by the name of the reverse foreign key relation.
#     'Limited set' refers to all of those reverse foriegn key relations that
#     are effectively 'few-to-one' and unlikely to change often enough to
#     warrant normalizind after the initial processing steps. For example,
#     transaction -> transactor is many to one and we create separate transaction
#     and transactor tables immediately. address -> transactor changes
#     infrequently, requires more sophisticated record linkage and is not worth
#     immediately normalizing.
"""

import re
import warnings
from pathlib import Path

import pandas as pd

from utils.constants import ID_SUFFIX, SPLIT
from utils.ids import (
    get_raw_ids_mask,
    handle_existing_ids,
    handle_id_column,
    replace_null_ids_with_uuids,
)
from utils.io import FileFormat, load_table, save_database, table_column
from utils.schema import DataSchema

REPEATING_COLUMN_REGEX = r"^[A-Za-z_]+-\d+$"


class Normalizer:
    """Class for normalizing a database according to a provided schema"""

    @property
    def id_mapping(self) -> dict[tuple, str]:
        """Dictionary mapping original ids to uuids

        Original ids are stored as tuples of the form (raw id, table_name, year, state)
        """
        return self._id_mapping

    def __init__(
        self,
        database: dict[str, pd.DataFrame],
        schema: DataSchema | Path | str,
        existing_id_mapping: dict[tuple, str] | None = None,
    ) -> None:
        """Create new normalizer

        Args:
            database: Dictionary of table names to DataFrames
            schema: DataSchema object or path to yaml file containing one
            existing_id_mapping: Pre-existing ID mappings to maintain consistency across chunks
        """
        self.database = database
        if isinstance(schema, str | Path):
            schema = DataSchema(schema)
        elif not isinstance(schema, DataSchema):
            raise RuntimeError("schema must be path or DataSchema object")
        self.schema = schema
        self._id_mapping = existing_id_mapping.copy() if existing_id_mapping else {}

    def get_foreign_table_name(self, base_type: str, column_name: str) -> str:
        """Retrieve the type of a multivalued/foreign table"""
        column_tokens = column_name.split(SPLIT)
        current_table_name = base_type
        for column_token in column_tokens:
            current_table_schema = self.schema.schema[current_table_name]
            if column_token in current_table_schema.relations:
                current_table_name = current_table_schema.relations[column_token]
                continue
            elif current_table_schema.attributes_regex.match(column_token):
                return current_table_name
        return current_table_name

    def _get_repeated_columns(self, table_columns: str) -> list[str]:
        r"""Given a list of columns and potentially repeating columns

        Example: 'amount' could be considered a repeating column if 'amount-\d' were
        in the table as well, but otherwise is not.
        """
        repeating_column_base_names = [
            col.split("-")[0]
            for col in table_columns
            if re.match(REPEATING_COLUMN_REGEX, col)
        ]
        return [
            col
            for col in table_columns
            if col.split("-")[0] in repeating_column_base_names
        ]

    def convert_to_1NF_from_unnormalized(self, table_name: str) -> None:
        """Converts an unnormalized table into first normal form (1NF)

        Removes anticipated repeating groups

        Args:
            table_name: name of table name to normalize
        Modifies:
            database[table_name] transformed to 1NF
        """
        unnormalized_table = self.database[table_name]
        repeated_columns = self._get_repeated_columns(unnormalized_table.columns)
        repeat_col_table = unnormalized_table[repeated_columns]
        if repeat_col_table.empty:
            return None
        # Add "nosuffix" as the suffix for repeating columns without suffixes
        repeat_col_table = repeat_col_table.rename(
            columns={
                col: f"{col}-nosuffix" for col in repeated_columns if "-" not in col
            }
        )
        # Split the suffixes into a second index level, and unstack
        repeat_col_table.columns = pd.MultiIndex.from_tuples(
            repeat_col_table.columns.to_series().str.split("-")
        )
        # pandas>=3 stack keeps all-NaN rows (from absent repeats), so drop them
        unstacked_repeat_col_table = (
            repeat_col_table.stack(1)  # noqa: PD013
            .dropna(how="all")
            .droplevel(1)
        )
        # Join with unrepeated columns
        first_normal_form_table = unnormalized_table.drop(
            columns=repeated_columns
        ).join(unstacked_repeat_col_table)
        self.database[table_name] = first_normal_form_table

    def _drop_verifiably_incomplete_rows(
        self,
        table_name: str,
        table: pd.DataFrame,
        relation_prefix: str,
    ) -> pd.DataFrame:
        """Drop all rows from table that cannot become complete after normalization

        Example: when normalizing, we may come accross a missing required foreign key
        column because the object the foreign key refers to is still stored in the
        current table (so prop--x exists, but prop_id does not yet exist, but will
        once normalization is completed)

        Args:
            table_name: name of table to drop verifiably incomplete rows from
            table: table to drop verifiably incomplete rows from
            relation_prefix: prefix of columns to be split from table. prefix should be in
                one of self.forward_relations or self.reverse_relations

        Returns:
            table with verifiably incomplete rows dropped
        """
        table_schema = self.schema.schema[table_name]
        required_attributes = set(table_schema.required_attributes)
        if "id" in required_attributes:
            required_attributes.remove("id")  # id has not been generated yet
        # check if any required attributes are forward relations (and therefore
        # may not exist in a valid table that is not yet normalized)
        required_forward_relations = [
            attribute[: -len(ID_SUFFIX)]
            for attribute in required_attributes
            if attribute[: -len(ID_SUFFIX)] in table_schema.forward_relations
        ]
        for forward_relation in required_forward_relations:
            # We will handle forward relations from the table separately
            # since the table may not be normalized yet. If a forward relation
            # is a required attribute, it is possible that the normalized column
            # for it has not been created yet.
            required_attributes.remove(f"{forward_relation}{ID_SUFFIX}")

            # We can still verify that the row in the base table is incomplete
            # if all columns that are properties of a given required forward relation
            # are null.
            forward_relation_columns = [
                col
                for col in table.columns
                if col.startswith(f"{relation_prefix}{SPLIT}{forward_relation}")
            ]
            if forward_relation_columns == []:
                # table has no columns for required forward relations, it is
                # verifiably incomplete
                return pd.DataFrame(columns=table.columns)
            # drop all rows that are all na for required forward relation
            table = table.dropna(subset=forward_relation_columns, how="all")
        # now drop all rows that are na for any non forward relation columns
        required_attribute_column_names = [
            f"{relation_prefix}{SPLIT}{attribute}" for attribute in required_attributes
        ]
        if not set(required_attribute_column_names).issubset(table.columns):
            return pd.DataFrame(columns=table.columns)
        table = table.dropna(subset=required_attribute_column_names, how="any")

        # drop all rows that are na for all columns that start with relation_prefix
        extracted_table_columns = [
            column
            for column in table.columns
            if column.startswith(f"{relation_prefix}{SPLIT}")
        ]
        table = table.dropna(how="all", subset=extracted_table_columns)
        # now drop all rows that na for all columns except a single required forward
        # relation and metadata columns. These are verifiably incomplete because if all
        # else is null, the forward relation is linking to nothing.
        if (
            len(table_schema.required_attributes) == 1
            and len(required_forward_relations) == 1
        ):
            if (
                f"{relation_prefix}{SPLIT}{required_forward_relations[0]}{ID_SUFFIX}"
                in extracted_table_columns
            ):
                extracted_table_columns.remove(
                    f"{relation_prefix}{SPLIT}{required_forward_relations[0]}{ID_SUFFIX}"
                )
            table = table.dropna(how="all", subset=extracted_table_columns)

        return table

    def _add_relation_to_extracted_table(
        self,
        table: pd.DataFrame,
        table_name: str,
        schema: DataSchema,
        foreign_key_prefix: str,
    ) -> pd.DataFrame:
        """Ensures a column linking back to the base table is required in a extracted table

        Args:
            table: DataFrame containing the foreign key prefix
            table_name: name of table containing the foreign key prefix
            schema: DataSchema object
            foreign_key_prefix: prefix of the foreign key column
        """
        # backlink column is the column in the extracted table that links back to the
        # base table
        backlink_column = schema.schema[table_name].reverse_relation_names[
            foreign_key_prefix
        ]
        if f"{foreign_key_prefix}{SPLIT}{backlink_column}" not in table.columns:
            # this means the required column doesn't exist so we should
            # create it. Since ids are handled upon table creation, the table
            # must have a valid "id" column
            backlink_column = f"{foreign_key_prefix}{SPLIT}{backlink_column}"
            table.loc[:, backlink_column] = table["id"]

        return table

    def _add_relationship_metadata_to_extracted_table(
        self,
        table: pd.DataFrame,
        table_name: str,
        relation_prefix: str,
    ) -> pd.DataFrame:
        """Adds relationship metadata to an extracted table"""
        relationship_metadata = self.schema.schema[table_name].relations_data[
            relation_prefix
        ]
        if "relationship_column_values" in relationship_metadata:
            for column, value in relationship_metadata[
                "relationship_column_values"
            ].items():
                table[f"{relation_prefix}{SPLIT}{column}"] = value
        return table

    @staticmethod
    def _row_keys(table: pd.DataFrame, columns: list[str]) -> list[tuple]:
        """Hashable key for each row of table, in order, with every null as None"""
        values = table[columns].astype(object)
        values = values.where(values.notna(), None)
        return list(zip(*(values[column] for column in columns)))

    def _assign_forward_relation_ids(
        self,
        table: pd.DataFrame,
        extracted_table: pd.DataFrame,
        extracted_table_name: str,
        relation_prefix: str,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Give each extracted entity an id and point the base table's foreign key at it

        Source ids decide identity. Rows with different {relation_prefix}_id values
        are different entities even when their attributes match, and a
        {relation_prefix}_id already present in the base table is never replaced.
        A row with no {relation_prefix}_id is matched by its attributes to the one
        entity with the same attributes. If no entity, or more than one, has those
        attributes, the row becomes a new entity rather than a guess.

        Rows are matched by position because the 1NF step can leave duplicate index
        labels.

        Args:
            table: base table containing the {relation_prefix}--* columns
            extracted_table: rows of table that describe an entity, after
                verifiably incomplete rows have been dropped
            extracted_table_name: name of the table the relation points to
            relation_prefix: prefix of the forward relation

        Returns:
            table with {relation_prefix}_id filled in where it was missing, and
            extracted_table with one row per distinct (id, attributes) and a
            {relation_prefix}_id for every row
        """
        id_column = f"{relation_prefix}{ID_SUFFIX}"
        foreign_columns = self._get_foreign_columns(table, relation_prefix)
        for frame in (table, extracted_table):
            if id_column not in frame.columns:
                frame[id_column] = None
            # map any raw ids that were not mapped before splitting. In the
            # pipeline normalize_database has already mapped them, so skip the
            # row-wise mapping when every id is already a uuid.
            if get_raw_ids_mask(frame, id_column).any():
                handle_existing_ids(
                    frame, extracted_table_name, self.id_mapping, id_column
                )

        has_source_id = extracted_table[id_column].notna()
        sourced = extracted_table[has_source_id].drop_duplicates(
            subset=[id_column, *foreign_columns]
        )
        unsourced = extracted_table[~has_source_id].drop_duplicates(
            subset=foreign_columns
        )

        source_ids_by_key: dict[tuple, set[str]] = {}
        for key, source_id in zip(
            self._row_keys(sourced, foreign_columns), sourced[id_column]
        ):
            source_ids_by_key.setdefault(key, set()).add(source_id)

        id_by_key = {}
        new_entity_positions = []
        for position, key in enumerate(self._row_keys(unsourced, foreign_columns)):
            matching_ids = source_ids_by_key.get(key, set())
            if len(matching_ids) == 1:
                id_by_key[key] = next(iter(matching_ids))
            else:
                new_entity_positions.append(position)
        new_entities = unsourced.iloc[new_entity_positions].reset_index(drop=True)
        # the id is made from the entity's attributes and state, so the same
        # entity gets the same id in every run and in each chunk it is new in.
        # An entity matched to a sourced one above takes that id instead, but
        # only when the sourced entity is in this chunk, so ids can change
        # with the chunk size.
        content_columns = foreign_columns + [
            column for column in ("reported_state",) if column in new_entities
        ]
        replace_null_ids_with_uuids(
            new_entities,
            id_column,
            table_name=extracted_table_name,
            content_columns=content_columns,
        )
        for key, new_id in zip(
            self._row_keys(new_entities, foreign_columns), new_entities[id_column]
        ):
            id_by_key[key] = new_id

        table[id_column] = [
            existing_id if pd.notna(existing_id) else id_by_key.get(key)
            for existing_id, key in zip(
                table[id_column], self._row_keys(table, foreign_columns)
            )
        ]
        entity_frames = [frame for frame in (sourced, new_entities) if not frame.empty]
        if entity_frames:
            extracted_table = pd.concat(entity_frames, ignore_index=True)
        else:
            extracted_table = extracted_table.iloc[0:0]
        return table, extracted_table

    def _combine_rows_sharing_an_id(
        self, table: pd.DataFrame, table_name: str
    ) -> pd.DataFrame:
        """Combine rows that have the same id into one row

        See combine_rows_sharing_an_id.

        Args:
            table: normalized table
            table_name: name of table, used in the warning

        Returns:
            table with at most one row per non-null id
        """
        return combine_rows_sharing_an_id(table, table_name)

    def _get_foreign_columns(
        self, table: pd.DataFrame, relation_prefix: str
    ) -> list[str]:
        """Get all columns in table that start with relation_prefix"""
        return [
            column
            for column in table.columns
            if column.startswith(f"{relation_prefix}{SPLIT}")
        ]

    def _split_prefixed_columns(
        self,
        table: pd.DataFrame,
        table_name: str,
        relation_prefix: str,
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """Remove columns starting with prefix from table, optionally keeping foreign key

        Args:
            table: base table to split relation from. If the split relation is a reverse
                relation, the table should have an 'id' column with valid uuids. Any
                existing {relation_prefix}_id column should have all NaNs or valid uuids.
            table_name: table name present in schema. There should be no column matching
                {relation_prefix}{SPLIT}id in the table.
            relation_prefix: prefix of columns to be split from table. prefix should be in
                one of self.forward_relations or self.reverse_relations
        Returns:
            table: original table with columns removed
            extracted_table: new foreign table.
            If it is a forward relation:
                - the extracted table will have an 'id' column with valid uuids.
                - Existing {relation_prefix}_id values in the base table are kept, and
                  rows with different ids stay different entities even when their
                  attributes match. See _assign_forward_relation_ids.
                - Verifiably incomplete rows are dropped from the extracted table.
                  Their {relation_prefix}_id in the base table is kept if present,
                  and is NaN otherwise.
                - Rows without an id are mapped to the entity with the same attributes
                  when exactly one exists, and otherwise become a new entity.
                - The extracted table can hold several rows for one id when that id was
                  reported with different attributes; convert_to_3NF_from_1NF combines
                  them.
        """
        # step 0 - get foreign table details (name, schema, columns)
        extracted_table_name = self.get_foreign_table_name(
            table_name,
            relation_prefix,
        )
        extracted_table_schema = self.schema.schema[extracted_table_name]

        extracted_table = table.copy()
        # step 1 - validate legal input
        if relation_prefix in self.schema.schema[table_name].reverse_relations:
            if "id" not in table.columns or table["id"].isna().any():
                raise ValueError(
                    f"Table '{table_name}' has no id column or has NaN ids. "
                    f"Since {relation_prefix} is a reverse relation, the base table "
                    "should have an id column with valid uuids before splitting."
                )
            extracted_table = self._add_relation_to_extracted_table(
                extracted_table, table_name, self.schema, relation_prefix
            )
        if f"{relation_prefix}{SPLIT}id" in table.columns:
            raise ValueError(
                f"Column {relation_prefix}{SPLIT}id already exists in table {table_name}"
                "This should not happen because derivative table id columns are"
                "supposed to be named <relation_prefix>_id in state schemas."
            )

        # step 2 - drop incomplete rows and duplicates. Forward relations are
        # deduplicated by id and attributes in step 3 instead.
        is_forward_relation = (
            relation_prefix in self.schema.schema[table_name].forward_relations
        )
        foreign_columns = self._get_foreign_columns(extracted_table, relation_prefix)
        extracted_table = self._drop_verifiably_incomplete_rows(
            extracted_table_name, extracted_table, relation_prefix
        )
        if not is_forward_relation:
            extracted_table = extracted_table.drop_duplicates(subset=foreign_columns)

        # step 3 - handle ids and metadata columns
        extracted_table[f"{relation_prefix}{SPLIT}reported_state"] = extracted_table[
            "reported_state"
        ]
        if relation_prefix in self.schema.schema[table_name].reverse_relations:
            extracted_table = self._add_relationship_metadata_to_extracted_table(
                extracted_table, table_name, relation_prefix
            )
        elif is_forward_relation:
            table, extracted_table = self._assign_forward_relation_ids(
                table, extracted_table, extracted_table_name, relation_prefix
            )
            extracted_table[f"{relation_prefix}{SPLIT}id"] = extracted_table[
                f"{relation_prefix}{ID_SUFFIX}"
            ]
        elif "id" in self.schema.schema[extracted_table_name].attributes:
            # If a non-forward relation has an id column, we want to handle it
            # Does this ever happen?
            handle_id_column(
                extracted_table,
                extracted_table_schema,
                extracted_table_name,
                self.id_mapping,
                id_column="id",
            )

        # step 4 - split foreign table off of old base table
        foreign_columns_in_base_table = self._get_foreign_columns(
            extracted_table, relation_prefix
        )
        foreign_columns_in_foreign_table = [
            column[len(relation_prefix) + len(SPLIT) :]
            for column in foreign_columns_in_base_table
        ]
        extracted_table = extracted_table[foreign_columns_in_base_table]
        extracted_table.columns = foreign_columns_in_foreign_table
        table = table.drop(columns=foreign_columns_in_base_table, errors="ignore")
        return table, extracted_table

    def _find_column_prefixes_in_1NF(
        self, table: pd.DataFrame, table_name: str
    ) -> list[str]:
        """Find all columns in 1NF for a given table

        Args:
            table: a valid table of table_name given schema
            table_name: used to identify which type of table in given schema
                table should fit
        Raises:
            ValueError if a column for the table contains a split sequence but
                does not have a corresponding relation in the schema

        Returns:
            list of columns in 1NF for table
        """
        table_schema = self.schema.schema[table_name]
        column_prefixes_in_1NF = set()
        for column in table.columns:
            if SPLIT in column and column.split(SPLIT)[0] not in table_schema.relations:
                raise ValueError(
                    f"Column {column} has a split sequence ({SPLIT}), but the prefix"
                    f"{column.split(SPLIT)[0]} does not appear in {table_name}'s valid"
                    f"relations: {table_schema.relations}"
                )
            elif column.split(SPLIT)[0] in table_schema.relations:
                column_prefixes_in_1NF.add(column.split(SPLIT)[0])
        return list(column_prefixes_in_1NF)

    def _convert_table_to_3NF_from_1NF(
        self,
        table: pd.DataFrame,
        table_name: str,
    ) -> dict[str, pd.DataFrame]:
        """Normalize table and any derivative tables to desired level given table schema

        Args:
            table: a valid table of table_name given schema
            table_name: used to identify which type of table in given schema
                table should fit

        Returns:
            Dictionary mapping table_names to tables attaining the desired
                normalization level.
        """
        # Step 1: Figure out which columns need to be normalized
        column_prefixes_in_1NF = self._find_column_prefixes_in_1NF(table, table_name)
        # Step 2: Go through columns that need to be normalized, if any.
        # Each decomposition may return a derivative table that also needs
        # normalization so this function will run on each derived table.
        updated_database = self.schema.empty_database()
        active_table = table
        # sorted column_prefixes_in_1NF to ensure deterministic behavior
        for first_column_token in sorted(column_prefixes_in_1NF):
            # this is where the heavy lifting is done and a new foreign table
            # is created derived from the columns that did not belong in base table
            active_table, extracted_table = self._split_prefixed_columns(
                active_table, table_name, first_column_token
            )
            # Step 4 - Recursive step. Bring the foreign derivative table to the
            # desired form and all ensuing derivative tables
            extracted_table_derived_database = self._convert_table_to_3NF_from_1NF(
                extracted_table,
                self.schema.schema[table_name].relations[first_column_token],
            )
            for derived_table_name in extracted_table_derived_database:
                updated_database[derived_table_name] = pd.concat(
                    [
                        updated_database[derived_table_name],
                        extracted_table_derived_database[derived_table_name],
                    ],
                    ignore_index=True,
                )
        updated_database[table_name] = pd.concat(
            [updated_database[table_name], active_table],
            ignore_index=True,
        )
        return updated_database

    def convert_to_3NF_from_1NF(self) -> None:
        """Converts database in first normal form (1NF) to third normal form (3NF)

        When called, each table in self.database should have appropriate ids, and
        a filled 'reported_state' column.

        Args:
            first_normal_form_database: TODO: spec of database somewhere
            schema: database schema that should contain all keys in database

        Modifies:
            database: updates database so that each table is in at least 3NF
        """
        normalized_database = self.schema.empty_database()
        for table_name, table in self.database.items():
            if table.index.name:
                table = table.reset_index()
            updated_database = self._convert_table_to_3NF_from_1NF(
                table,
                table_name,
            )
            for normalized_table_name in updated_database:
                normalized_database[normalized_table_name] = pd.concat(
                    [
                        normalized_database[normalized_table_name],
                        updated_database[normalized_table_name],
                    ],
                    ignore_index=True,
                )
        for normalized_table_name, table in normalized_database.items():
            normalized_database[normalized_table_name] = (
                self._combine_rows_sharing_an_id(table, normalized_table_name)
            )
        self.database = normalized_database

    def _consolidate_database(
        self,
        database: dict[str, list[pd.DataFrame]],
    ) -> dict[str, pd.DataFrame]:
        """Consolidate the database by concatenating each table name"""
        consolidated_database = {}
        for table_name in database:
            if database[table_name] == []:
                continue
            consolidated_table = pd.concat(database[table_name], ignore_index=True)
            if "id" in consolidated_table.columns:
                consolidated_table = consolidated_table.set_index("id")
            consolidated_database[table_name] = consolidated_table
        return consolidated_database

    def _add_all_columns(
        self, database: dict[str, pd.DataFrame]
    ) -> dict[str, pd.DataFrame]:
        """Add all columns to the database"""
        for table_name in database:
            for column in self.schema.schema[table_name].attributes:
                if column not in database[table_name].columns:
                    database[table_name][column] = None
        return database

    def normalize_database(self) -> dict[str, pd.DataFrame]:
        """Bring a database to 4NF given the provided schema

        Returns:
            dictionary mapping table names to respective tables
        """
        # to start, ensure all ids are properly handled in the database
        for table_name, table in self.database.items():
            # In each table, if its schema has an 'id' column, ensure each row has a
            # valid uuid, and any state-provided ids are mapped to uuids.
            handle_id_column(
                table,
                self.schema.schema[table_name],
                table_name,
                self.id_mapping,
                id_column="id",
            )
            # For each column that ends with ID_SUFFIX, ensure that each existing
            # id is mapped and replaced with a uuid. Rows with null ids are left
            # as null at this point. This is done because these id columns are
            # foreign keys to other tables that may actually be null.
            for column in table.columns:
                if column.endswith(ID_SUFFIX):
                    column_reference_table = self.get_foreign_table_name(
                        table_name, column[: -len(ID_SUFFIX)]
                    )
                    handle_existing_ids(
                        table, column_reference_table, self.id_mapping, column
                    )

        # bring to 1NF
        for table_name in self.database:
            self.convert_to_1NF_from_unnormalized(table_name)

        # bring to 3NF
        self.convert_to_3NF_from_1NF()
        return self.database

    def convert_to_class_table_from_single_table(
        self, database_single_table: dict[str, pd.DataFrame], data_schema: DataSchema
    ) -> dict[str, pd.DataFrame]:
        """[WIP] convert a database from a single table to class table inheritance strategy"""
        database_class_table = {}
        for table_name in database_single_table:
            original_table = database_single_table[table_name]
            data_schema.inheritance_strategy = "class table inheritance"
            for child_table in data_schema.schema[table_name].child_tables:
                child_schema = data_schema.schema[child_table]
                child_table = original_table[
                    [
                        col
                        for col in original_table.columns
                        if col in child_schema.attributes
                    ]
                ]
                child_table = child_table.dropna(how="all")
                database_class_table[child_table] = child_table
            parent_schema = data_schema.schema[table_name]
            parent_table = original_table[
                [
                    col
                    for col in original_table.columns
                    if col in parent_schema.attributes
                ]
            ]
            database_class_table[table_name] = parent_table
        return database_class_table


def combine_rows_sharing_an_id(table: pd.DataFrame, table_name: str) -> pd.DataFrame:
    """Combine rows that have the same id into one row

    The same entity can be extracted more than once: as the donor in one
    transaction and the recipient in another, reported with different values
    in different rows, or found in more than one chunk. Each column keeps its
    first non-null value. Ids whose rows disagree on a value are reported in a
    warning.

    Args:
        table: normalized table
        table_name: name of table, used in the warning

    Returns:
        table with at most one row per non-null id. Rows without an id are
        kept as they are, after the combined rows.
    """
    if "id" not in table.columns or not table["id"].dropna().duplicated().any():
        return table
    has_id = table["id"].notna()
    rows_with_id = table[has_id]
    repeated = rows_with_id[rows_with_id["id"].duplicated(keep=False)]
    # an id conflicts when any column has more than one distinct value
    conflicting = (repeated.groupby("id").nunique() > 1).any(axis=1)  # noqa: PD101
    if conflicting.any():
        warnings.warn(
            f"{conflicting.sum()} {table_name} id(s) had rows with conflicting "
            "values; kept the first non-null value in each column. Example ids: "
            f"{', '.join(map(str, conflicting[conflicting].index[:5]))}",
            stacklevel=2,
        )
    combined = rows_with_id.groupby("id", sort=False, as_index=False).first()
    if has_id.all():
        return combined
    return pd.concat([combined, table[~has_id]], ignore_index=True)


def combine_duplicates_across_chunks(
    directory: Path, format: FileFormat, schema: DataSchema | Path | str
) -> None:
    """Remove the duplicates that appending chunks leaves in saved tables

    normalize and clean remove duplicates within each chunk, then append each
    chunk to the table files, so an entity found in more than one chunk gets
    one row per chunk. This does across all chunks what is already done
    within each one:
    - tables with an id column: rows sharing an id are combined, as in
      combine_rows_sharing_an_id. Only the id column is read unless an id
      repeats.
    - tables that normalize splits off another table and that have no id
      (Address, Membership): exact duplicate rows are dropped, as
      _split_prefixed_columns does within a chunk.
    Other tables, such as Transaction, are left alone: two identical
    transactions can be two real payments.

    Args:
        directory: Folder holding one file per table.
        format: "csv" or "parquet".
        schema: DataSchema, or path to the schema YAML.
    """
    if not isinstance(schema, DataSchema):
        schema = DataSchema(schema)
    split_off_tables = {
        schema.schema[table_name].relations[prefix]
        for table_name in schema.schema
        for prefix in schema.schema[table_name].reverse_relations
    }
    for file_path in sorted(Path(directory).glob(f"*.{format}")):
        table_name = file_path.stem
        ids = table_column(file_path, format, "id")
        if ids is not None:
            if not ids.dropna().duplicated().any():
                continue
            table = load_table(file_path, format)
            deduplicated = combine_rows_sharing_an_id(table, table_name)
        elif table_name in split_off_tables:
            table = load_table(file_path, format)
            if not table.duplicated().any():
                continue
            deduplicated = table.drop_duplicates(ignore_index=True)
        else:
            continue
        save_database({table_name: deduplicated}, directory, format=format)
