import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def schema(name: str) -> dict:
    return json.loads((ROOT / "presets" / "belarus-market" / "schemas" / name).read_text(encoding="utf-8"))


def test_deposit_schema_exposes_all_retail_and_effective_condition_fields():
    properties = schema("bank-deposit-offer-v2.json")["properties"]

    assert {"revocability", "replenishment_allowed", "partial_withdrawal_allowed", "capitalization", "interest_payment_frequency", "effective_from", "effective_to", "evidence_ref"} <= set(properties)


def test_news_schema_has_stable_external_identity_and_selection_evidence():
    properties = schema("market-news-v1.json")["properties"]

    assert {"external_id", "selection_rule_version", "selection_reason", "evidence_ref"} <= set(properties)


def test_indicator_schema_has_series_dimensions_and_evidence():
    properties = schema("market-indicator-v1.json")["properties"]

    assert {"series_id", "unit", "dimensions", "evidence_ref"} <= set(properties)
