import pytest
import yaml
from utils.schema import (
    DataSchema,
    TableSchema,
)


@pytest.fixture
def sample_schemas():
    """Returns a dictionary of different sample schemas for testing."""
    return {
        "complete": yaml.safe_load("""
            Person:
              attributes: ["id", "name", "age", "gender", "phone"]
              relations:
                - prefix: address
                  table: "Address"
                  direction: reverse
                  reverse_relation_name: "person_id"
              enum_columns:
                gender: ["Male", "Female", "Other"]
              required_attributes: ["id", "name"]
              child_tables: ["Teacher", "Student"]

            Teacher:
              attributes: ["position", "department"]
              parent_table: "Person"

            Student:
              attributes: ["grade", "homeroom_id"]
              parent_table: "Person"
              enum_columns:
                grade: ["Freshman", "Sophomore", "Junior", "Senior"]
              relations:
                - prefix: homeroom
                  table: Class
                  direction: forward

            Address:
              attributes: ["line_1", "line_2", "city", "state", "zipcode", "person_id"]
              required_attributes: ["person_id"]
              relations:
                - prefix: person
                  table: "Person"
                  direction: forward

            Class:
              required_attributes: ["id"]
              attributes: ["id", "subject", "teacher_id"]
              relations:
                - prefix: teacher
                  table: Teacher
                  direction: forward
        """),
        "missing_attribute_key": yaml.safe_load("""
            Person:
              attributes: ["id", "name", "dob"]
              enum_columns:
                gender: ["Male", "Female", "Other"]
        """),
        "missing_parent": yaml.safe_load("""
            Person:
              attributes: ["id", "name"]
              child_tables: ["Teacher"]

            Teacher:
              attributes: ["position"]
        """),
        "missing_forward_relation": yaml.safe_load("""
            Person:
              attributes: ["id", "homeroom_id", "name"]
              relations:
                - prefix: homeroom
                  table: Class
                  direction: forward
        """),
        "missing_reverse_relation_name": yaml.safe_load("""
            Person:
              attributes: ["id", "name"]
              relations:
                - prefix: address
                  table: "Address"
                  direction: reverse
            Address:
              attributes: ["city", "state", "person_id"]
              relations:
                - prefix: person
                  table: "Person"
                  direction: forward
        """),
    }


@pytest.fixture
def schema_instance(request, sample_schemas):
    """Returns a TableSchema instance based on test parameters."""
    schema_type, table_name, inheritance_strategy = request.param
    return TableSchema(sample_schemas[schema_type], table_name, inheritance_strategy)


@pytest.fixture
def complete_data_schema_data(sample_schemas):
    """Returns a DataSchema instance based on test parameters"""
    schema_data = sample_schemas["complete"]
    return schema_data


@pytest.mark.parametrize(
    "schema_instance,expected_attributes",
    [
        (
            ("complete", "Person", "single table inheritance"),
            {
                "id",
                "name",
                "age",
                "gender",
                "phone",
                "position",
                "department",
                "grade",
                "homeroom_id",
            },
        ),
        (
            ("complete", "Teacher", "single table inheritance"),
            {"id", "name", "age", "gender", "phone", "position", "department"},
        ),
        (
            ("complete", "Student", "single table inheritance"),
            {"id", "name", "age", "gender", "phone", "grade", "homeroom_id"},
        ),
        (
            ("complete", "Person", "class table inheritance"),
            {"id", "name", "age", "gender", "phone"},
        ),
        (
            ("complete", "Teacher", "class table inheritance"),
            {"position", "department"},
        ),
        (
            ("complete", "Student", "class table inheritance"),
            {"grade", "homeroom_id"},
        ),
    ],
    indirect=["schema_instance"],
)
def test_direct_attributes(schema_instance, expected_attributes):
    assert set(schema_instance.attributes) == expected_attributes


@pytest.mark.parametrize(
    "schema_instance,expected_enum",
    [
        (
            ("complete", "Person", "single table inheritance"),
            {
                "gender": ["Male", "Female", "Other"],
                "grade": ["Freshman", "Sophomore", "Junior", "Senior"],
            },
        ),
        (
            ("complete", "Student", "single table inheritance"),
            {
                "gender": ["Male", "Female", "Other"],
                "grade": ["Freshman", "Sophomore", "Junior", "Senior"],
            },
        ),
        (
            ("complete", "Teacher", "single table inheritance"),
            {"gender": ["Male", "Female", "Other"]},
        ),
    ],
    indirect=["schema_instance"],
)
def test_enum_columns(schema_instance, expected_enum):
    assert schema_instance.enum_columns == expected_enum


@pytest.mark.parametrize(
    "schema_instance,expected_relations",
    [
        (("complete", "Student", "single table inheritance"), {"homeroom": "Class"}),
        (("complete", "Person", "single table inheritance"), {"homeroom": "Class"}),
        (("complete", "Address", "single table inheritance"), {"person": "Person"}),
        (("complete", "Class", "single table inheritance"), {"teacher": "Person"}),
        (("complete", "Class", "class table inheritance"), {"teacher": "Teacher"}),
    ],
    indirect=["schema_instance"],
)
def test_forward_relations(schema_instance, expected_relations):
    assert schema_instance.forward_relations == expected_relations


@pytest.mark.parametrize(
    "schema_instance,expected_relations,expected_relation_names",
    [
        (
            (
                "complete",
                "Student",
                "single table inheritance",
            ),
            {"address": "Address"},
            {"address": "person_id"},
        ),
        (
            ("complete", "Person", "single table inheritance"),
            {"address": "Address"},
            {"address": "person_id"},
        ),
        (("complete", "Student", "class table inheritance"), {}, {}),
    ],
    indirect=["schema_instance"],
)
def test_reverse_relations(
    schema_instance, expected_relations, expected_relation_names
):
    assert schema_instance.reverse_relations == expected_relations
    assert schema_instance.reverse_relation_names == expected_relation_names


@pytest.mark.parametrize(
    "schema_instance,expected_parent",
    [
        (("complete", "Teacher", "single table inheritance"), "Person"),
        (("complete", "Student", "single table inheritance"), "Person"),
        (("complete", "Person", "single table inheritance"), None),
    ],
    indirect=["schema_instance"],
)
def test_parent_table(schema_instance, expected_parent):
    assert schema_instance.parent_table == expected_parent


@pytest.mark.parametrize(
    "schema_key, should_raise_error",
    [
        ("complete", False),  # Complete schema should pass validation
        ("missing_attribute_key", True),  # Schema missing attributes should fail
        ("missing_parent", True),  # Schema with missing parent should fail
    ],
)
def test_schema_validation(sample_schemas, schema_key, should_raise_error, tmp_path):
    """Tests schema validation for different sample schemas."""
    schema_data = sample_schemas[schema_key]

    # Save the YAML schema to a temporary file
    schema_file = tmp_path / "test_schema.yaml"
    schema_file.write_text(yaml.dump(schema_data))

    if should_raise_error:
        with pytest.raises(ValueError):
            DataSchema(schema_file)  # This should raise an error
    else:
        try:
            DataSchema(schema_file)  # Should pass without errors
        except ValueError as e:
            pytest.fail(f"Unexpected validation error: {e}")


@pytest.mark.parametrize(
    "schema_key, expected_error",
    [
        (
            "missing_parent",
            "Teacher lists Person as a child, but Person does not list Teacher as a parent.",
        ),
        (
            "missing_attribute_key",
            "Error in Person: enum_columns 'gender' must be listed in attributes.",
        ),
    ],
)
def test_missing_parent(sample_schemas, schema_key, expected_error, tmp_path):
    """Ensures schemas with missing parent references fail validation."""
    schema_data = sample_schemas[schema_key]

    # Save YAML schema to temp file
    schema_file = tmp_path / "test_schema.yaml"
    schema_file.write_text(yaml.dump(schema_data))

    with pytest.raises(ValueError) as excinfo:
        DataSchema(schema_file)

    assert expected_error in str(excinfo.value)


@pytest.mark.parametrize(
    "schema_key, expected_error",
    [
        (
            "missing_forward_relation",
            "Error in Person: relation table 'Class' must be a valid table.",
        ),
    ],
)
def test_invalid_forward_relations(
    sample_schemas, schema_key, expected_error, tmp_path
):
    """Ensures forward relations keys exist in attributes and values point to valid tables."""
    schema_data = sample_schemas[schema_key]

    # Save YAML schema to temp file
    schema_file = tmp_path / "test_schema.yaml"
    schema_file.write_text(yaml.dump(schema_data))

    with pytest.raises(ValueError) as excinfo:
        DataSchema(schema_file)

    assert expected_error in str(excinfo.value)


def test_invalid_table_name(complete_data_schema_data, tmp_path):
    """Tests behavior when an invalid table name is given."""
    schema_file = tmp_path / "test_schema.yaml"
    schema_file.write_text(yaml.dump(complete_data_schema_data))

    sample_data_schema = DataSchema(schema_file)
    with pytest.raises(KeyError):
        TableSchema(sample_data_schema.raw_data_schema, "InvalidType")


def test_missing_reverse_relation_name(sample_schemas, tmp_path):
    """Test if reverse relation name is missing"""
    schema_file = tmp_path / "test_schema.yaml"
    schema_file.write_text(yaml.dump(sample_schemas["missing_reverse_relation_name"]))

    with pytest.raises(ValueError) as excinfo:
        DataSchema(schema_file)

    assert "reverse relation 'address' must have 'reverse_relation_name' field" in str(
        excinfo.value
    )
