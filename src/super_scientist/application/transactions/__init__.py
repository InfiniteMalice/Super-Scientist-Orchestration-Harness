"""Fixed transaction coordination for durable proposal submission."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from super_scientist.application.transactions.coordinator import TransactionCoordinator
    from super_scientist.application.transactions.router import ProposalRouter

__all__ = ["ProposalRouter", "TransactionCoordinator"]


def __getattr__(name: str) -> Any:
    if name == "TransactionCoordinator":
        from super_scientist.application.transactions.coordinator import TransactionCoordinator

        return TransactionCoordinator
    if name == "ProposalRouter":
        from super_scientist.application.transactions.router import ProposalRouter

        return ProposalRouter
    raise AttributeError(name)
