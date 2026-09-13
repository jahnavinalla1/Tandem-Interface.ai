"""Discriminated caller-facing result union, independent of internal ledger records."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class Success(BaseModel):
    kind: Literal['success'] = 'success'
    outputs: dict[str, Any]


class BusinessOutcome(BaseModel):
    kind: Literal['business_outcome'] = 'business_outcome'
    code: str
    data: dict[str, Any]


class Failure(BaseModel):
    kind: Literal['failure'] = 'failure'
    classification: str
    code: str
    step: str | None
    expected: str
    observed: str
    evidence: str | None
    requires_human: bool


Result = Annotated[Success | BusinessOutcome | Failure, Field(discriminator='kind')]


def to_result(outcome) -> Success | BusinessOutcome | Failure:
    if outcome.is_success:
        return Success(outputs=outcome.outputs)
    if outcome.is_business_terminal:
        return BusinessOutcome(code=outcome.code.value, data=outcome.details)
    return Failure(classification=outcome.category.value, code=outcome.code.value,
                   step=outcome.failed_step, expected=outcome.expected or 'Declared capability checkpoint',
                   observed=outcome.observed or outcome.message, evidence=outcome.evidence,
                   requires_human=outcome.requires_human)
