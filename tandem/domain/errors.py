"""Exception hierarchy for Tandem effect and replay engine."""


class TandemError(Exception):
    """Base exception for all Tandem domain and execution errors."""


class SchemaValidationError(TandemError):
    """Raised when a capability definition violates schema or safety contracts."""


class PolicyViolationError(TandemError):
    """Raised when an operation violates business policy bounds (e.g. amount exceeds limit)."""


class EntityBindingMismatchError(TandemError):
    """Raised when container-scoped guard observes an unexpected entity/member ID."""


class AmountMismatchError(TandemError):
    """Raised when container-scoped guard observes an unexpected transaction or credit amount."""


class UncertainEffectError(TandemError):
    """Raised when an external mutation may have landed but confirmation was lost."""


class PrecheckError(TandemError):
    """Raised when precheck execution cannot determine current state."""


class PostcheckError(TandemError):
    """Raised when postcheck fails to resolve state following an interrupted action."""


class PageDriftError(TandemError):
    """Raised when surface locators cannot resolve target controls due to DOM changes."""


class SessionExpiredError(TandemError):
    """Raised when legacy system session has expired."""


class ComplianceInterstitialError(TandemError):
    """Raised when an interstitial compliance screen requires manual operator clearance."""


class LeaseConflictError(TandemError):
    """Raised when a lease is already held by another actor (AUTOMATION vs HUMAN)."""


class LedgerIntegrityError(TandemError):
    """Raised when immutable procedure history is incomplete or has been altered."""
