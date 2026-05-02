"""Verify the canonical ``tools.py`` enums flow through unchanged.

The previous code defined parallel ``*Enum`` aliases in ``server.py``. We
removed them in favor of importing the canonical types from ``tools.py``.
These tests assert that the values land in the API payload exactly as the
backend expects.
"""

from __future__ import annotations

from unittest.mock import patch

from quantdata_mcp import server
from quantdata_mcp.tools import (
    AggregationPeriod,
    ContractTypeFilter,
    DataMode,
    GreekMode,
    MoneynessType,
    RepresentationMode,
    TradeSideCodeType,
)


def _put_tool_payloads(mock_client):
    return [
        c.kwargs["json"]
        for c in mock_client._make_request.call_args_list
        if c.args[0] == "PUT" and c.args[1] == "tool"
    ]


def test_greek_mode_delta_lands_in_metadata_put(mock_client, mock_specs) -> None:
    with patch.object(server, "_get_client", lambda: mock_client), patch.object(
        server, "_get_specs", lambda: mock_specs
    ), patch.object(server, "_get_page_id", lambda: "page-abc"):
        server.qd_get_exposure_by_strike(
            greek_type=GreekMode.DELTA,
            ticker="SPX",
        )

    payloads = _put_tool_payloads(mock_client)
    assert payloads, "Expected at least one PUT /tool"
    apply_payload = payloads[0]
    assert apply_payload["metadata"]["greekModeType"] == "DELTA"
    assert apply_payload["metadata"]["representationModeType"] == "PER_ONE_PERCENT_MOVE"


def test_aggregation_and_data_mode_enum_wiring(mock_client, mock_specs) -> None:
    with patch.object(server, "_get_client", lambda: mock_client), patch.object(
        server, "_get_specs", lambda: mock_specs
    ), patch.object(server, "_get_page_id", lambda: "page-abc"):
        server.qd_get_net_flow(
            ticker="SPX",
            aggregation=AggregationPeriod.FIFTEEN_MINUTE,
            data_mode=DataMode.VOLUME,
        )

    apply_payload = _put_tool_payloads(mock_client)[0]
    assert apply_payload["metadata"]["aggregationPeriodType"] == "FIFTEEN_MINUTE"
    assert apply_payload["metadata"]["dataModeType"] == "VOLUME"


def test_moneyness_and_trade_side_filter_wiring(mock_client, mock_specs) -> None:
    with patch.object(server, "_get_client", lambda: mock_client), patch.object(
        server, "_get_specs", lambda: mock_specs
    ), patch.object(server, "_get_page_id", lambda: "page-abc"):
        server.qd_get_net_flow(
            ticker="SPX",
            moneyness=[MoneynessType.OTM, MoneynessType.ATM],
            trade_side=[TradeSideCodeType.AA, TradeSideCodeType.BB],
        )

    apply_payload = _put_tool_payloads(mock_client)[0]
    new_filter = apply_payload["metadata"]["filter"]
    assert new_filter["moneynessMoneyType"]["value"] == [
        "OUT_OF_THE_MONEY",
        "AT_THE_MONEY",
    ]
    assert new_filter["tradeSideCodeType"]["value"] == ["AA", "BB"]


def test_contract_type_filter_enum(mock_client, mock_specs) -> None:
    with patch.object(server, "_get_client", lambda: mock_client), patch.object(
        server, "_get_specs", lambda: mock_specs
    ), patch.object(server, "_get_page_id", lambda: "page-abc"):
        server.qd_get_iv_rank(contract_type=[ContractTypeFilter.PUT])

    apply_payload = _put_tool_payloads(mock_client)[0]
    assert apply_payload["metadata"]["filter"]["contractType"]["value"] == ["PUT"]


def test_representation_mode_raw(mock_client, mock_specs) -> None:
    with patch.object(server, "_get_client", lambda: mock_client), patch.object(
        server, "_get_specs", lambda: mock_specs
    ), patch.object(server, "_get_page_id", lambda: "page-abc"):
        server.qd_get_exposure_by_expiration(
            greek_type=GreekMode.VANNA,
            representation_mode=RepresentationMode.RAW,
        )

    apply_payload = _put_tool_payloads(mock_client)[0]
    assert apply_payload["metadata"]["greekModeType"] == "VANNA"
    assert apply_payload["metadata"]["representationModeType"] == "RAW"
