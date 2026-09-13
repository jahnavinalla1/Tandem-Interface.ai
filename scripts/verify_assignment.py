"""Keyless verification of a saved artifact on the local synthetic portal.

The operator in the handoff scenario is explicitly scripted, exercising the real
lease transfer protocol. Use --manual-handoff to perform sign-off yourself instead.
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import sync_playwright

from tandem.domain.capability import load_capability_from_yaml
from tandem.domain.outcomes import OutcomeCode
from tandem.domain.result_contract import to_result
from tandem.handoff.coordinator import HandoffCoordinator
from tandem.ledger.database import get_engine, get_session_factory, init_db
from tandem.ledger.repository import LedgerRepository
from tandem.policy.telemetry import llm_tracker
from tandem.replay.executor import DeterministicExecutor
from tandem.security.evidence import sanitize, screenshot
from tandem.support.simulator_control import ensure_simulators_running, set_core_bank_mode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact', default='capabilities/compiled/demo_post_provisional_credit.yaml')
    parser.add_argument('--output', default='evidence/verification')
    parser.add_argument('--manual-handoff', action='store_true')
    args = parser.parse_args()
    cap = load_capability_from_yaml(args.artifact)
    folder = Path(args.output) / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    folder.mkdir(parents=True, exist_ok=False)
    ensure_simulators_running()
    llm_tracker.reset()
    records = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.manual_handoff)
        page = browser.new_context().new_page()
        # Enforce no target API calls at browser transport, including fetch/XHR.
        page.context.route('**/api/**', lambda route: route.abort())
        inputs = dict(institution_id='alpha', member_id='8830142', account_id='CHK-8830142-01',
                      case_id='VERIFY-' + uuid4().hex[:12], amount=150.0, currency='USD')
        executor = DeterministicExecutor(page, ui_checks=True)

        def save(name, outcome):
            assert llm_tracker.call_count == 0
            outcome.evidence = name + '.png'
            record = dict(scenario=name, artifact_hash=cap.artifact_hash,
                          inputs=inputs.copy(), llm_calls=0, outcome=outcome.model_dump(mode='json'),
                          result=to_result(outcome).model_dump(mode='json'))
            (folder / (name + '.json')).write_text(json.dumps(sanitize(record), indent=2))
            (folder / (name + '.png')).write_bytes(screenshot(page))
            records.append(record)
            print(name + ': ' + outcome.code.value, flush=True)

        try:
            result = executor.execute(cap, inputs)
            save('success', result)
            assert result.code == OutcomeCode.COMPLETED
            result = executor.execute(cap, inputs)
            save('duplicate', result)
            assert result.code == OutcomeCode.ALREADY_APPLIED
            inputs['amount'] = 501
            result = executor.execute(cap, inputs)
            save('policy-denied', result)
            assert result.code == OutcomeCode.POLICY_DENIED
            inputs['amount'] = 150
            inputs['case_id'] = 'HANDOFF-' + uuid4().hex[:12]
            set_core_bank_mode(require_compliance_interstitial=True)
            result = executor.execute(cap, inputs)
            save('intervention', result)
            assert result.code == OutcomeCode.COMPLIANCE_INTERSTITIAL and result.requires_human
            engine = get_engine(str(folder / 'handoff.db'))
            init_db(engine)
            with get_session_factory(engine)() as db:
                repo = LedgerRepository(db)
                repo.create_or_get_case(inputs['case_id'], inputs['member_id'], inputs['amount'])
                initial = repo.acquire_lease(inputs['case_id'], 'automation', owner_type='AUTOMATION')
                old_token = initial.fencing_token
                db.commit()
                coordinator = HandoffCoordinator(db)
                coordinator.initiate_handoff(inputs['case_id'], result.code.value, result.model_dump(mode='json'))
                lease = coordinator.claim_operator_lease(inputs['case_id'], 'demo-operator')
                assert not repo.validate_lease_token(inputs['case_id'], 'automation', old_token)
                page_id = id(page)
                if args.manual_handoff:
                    input('Review and sign off in the open browser, then press Enter to hand control back: ')
                    repo.record_event(inputs['case_id'], 'MANUAL_CONTROL_RETURNED', 'handoff',
                                      actor='HUMAN', payload={'operator': 'demo-operator'})
                    repo.release_lease(inputs['case_id'], 'demo-operator', lease.fencing_token)
                    db.commit()
                else:
                    coordinator.operator_clear_compliance(inputs['case_id'], 'demo-operator', lease.fencing_token, page)
                resumed = repo.acquire_lease(inputs['case_id'], 'automation-resumed', owner_type='AUTOMATION')
                db.commit()
                executor.lease_validator = lambda: repo.validate_lease_token(
                    inputs['case_id'], 'automation-resumed', resumed.fencing_token)
                result = executor.execute(cap, inputs)
                save('handoff-resumed', result)
                assert id(page) == page_id and result.code == OutcomeCode.COMPLETED
                (folder / 'handoff.json').write_text(json.dumps(dict(
                    same_page=True, old_automation_fenced=True,
                    operator='manual' if args.manual_handoff else 'scripted demonstration',
                    states=['AUTOMATION', 'NEEDS_HUMAN', 'HUMAN', 'AUTOMATION'],
                    fencing_tokens=[old_token, lease.fencing_token, resumed.fencing_token],
                ), indent=2))
        finally:
            set_core_bank_mode(require_compliance_interstitial=False)
            browser.close()
    (folder / 'manifest.json').write_text(json.dumps(dict(
        artifact_hash=cap.artifact_hash, source_discovery_run_id=cap.source_discovery_run_id,
        scenarios=[r['scenario'] for r in records], llm_calls=llm_tracker.call_count,
        target_verification='UI_ONLY', data='SYNTHETIC_ONLY',
    ), indent=2))
    print('Verified evidence: ' + str(folder))


if __name__ == '__main__':
    main()
