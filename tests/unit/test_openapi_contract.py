"""docs/openapi-v1.json is the contract Repo B builds against; it must match the code."""

from src.routers.contract import CONTRACT_PATH, render, v1_openapi


def test_committed_contract_matches_the_code():
    assert CONTRACT_PATH.read_text(encoding="utf-8") == render(), (
        "regenerate with: uv run python -m src.routers.contract")


def test_contract_covers_only_v1_and_documents_every_status():
    spec = v1_openapi()
    assert list(spec["paths"]) == ["/v1/retrieve"]
    assert set(spec["paths"]["/v1/retrieve"]["post"]["responses"]) == {"200", "401", "422", "500", "503", "504"}
    assert "tenant_id" not in spec["components"]["schemas"]["RetrieveRequest"]["properties"]
    assert {"RetrieveResponse", "ParentSection", "ErrorBody"} <= set(spec["components"]["schemas"])
