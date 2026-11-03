import json
import random
from unittest.mock import patch

import duckdb
import pandas as pd
import pytest
from utils.link import train


@pytest.fixture
def con():
    """In-memory database with a small table of fake transactors"""
    rng = random.Random(0)  # noqa: S311 (fake test data, not security)
    rows = [
        {
            "id": str(i),
            "transactor_type": rng.choice(["Individual", "Organization"]),
            "first_name": rng.choice(["ann", "bob", "cat", "dan", "eve", "fay"]),
            "last_name": rng.choice(["smith", "jones", "lee", "khan"]),
            "address_city": rng.choice(["chicago", "austin", "reno"]),
            "address_street_name": rng.choice(["main st", "oak ave", "elm rd"]),
            "name_suffix": rng.choice(["jr", "sr", "none"]),
            "name_prefix": rng.choice(["mr", "ms", "none"]),
            "employer_full_name": rng.choice(["acme", "globex", "initech"]),
            "employer_role": rng.choice(["ceo", "clerk"]),
        }
        for i in range(300)
    ]
    connection = duckdb.connect()
    connection.register("df", pd.DataFrame(rows))
    connection.execute("CREATE TABLE transactors AS SELECT * FROM df")
    return connection


def final_m_probabilities(model_path):
    settings = json.loads(model_path.read_text())
    return [
        level.get("m_probability")
        for comparison in settings["comparisons"]
        for level in comparison["comparison_levels"]
    ]


def test_train_saves_checkpoint_and_model(con, tmp_path):
    model, checkpoint = tmp_path / "model.json", tmp_path / "model_checkpoint.json"
    train.train_splink(con, "transactors", model, checkpoint_path=checkpoint)
    assert model.exists()
    assert train.TRAINED_M_HISTORY_KEY in json.loads(checkpoint.read_text())


def test_resume_skips_first_session_and_matches_fresh_model(con, tmp_path):
    model, checkpoint = tmp_path / "model.json", tmp_path / "model_checkpoint.json"
    train.train_splink(con, "transactors", model, checkpoint_path=checkpoint)
    fresh = final_m_probabilities(model)

    resumed_model = tmp_path / "resumed.json"
    with patch.object(train, "SettingsCreator", side_effect=AssertionError):
        train.train_splink(
            con,
            "transactors",
            resumed_model,
            checkpoint_path=checkpoint,
            resume_from_checkpoint=True,
        )
    assert final_m_probabilities(resumed_model) == pytest.approx(fresh)


def test_resume_without_checkpoint_trains_from_scratch(con, tmp_path):
    model, checkpoint = tmp_path / "model.json", tmp_path / "missing.json"
    train.train_splink(
        con,
        "transactors",
        model,
        checkpoint_path=checkpoint,
        resume_from_checkpoint=True,
    )
    assert model.exists()
    assert checkpoint.exists()


def test_training_sample_is_individuals_from_given_table(con):
    train.create_random_individuals_subset(con, "transactors", 50)
    types = con.execute(
        f"SELECT DISTINCT transactor_type FROM {train.TRAINING_SAMPLE_TABLE}"
    ).fetchall()
    assert types == [("Individual",)]
    # filtering happens before sampling, so the full sample size is returned
    count = con.execute(
        f"SELECT COUNT(*) FROM {train.TRAINING_SAMPLE_TABLE}"
    ).fetchone()[0]
    assert count == 50
    # can be called again on the same connection
    train.create_random_individuals_subset(con, "transactors", 10)
