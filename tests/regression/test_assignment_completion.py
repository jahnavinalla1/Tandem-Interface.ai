"""Contracts exposed by the assignment review."""

import pytest
from playwright.sync_api import sync_playwright
from pydantic import ValidationError

from tandem.domain.capability import StepDefinition
from tandem.domain.errors import PolicyViolationError
from tandem.domain.outcomes import ExecutionOutcome, OutcomeCategory, OutcomeCode
from tandem.policy.browser import authorize_control, authorize_url
from tandem.surfaces.playwright_surface import PlaywrightSurface


def test_human_category_requests_intervention():
    assert ExecutionOutcome(category=OutcomeCategory.NEEDS_HUMAN,
                            code=OutcomeCode.COMPLIANCE_INTERSTITIAL, message='Review').requires_human


def test_policy_blocks_external_origin_and_admin_routes():
    for url in ['https://example.com/', 'http://127.0.0.1:8001/api/reset']:
        with pytest.raises(PolicyViolationError):
            authorize_url(url)
    authorize_url('http://127.0.0.1:8001')


def test_unimplemented_actions_are_not_exposed_by_artifact_schema():
    for action in ('READ_TEXT', 'WAIT_FOR', 'SELECT_FRAME'):
        with pytest.raises(ValidationError):
            StepDefinition(step_id='unsupported', action=action, semantic_target='unsupported')


def test_ambiguous_locator_and_misclassified_commit_blocked():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content('<button>Same</button><button>Same</button>')
        with pytest.raises(PolicyViolationError, match='Ambiguous'):
            PlaywrightSurface(page)._find_best_locator(page, ['button'], 'Same')
        page.set_content('<form method="post" action="http://127.0.0.1:8001/workspace/credit/commit"><button>Commit</button></form>')
        with pytest.raises(PolicyViolationError, match='guarded SUBMIT'):
            authorize_control(page.locator('button'), 'CLICK')
        browser.close()


def test_common_pii_is_redacted_at_boundary():
    from tandem.security.evidence import sanitize, sanitize_evidence

    result = sanitize({'body': 'Contact alice@example.com; SSN 123-45-6789', 'password': 'private'})
    assert result['password'] == '[REDACTED]'
    assert 'alice@example.com' not in result['body']
    assert '123-45-6789' not in result['body']

    evidence = sanitize_evidence(
        {
            'member_id': '8830142',
            'account_id': 'CHK-8830142-01',
            'body': 'Member 8830142 uses CHK-8830142-01',
        },
        identifiers=['8830142', 'CHK-8830142-01'],
    )
    assert evidence['member_id'].endswith('0142')
    assert evidence['account_id'].endswith('2-01')
    assert '8830142' not in evidence['body']
    assert 'CHK-8830142-01' not in evidence['body']


def test_caller_result_is_discriminated():
    from pydantic import TypeAdapter

    from tandem.domain.result_contract import Result, to_result
    outcome = ExecutionOutcome(category=OutcomeCategory.NEEDS_HUMAN,
                               code=OutcomeCode.COMPLIANCE_INTERSTITIAL, message='Review')
    result = to_result(outcome)
    assert TypeAdapter(Result).validate_json(result.model_dump_json()).kind == 'failure'
    assert result.requires_human
