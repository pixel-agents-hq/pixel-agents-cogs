"""Typed contracts at the boundaries of the Pixel Agents application."""

from .layout import JsonValue, OfficeLayout, RawOfficeLayout
from .pixel_index import (
    LayoutDetail,
    LayoutFiles,
    LayoutListResponse,
    LayoutSummary,
    PublicAuthor,
)
from .websocket import (
    AuthorizeMessage,
    ClientMessage,
    ClientMessageEnvelope,
    ImportLayoutMessage,
    InvalidClientMessageError,
    RequestDiagnosticsMessage,
    SaveAgentSeatsMessage,
    SaveLayoutMessage,
    SeatAssignmentPatch,
    WebviewReadyMessage,
    parse_client_message,
)

__all__ = [
    "AuthorizeMessage",
    "ClientMessage",
    "ClientMessageEnvelope",
    "ImportLayoutMessage",
    "InvalidClientMessageError",
    "JsonValue",
    "LayoutDetail",
    "LayoutFiles",
    "LayoutListResponse",
    "LayoutSummary",
    "OfficeLayout",
    "PublicAuthor",
    "RawOfficeLayout",
    "RequestDiagnosticsMessage",
    "SaveAgentSeatsMessage",
    "SaveLayoutMessage",
    "SeatAssignmentPatch",
    "WebviewReadyMessage",
    "parse_client_message",
]
