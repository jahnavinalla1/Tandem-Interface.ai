"""Shared browser action admission for the supported synthetic banking surface."""

from urllib.parse import urlsplit

from playwright.sync_api import Locator

from tandem.config import settings
from tandem.domain.errors import PolicyViolationError

READ_ROUTES = {'/', '/workspace/search', '/workspace/memos', '/workspace/credit/entry'}
POST_ROUTES = {'/workspace/credit/confirm', '/workspace/credit/commit'}


def authorize_url(url: str, *, method: str = 'GET', human: bool = False) -> None:
    parsed = urlsplit(url)
    allowed = {urlsplit(settings.core_bank_url).netloc, urlsplit(settings.core_bank_2_url).netloc}
    routes = READ_ROUTES if method == 'GET' else POST_ROUTES
    if human and method == 'POST':
        routes = routes | {'/workspace/credit/clear_compliance'}
    if parsed.scheme != 'http' or parsed.netloc not in allowed or (parsed.path or "/") not in routes:
        raise PolicyViolationError('Browser destination or method is not allowlisted')


def authorize_control(control: Locator, action: str, *, human: bool = False) -> None:
    """Inspect the actual destination, not the model's description of a control."""
    info = control.evaluate('''el => ({
        href: el.tagName === 'A' ? el.href : null,
        action: el.form ? (el.hasAttribute('formaction') ? el.formAction : el.form.action) : null,
        method: el.form ? (el.hasAttribute('formmethod') ? el.formMethod : el.form.method).toUpperCase() : null,
        submit: ['submit', 'image'].includes(el.type)
    })''')
    if action == 'FILL':
        if info['action']:
            authorize_url(info['action'], method=info['method'], human=human)
        return
    if info['href']:
        authorize_url(info['href'])
    elif info['submit'] and info['action']:
        authorize_url(info['action'], method=info['method'], human=human)
        if urlsplit(info['action']).path.endswith('/commit') and action != 'SUBMIT':
            raise PolicyViolationError('A commit control must use guarded SUBMIT, never CLICK')
    else:
        raise PolicyViolationError('Control has no declared allowlisted navigation or form action')


def install_navigation_policy(context) -> None:
    """Admit navigation requests exposed by Playwright routing.

    The transport allowlist includes manual compliance sign-off. Automated control
    admission still rejects that action unless the caller is explicitly human.
    """
    if getattr(context, '_tandem_navigation_policy', False):
        return

    def inspect(route):
        request = route.request
        if request.is_navigation_request():
            try:
                authorize_url(request.url, method=request.method, human=True)
            except PolicyViolationError:
                route.abort('blockedbyclient')
                return
        route.fallback()

    context.route('**/*', inspect)
    context._tandem_navigation_policy = True
