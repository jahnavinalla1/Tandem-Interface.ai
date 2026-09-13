"""Run from the repo root: python -m scripts.assignment_evidence --help."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import sync_playwright

from tandem.config import settings
from tandem.discovery.agent import DiscoveryAgent
from tandem.discovery.compiler import CapabilityCompiler
from tandem.discovery.factory import create_discovery_provider
from tandem.domain.capability import load_capability_from_yaml
from tandem.domain.outcomes import OutcomeCode
from tandem.policy.telemetry import llm_tracker
from tandem.replay.executor import DeterministicExecutor
from tandem.security.evidence import screenshot
from tandem.support.simulator_control import ensure_simulators_running, set_core_bank_mode


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--goal', required=True)
    parser.add_argument('--manual-handoff', action='store_true')
    parser.add_argument('--target', default=settings.core_bank_url)
    parser.add_argument('--output', type=Path, default=Path('evidence'))
    args = parser.parse_args()
    try:
        provider = create_discovery_provider(settings)
    except ValueError as exc:
        parser.error(str(exc))
    if args.target.rstrip('/') != settings.core_bank_url:
        parser.error('This evidence demo supports only the configured local core bank simulator')
    run_dir = args.output / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    run_dir.mkdir(parents=True, exist_ok=False)
    inputs = dict(institution_id='alpha', member_id='8830142',
                  account_id='CHK-8830142-01', case_id=f'D-DISC-{uuid4().hex[:12]}',
                  amount=150.0, currency='USD')
    ensure_simulators_running()
    llm_tracker.reset()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.manual_handoff)
        try:
            page = browser.new_context().new_page()
            from tandem.handoff.console import ConsoleHandoff
            handler = ConsoleHandoff(run_dir / 'operator') if args.manual_handoff else None
            trace = DiscoveryAgent(page, provider=provider, evidence_root=run_dir / 'discovery',
                                   intervention_handler=handler).discover(
                args.goal, args.target, inputs,
            )
            discovery_calls = llm_tracker.call_count
            if not trace.discovered_memo or not trace.money_moved or discovery_calls < 1:
                raise RuntimeError('Discovery did not demonstrate a real successful model-driven run')
            _, artifact = CapabilityCompiler(str(run_dir)).compile(trace, 'capability.yaml')
            cap = load_capability_from_yaml(str(artifact))
            results = []
            for scenario in ('replay', 'replay-interstitial'):
                inputs['case_id'] = f'D-REPLAY-{uuid4().hex[:12]}'
                llm_tracker.reset()
                page = browser.new_context().new_page()
                try:
                    if scenario == 'replay-interstitial':
                        set_core_bank_mode(require_compliance_interstitial=True)
                    outcome = DeterministicExecutor(page, ui_checks=True).execute(cap, inputs)
                    (run_dir / f'{scenario}.png').write_bytes(screenshot(page))
                    record = dict(scenario=scenario, artifact_hash=cap.artifact_hash,
                                  source_discovery_run_id=cap.source_discovery_run_id,
                                  inputs=inputs.copy(), llm_calls=llm_tracker.call_count,
                                  outcome=outcome.model_dump(mode='json'))
                    (run_dir / f'{scenario}.json').write_text(json.dumps(record, indent=2))
                    results.append(record)
                    if llm_tracker.call_count != 0:
                        raise RuntimeError('Replay called the model')
                    if scenario == 'replay' and outcome.code != OutcomeCode.COMPLETED:
                        raise RuntimeError(f'Replay failed: {outcome.code.value}')
                    if scenario == 'replay-interstitial' and outcome.is_success:
                        raise RuntimeError('Injected interstitial unexpectedly succeeded')
                finally:
                    if scenario == 'replay-interstitial':
                        set_core_bank_mode(require_compliance_interstitial=False)
                    page.close()
            (run_dir / 'manifest.json').write_text(json.dumps(dict(
                discovery_run_id=trace.run_id, discovery_llm_calls=discovery_calls,
                artifact_hash=cap.artifact_hash, replay_logs=[r['scenario'] + '.json' for r in results],
                data='Local simulator synthetic fixtures only',
            ), indent=2))
        finally:
            browser.close()
    print(f'Evidence saved to {run_dir}')


if __name__ == '__main__':
    main()
