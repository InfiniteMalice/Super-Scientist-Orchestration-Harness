from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType

from pydantic import BaseModel

from super_scientist.application.transactions.contracts import ProposalHandler

type RoutedHandler = ProposalHandler[BaseModel, BaseModel]


class ProposalRouter:
    """Source-controlled proposal handler lookup with immutable registrations."""

    def __init__(
        self,
        handlers: Mapping[str, RoutedHandler] | Iterable[tuple[str, RoutedHandler]],
    ) -> None:
        items = handlers.items() if isinstance(handlers, Mapping) else handlers
        registered: dict[str, RoutedHandler] = {}
        for proposal_type, handler in items:
            if proposal_type in registered:
                raise ValueError(f"duplicate proposal handler registration: {proposal_type}")
            registered[proposal_type] = handler
        self._handlers: Mapping[str, RoutedHandler] = MappingProxyType(registered)

    def resolve(self, proposal_type: str) -> RoutedHandler:
        try:
            return self._handlers[proposal_type]
        except KeyError as error:
            raise ValueError(f"no proposal handler is registered for: {proposal_type}") from error
