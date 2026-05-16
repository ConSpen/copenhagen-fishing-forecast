"""Tests for the seasonal species recommender."""
from __future__ import annotations

from fishing_forecast.species import COD, GARFISH, MACKEREL, SEA_TROUT, recommend


def test_may_recommends_garfish():
    # Mid-May, ~11 C water — garfish are at their Øresund peak.
    rec = recommend(month=5, water_temp_c=11.0)
    assert rec.primary is GARFISH
    assert "peak" in rec.reasoning.lower()


def test_january_recommends_cod():
    # January, cold water — cod are at their peak and sea trout are the backup.
    rec = recommend(month=1, water_temp_c=4.0)
    assert rec.primary is COD
    assert rec.secondary is SEA_TROUT


def test_july_recommends_mackerel():
    # July, warm water — mackerel are peaking.
    rec = recommend(month=7, water_temp_c=17.0)
    assert rec.primary is MACKEREL


def test_single_species_season_has_no_secondary():
    # August: only mackerel are realistically a shore-spinning target.
    rec = recommend(month=8, water_temp_c=18.0)
    assert rec.primary is MACKEREL
    assert rec.secondary is None


def test_recommendation_works_without_water_temperature():
    rec = recommend(month=5, water_temp_c=None)
    assert rec.primary is GARFISH
    assert rec.reasoning  # still produces a readable explanation
