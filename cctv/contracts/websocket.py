"""Validated messages accepted from either CCTV webview."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, TypeAlias, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pixelagents.contracts.layout import OfficeLayout

_INGRESS_CONFIG = ConfigDict(extra="allow", strict=True)


class ClientMessageEnvelope(BaseModel):
    model_config = _INGRESS_CONFIG
    type: str


class AuthorizeMessage(BaseModel):
    model_config = _INGRESS_CONFIG
    type: Literal["authorize"]
    ticket: str = Field(min_length=1)


class WebviewReadyMessage(BaseModel):
    model_config = _INGRESS_CONFIG
    type: Literal["webviewReady"]


class SaveLayoutMessage(BaseModel):
    model_config = _INGRESS_CONFIG
    type: Literal["saveLayout"]
    layout: OfficeLayout


class SeatAssignmentPatch(BaseModel):
    model_config = _INGRESS_CONFIG
    palette: int | None = Field(default=None, ge=0)
    hue_shift: int | None = Field(default=None, alias="hueShift", ge=0, le=360)
    seat_id: str | None = Field(default=None, alias="seatId")


class SaveAgentSeatsMessage(BaseModel):
    model_config = _INGRESS_CONFIG
    type: Literal["saveAgentSeats"]
    seats: dict[str, SeatAssignmentPatch]


class RequestDiagnosticsMessage(BaseModel):
    model_config = _INGRESS_CONFIG
    type: Literal["requestDiagnostics"]


class ImportLayoutMessage(BaseModel):
    model_config = _INGRESS_CONFIG
    type: Literal["importLayout"]


ClientMessage: TypeAlias = (
    AuthorizeMessage
    | WebviewReadyMessage
    | SaveLayoutMessage
    | SaveAgentSeatsMessage
    | RequestDiagnosticsMessage
    | ImportLayoutMessage
)

_MESSAGE_MODELS: dict[str, type[BaseModel]] = {
    "authorize": AuthorizeMessage,
    "webviewReady": WebviewReadyMessage,
    "saveLayout": SaveLayoutMessage,
    "saveAgentSeats": SaveAgentSeatsMessage,
    "requestDiagnostics": RequestDiagnosticsMessage,
    "importLayout": ImportLayoutMessage,
}


class InvalidClientMessageError(ValueError):
    def __init__(self, message_type: str | None, details: tuple[str, ...]) -> None:
        self.message_type = message_type
        self.details = details
        super().__init__(
            f"invalid {message_type or '<missing>'} client message: {'; '.join(details)}"
        )


def _details(error: ValidationError) -> tuple[str, ...]:
    return tuple(
        f"{'.'.join(str(part) for part in issue['loc'])}: {issue['msg']}"
        for issue in error.errors()
    )


def parse_client_message(payload: object) -> ClientMessage | None:
    message_type = payload.get("type") if isinstance(payload, Mapping) else None
    message_type = message_type if isinstance(message_type, str) else None
    try:
        envelope = ClientMessageEnvelope.model_validate(payload)
    except ValidationError as exc:
        raise InvalidClientMessageError(message_type, _details(exc)) from exc
    model = _MESSAGE_MODELS.get(envelope.type)
    if model is None:
        return None
    try:
        return cast(ClientMessage, model.model_validate(payload))
    except ValidationError as exc:
        raise InvalidClientMessageError(envelope.type, _details(exc)) from exc


__all__ = [
    "AuthorizeMessage",
    "ClientMessage",
    "ImportLayoutMessage",
    "InvalidClientMessageError",
    "RequestDiagnosticsMessage",
    "SaveAgentSeatsMessage",
    "SaveLayoutMessage",
    "SeatAssignmentPatch",
    "WebviewReadyMessage",
    "parse_client_message",
]
