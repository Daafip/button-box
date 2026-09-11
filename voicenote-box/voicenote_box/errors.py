"""Errors that are safe to return to a caller or show in the ingress UI."""

from __future__ import annotations


class VoicenoteError(ValueError):
    """A validation or routing error with a message safe to display."""


class TransportError(RuntimeError):
    """A messaging service refused or failed to deliver a message.

    Raised at the network boundary. Callers treat it as retryable unless
    ``permanent`` is set, which marks a request the service will never accept
    (unknown recipient, revoked credentials, closed messaging window).
    """

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent
