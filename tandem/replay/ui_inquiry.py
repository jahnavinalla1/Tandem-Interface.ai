"""Independent read-only memo inquiry through the banking UI, without target APIs."""

from typing import Any

from playwright.sync_api import Page

from tandem.domain.money import parse_money
from tandem.domain.outcomes import ExecutionOutcome, OutcomeCategory, OutcomeCode
from tandem.policy.browser import authorize_url
from tandem.surfaces.routing import core_bank_url_for


def inquire_credit(page: Page, inputs: dict[str, Any], *, before: bool) -> ExecutionOutcome:
    inquiry = page.context.new_page()
    try:
        url = core_bank_url_for(inputs.get('institution_id')) + '/workspace/memos'
        authorize_url(url)
        inquiry.goto(url, wait_until='domcontentloaded')
        inquiry.locator('input[name="case_id"]').fill(str(inputs['case_id']))
        inquiry.get_by_role('button', name='Search Core Memos').click()
        inquiry.locator('.memo-found-record, .memo-not-found').wait_for(timeout=5000)
        if inquiry.locator('.memo-not-found').count() == 1:
            return ExecutionOutcome(category=OutcomeCategory.SUCCESS if before else OutcomeCategory.BUSINESS_OUTCOME,
                                    code=OutcomeCode.NOT_APPLIED if before else OutcomeCode.CONFIRMED_NOT_APPLIED,
                                    message='UI inquiry confirmed effect absence')
        record = inquiry.locator('.memo-found-record')
        if record.count() != 1:
            raise ValueError('Ambiguous memo inquiry result')
        for field in ('case_id', 'member_id', 'account_id', 'institution_id', 'currency'):
            observed = record.locator('.found-' + field.replace('_', '-')).inner_text().strip()
            if observed != str(inputs[field]):
                raise ValueError(f'UI inquiry mismatch: {field}')
        amount = record.locator('.found-amount').inner_text().replace('$', '').replace('USD', '').replace(',', '').strip()
        if parse_money(amount) != parse_money(inputs['amount']):
            raise ValueError('UI inquiry amount mismatch')
        memo = record.locator('.found-memo-code').inner_text().strip()
        if not memo or record.locator('.found-status').inner_text().strip() != 'POSTED':
            raise ValueError('UI inquiry lacks posted receipt')
        return ExecutionOutcome(category=OutcomeCategory.BUSINESS_OUTCOME if before else OutcomeCategory.SUCCESS,
                                code=OutcomeCode.ALREADY_APPLIED if before else OutcomeCode.COMPLETED,
                                message='UI inquiry verified the posted credit identity', audit_ref=memo,
                                money_moved=not before, details={'verification': 'UI_ONLY'})
    except Exception as exc:
        return ExecutionOutcome(category=OutcomeCategory.HARD_FAILURE if before else OutcomeCategory.UNCERTAIN_EFFECT,
                                code=OutcomeCode.PRECHECK_UNAVAILABLE if before else OutcomeCode.POSTCHECK_UNCERTAIN,
                                message=f'UI inquiry could not establish effect state: {type(exc).__name__}')
    finally:
        inquiry.close()
