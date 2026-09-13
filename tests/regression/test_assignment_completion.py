"""Contracts exposed by the assignment review."""

import pytest
from playwright.sync_api import sync_playwright

from tandem.domain.capability import StepAction, StepDefinition, load_capability_from_yaml
from tandem.domain.errors import PolicyViolationError
from tandem.domain.outcomes import ExecutionOutcome, OutcomeCategory, OutcomeCode
from tandem.policy.browser import authorize_control, authorize_url
from tandem.replay.executor import DeterministicExecutor
from tandem.surfaces.playwright_surface import PlaywrightSurface


def test_human_category_requests_intervention():
    assert ExecutionOutcome(category=OutcomeCategory.NEEDS_HUMAN,
                            code=OutcomeCode.COMPLIANCE_INTERSTITIAL, message='Review').requires_human


def test_policy_blocks_external_origin_and_admin_routes():
    for url in ['https://example.com/', 'http://127.0.0.1:8001/api/reset']:
        with pytest.raises(PolicyViolationError):
            authorize_url(url)
    authorize_url('http://127.0.0.1:8001')


def test_unsupported_steps_rejected_before_browser_action():
    cap = load_capability_from_yaml('capabilities/core/post_provisional_credit.yaml')
    cap.steps.insert(0, StepDefinition(step_id='read', action=StepAction.READ_TEXT, semantic_target='read'))
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        result = DeterministicExecutor(page).execute(cap, {})
        assert result.code == OutcomeCode.POLICY_VIOLATION
        assert page.url == 'about:blank'
        browser.close()


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
    from tandem.security.evidence import sanitize
    result = sanitize({'body': 'Contact alice@example.com; SSN 123-45-6789', 'password': 'private'})
    assert result['password'] == '[REDACTED]'
    assert 'alice@example.com' not in result['body']
    assert '123-45-6789' not in result['body']


def test_caller_result_is_discriminated():
    from pydantic import TypeAdapter

    from tandem.domain.result_contract import Result, to_result
    outcome = ExecutionOutcome(category=OutcomeCategory.NEEDS_HUMAN,
                               code=OutcomeCode.COMPLIANCE_INTERSTITIAL, message='Review')
    result = to_result(outcome)
    assert TypeAdapter(Result).validate_json(result.model_dump_json()).kind == 'failure'
    assert result.requires_human
