"""Unit tests for Pixel Agents HTTP and WebSocket boundary contracts."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from floorplan import models as compatibility_models
from floorplan.contracts.layout import OfficeLayout, RawOfficeLayout
from floorplan.contracts.outbound import layout_loaded
from floorplan.contracts.pixel_index import (
    LayoutDetail,
    LayoutFiles,
    LayoutListResponse,
    LayoutSummary,
    PublicAuthor,
)
from floorplan.contracts.websocket import (
    AuthorizeMessage,
    ImportLayoutMessage,
    InvalidClientMessageError,
    RequestDiagnosticsMessage,
    SaveAgentSeatsMessage,
    SaveLayoutMessage,
    WebviewReadyMessage,
    parse_client_message,
)


def valid_layout(**updates: object) -> RawOfficeLayout:
    layout: RawOfficeLayout = {
        "version": 1,
        "cols": 2,
        "rows": 1,
        "tiles": [1, 2],
        "tileColors": [None, "#fff"],
        "furniture": [{"uid": "desk-1", "type": "DESK"}],
    }
    layout.update(updates)  # type: ignore[arg-type]
    return layout


class TestPixelIndexModels:
    def test_historical_imports_are_identity_reexports(self) -> None:
        assert compatibility_models.PublicAuthor is PublicAuthor
        assert compatibility_models.LayoutFiles is LayoutFiles
        assert compatibility_models.LayoutSummary is LayoutSummary
        assert compatibility_models.LayoutListResponse is LayoutListResponse
        assert compatibility_models.LayoutDetail is LayoutDetail

    def test_list_contract_requires_slug_but_tolerates_future_fields(self) -> None:
        response = LayoutListResponse.model_validate(
            {
                "layouts": [
                    {
                        "slug": "cozy-office",
                        "author": {"displayName": "Builder", "avatar": "future"},
                        "files": {"layout": "layout.json", "future": True},
                        "futureSummaryField": {"nested": True},
                    }
                ],
                "total": 1,
                "futurePageField": "ignored",
            }
        )

        assert response.layouts[0].slug == "cozy-office"
        assert response.layouts[0].author == PublicAuthor(displayName="Builder")
        assert response.layouts[0].files == LayoutFiles(layout="layout.json")
        assert response.layouts[0].model_extra is None
        with pytest.raises(ValidationError):
            LayoutListResponse.model_validate({"layouts": [{"title": "No slug"}]})

    def test_detail_requires_the_opaque_layout_blob(self) -> None:
        detail = LayoutDetail.model_validate(
            {"slug": "one", "layout": {"future": [1, {"nested": True}]}}
        )

        assert detail.layout == {"future": [1, {"nested": True}]}
        with pytest.raises(ValidationError):
            LayoutDetail.model_validate({"slug": "one"})


class TestOfficeLayout:
    def test_validates_and_preserves_original_json_shape(self) -> None:
        raw = valid_layout(
            layoutRevision=7,
            futureObject={"enabled": True, "values": [1, "two"]},
        )

        layout = OfficeLayout.model_validate(raw)

        assert layout.to_raw() == raw
        assert layout.model_extra == {
            "layoutRevision": 7,
            "futureObject": {"enabled": True, "values": [1, "two"]},
        }

    def test_does_not_add_an_omitted_optional_field(self) -> None:
        raw = valid_layout()
        del raw["tileColors"]

        layout = OfficeLayout.model_validate(raw)

        assert "tileColors" not in layout.to_raw()

    @pytest.mark.parametrize(
        "updates",
        [
            {"version": 2},
            {"version": 1.0},
            {"cols": 0},
            {"cols": True},
            {"rows": -1},
            {"tiles": [1]},
            {"furniture": {}},
            {"tileColors": [None]},
        ],
    )
    def test_rejects_invalid_structure(self, updates: dict[str, object]) -> None:
        with pytest.raises(ValidationError):
            OfficeLayout.model_validate(valid_layout(**updates))


class TestWebSocketIngress:
    @pytest.mark.parametrize(
        ("payload", "expected_type"),
        [
            ({"type": "authorize", "ticket": "abc"}, AuthorizeMessage),
            ({"type": "webviewReady"}, WebviewReadyMessage),
            ({"type": "saveLayout", "layout": valid_layout()}, SaveLayoutMessage),
            (
                {
                    "type": "saveAgentSeats",
                    "seats": {"-1": {"palette": 2, "hueShift": 45, "seatId": "chair:1"}},
                },
                SaveAgentSeatsMessage,
            ),
            ({"type": "requestDiagnostics"}, RequestDiagnosticsMessage),
            ({"type": "importLayout"}, ImportLayoutMessage),
        ],
    )
    def test_parses_each_known_message(
        self,
        payload: dict[str, object],
        expected_type: type[object],
    ) -> None:
        assert isinstance(parse_client_message(payload), expected_type)

    def test_known_messages_tolerate_future_fields(self) -> None:
        parsed = parse_client_message(
            {
                "type": "saveAgentSeats",
                "futureEnvelopeField": 1,
                "seats": {"-1": {"seatId": "desk:1", "futureSeatField": True}},
            }
        )

        assert isinstance(parsed, SaveAgentSeatsMessage)
        assert parsed.model_extra == {"futureEnvelopeField": 1}
        assert parsed.seats["-1"].model_extra == {"futureSeatField": True}

    def test_unknown_message_types_are_ignored(self) -> None:
        assert parse_client_message({"type": "futureMessage", "value": 1}) is None

    @pytest.mark.parametrize(
        "payload",
        [
            None,
            {},
            {"type": 1},
            {"type": "authorize"},
            {"type": "authorize", "ticket": ""},
            {"type": "saveLayout", "layout": {"version": 1}},
            {"type": "saveAgentSeats", "seats": []},
            {"type": "saveAgentSeats", "seats": {"-1": "chair:1"}},
            {"type": "saveAgentSeats", "seats": {"-1": {"hueShift": 361}}},
        ],
    )
    def test_malformed_messages_raise_a_typed_error(self, payload: object) -> None:
        with pytest.raises(InvalidClientMessageError) as caught:
            parse_client_message(payload)

        assert caught.value.details

    def test_invalid_error_identifies_a_known_message(self) -> None:
        with pytest.raises(InvalidClientMessageError) as caught:
            parse_client_message({"type": "authorize", "ticket": 42})

        assert caught.value.message_type == "authorize"
        assert "ticket" in str(caught.value)


class TestOutboundBuilders:
    """Agent-visualization builders (agentCreated, agentToolStart, etc.) moved
    to `pixelagents.contracts.outbound` -- see
    `pixelagents/tests/test_contracts_outbound.py` for their tests."""

    @pytest.mark.parametrize(
        ("builder", "expected"),
        [
            (
                lambda: layout_loaded(valid_layout()),
                {"type": "layoutLoaded", "layout": valid_layout()},
            ),
        ],
    )
    def test_builder_matches_wire_shape(
        self,
        builder: Callable[[], object],
        expected: dict[str, object],
    ) -> None:
        assert builder() == expected
