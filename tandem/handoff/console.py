"""Minimal headed-browser operator handoff retaining the same live Page."""

import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import Page

from tandem.handoff.coordinator import HandoffCoordinator
from tandem.ledger.database import get_engine, get_session_factory, init_db
from tandem.ledger.repository import LedgerRepository
from tandem.security.evidence import sanitize, screenshot


class ConsoleHandoff:
    """Automation is synchronously paused while the human holds a fenced lease."""

    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def __call__(self, page: Page, request: dict) -> bool:
        case_id = 'DISCOVERY-HANDOFF-' + uuid4().hex[:12]
        engine = get_engine(str(self.root / 'operator.db'))
        init_db(engine)
        request_path = self.root / (case_id + '.json')
        request_path.write_text(json.dumps(sanitize(request), indent=2))
        (self.root / (case_id + '.png')).write_bytes(screenshot(page))
        with get_session_factory(engine)() as db:
            repo = LedgerRepository(db)
            repo.create_or_get_case(case_id, 'SYNTHETIC', Decimal('0.00'))
            auto = repo.acquire_lease(case_id, 'discovery', owner_type='AUTOMATION')
            old = auto.fencing_token
            db.commit()
            coordinator = HandoffCoordinator(db)
            coordinator.initiate_handoff(case_id, request['reason'], request)
            human = coordinator.claim_operator_lease(case_id, 'console-operator')
            assert not repo.validate_lease_token(case_id, 'discovery', old)
            print('Automation paused. Review the same open browser. Request: ' + str(request_path))
            answer = input('Perform the manual steps. Type resume to return control, or stop: ').strip()
            notes = input('Describe the actions you performed: ').strip()
            repo.record_event(case_id, 'OPERATOR_ACTIONS_RECORDED', 'discovery', actor='HUMAN',
                              payload={'notes': notes, 'decision': answer})
            repo.release_lease(case_id, 'console-operator', human.fencing_token)
            resumed = repo.acquire_lease(case_id, 'discovery-resumed', owner_type='AUTOMATION')
            db.commit()
            request.update(operator_notes=notes, resumed=answer == 'resume',
                           fencing_tokens=[old, human.fencing_token, resumed.fencing_token])
            request_path.write_text(json.dumps(sanitize(request), indent=2))
            return answer == 'resume'
