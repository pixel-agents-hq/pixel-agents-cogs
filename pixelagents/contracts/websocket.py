"""Pydantic contracts for messages accepted from an office WebSocket."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, TypeAlias, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .layout import OfficeLayout

_INGRESS_CONFIG = ConfigDict(extra="allow", strict=True)


class ClientMessageEnvelope(BaseModel):
    """The common field needed to dispatch an inbound client message."""

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
    """Client-controlled seat fields; range checks mirror persisted values."""

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
    """Protected upstream action; currently a server-side no-op."""

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
    """An inbound envelope or known message failed structural validation."""

    def __init__(self, message_type: str | None, details: tuple[str, ...]) -> None:
        self.message_type = message_type
        self.details = details
        label = message_type if message_type is not None else "<missing>"
        super().__init__(f"invalid {label} client message: {'; '.join(details)}")


def _validation_details(error: ValidationError) -> tuple[str, ...]:
    return tuple(
        f"{'.'.join(str(part) for part in issue['loc'])}: {issue['msg']}"
        for issue in error.errors()
    )


def _message_type(payload: object) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    value = payload.get("type")
    return value if isinstance(value, str) else None


def parse_client_message(payload: object) -> ClientMessage | None:
    """Parse a supported message, returning ``None`` for future message types.

    Unknown types are intentionally ignored for forward compatibility.  A
    malformed envelope or a malformed known message raises a typed error that
    the transport can log and ignore without closing the socket.
    """

    message_type = _message_type(payload)
    try:
        envelope = ClientMessageEnvelope.model_validate(payload)
    except ValidationError as exc:
        raise InvalidClientMessageError(message_type, _validation_details(exc)) from exc

    model = _MESSAGE_MODELS.get(envelope.type)
    if model is None:
        return None
    try:
        parsed = model.model_validate(payload)
    except ValidationError as exc:
        raise InvalidClientMessageError(envelope.type, _validation_details(exc)) from exc
    return cast(ClientMessage, parsed)
