"""Surface abstractions decoupling business capabilities from DOM selectors."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional

from tandem.domain.money import Money


class LocatorKind(str, Enum):
    CSS = "CSS"
    TEXT = "TEXT"
    ROLE = "ROLE"
    XPATH = "XPATH"
    CONTAINER_RELATIVE = "CONTAINER_RELATIVE"


@dataclass
class ObservedControl:
    """Represents an actionable control inspected on the current surface."""

    name: str
    resolved_selector: str
    tag_name: str
    text_content: Optional[str] = None
    is_enabled: bool = True
    is_visible: bool = True


@dataclass
class ObservedRecord:
    """Logical record extracted from the form owned by a mutating control."""

    container_selector: str
    observed_institution_id: Optional[str] = None
    observed_member_id: Optional[str] = None
    observed_account_id: Optional[str] = None
    observed_amount: Optional[Money] = None
    observed_currency: Optional[str] = None
    observed_case_id: Optional[str] = None
    submission_values: Dict[str, List[str]] = field(default_factory=dict)
    submission_action: Optional[str] = None
    submission_method: Optional[str] = None
    raw_text: str = ""
    attributes: Dict[str, str] = field(default_factory=dict)


@dataclass
class SurfaceOverlay:
    """Per-institution surface overlay mapping semantic targets to institution-specific selectors."""

    institution_id: str
    name: str
    selector_overrides: Dict[str, List[str]] = field(default_factory=dict)
    container_overrides: Dict[str, str] = field(default_factory=dict)
    drift_warnings: List[str] = field(default_factory=list)

    def get_candidates(self, semantic_target: str, default_candidates: List[str]) -> List[str]:
        """Return overlay candidates if present, falling back to base candidates."""
        if semantic_target in self.selector_overrides:
            return self.selector_overrides[semantic_target]
        return default_candidates


class Surface(ABC):
    """Abstract surface interface separating replay logic from underlying automation driver."""

    @abstractmethod
    def navigate(self, url: str) -> None:
        pass

    @abstractmethod
    def resolve_and_click(
        self,
        semantic_target: str,
        candidates: List[str],
        frame_selector: Optional[str] = None,
        overlay: Optional[SurfaceOverlay] = None,
        before_click: Optional[Callable[[], None]] = None,
    ) -> ObservedControl:
        pass

    @abstractmethod
    def resolve_and_fill(
        self,
        semantic_target: str,
        candidates: List[str],
        value: str,
        frame_selector: Optional[str] = None,
        overlay: Optional[SurfaceOverlay] = None,
    ) -> ObservedControl:
        pass

    @abstractmethod
    def observe_container(
        self,
        container_selector: str,
        frame_selector: Optional[str] = None,
        overlay: Optional[SurfaceOverlay] = None,
        control_candidates: Optional[List[str]] = None,
        semantic_target: str = "Mutating submit control",
    ) -> ObservedRecord:
        pass
