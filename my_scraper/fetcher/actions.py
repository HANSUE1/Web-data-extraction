"""Steps taken on a page in the browser before it is read: click until, scroll until, fill, select, wait.

    {"click": "role=button[name='Load more']", "until": ".end-of-list", "times": 20}
    {"scroll": 10, "until": "text=No more results"}
    {"fill": "#q", "value": "laptop"}      {"select": "#sort", "value": "price"}
    {"press": "Enter"}                     {"wait": ".results"}

Targets are Playwright selectors: role=, text=, CSS or XPath. A read-only visitor's limits hold
whoever chose the steps: no password fields, no buttons that buy, send, sign up or delete, and no
navigation that posts a form, leaves the site, or goes where robots.txt does not allow.
"""
import re
from typing import Any, Awaitable, Callable, Dict, List
from urllib.parse import urlsplit

from my_scraper.utils.urls import is_action, site_key

KINDS = ("click", "scroll", "fill", "select", "press", "wait")
MAX_STEPS = 20
MAX_TIMES = 50
STEP_TIMEOUT = 5000  # milliseconds
MAX_CONTROLS = 80
KEYS = {"Enter", "Escape", "Tab", "ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight", "PageDown", "PageUp", "End", "Home"}

REFUSED_LABEL = re.compile(
    r"\b(buy|order|pay|checkout|check out|purchase|subscribe|sign ?up|register|create account|log ?in|sign ?in"
    r"|log ?out|delete|remove|unsubscribe|send|post|publish|comment|reply|donate|book now|reserve|upload)\b",
    re.IGNORECASE)

# What can be acted on, described the way a person would name it.
LIST_CONTROLS = """(max) => {
  const role = el => el.getAttribute('role') || ({A: 'link', BUTTON: 'button', SELECT: 'combobox',
    TEXTAREA: 'textbox', SUMMARY: 'button'})[el.tagName] || (el.tagName === 'INPUT'
      ? (['button', 'submit'].includes(el.type) ? 'button' : ['checkbox', 'radio'].includes(el.type) ? el.type : 'textbox')
      : 'generic');
  const label = el => (el.getAttribute('aria-label') || el.innerText || el.value || el.placeholder || el.name || '')
    .trim().replace(/\\s+/g, ' ').slice(0, 80);
  const found = [];
  for (const el of document.querySelectorAll(
      "a[href], button, select, textarea, summary, input:not([type=hidden]):not([type=password]), [role=button], [role=tab]")) {
    if (found.length >= max) break;
    const box = el.getBoundingClientRect();
    const name = label(el);
    if (!name || box.width === 0 || box.height === 0) continue;
    found.push({role: role(el), name, options: el.tagName === 'SELECT'
      ? [...el.options].slice(0, 30).map(o => o.value) : undefined});
  }
  return found;
}"""


class ActionError(Exception):
    """A step that cannot or may not be taken."""


def check(actions: Any) -> List[Dict[str, Any]]:
    """Validate steps before the browser starts."""
    if not isinstance(actions, list) or len(actions) > MAX_STEPS:
        raise ActionError(f"actions must be a list of at most {MAX_STEPS} steps")
    for step in actions:
        kinds = [kind for kind in KINDS if isinstance(step, dict) and kind in step]
        if len(kinds) != 1:
            raise ActionError(f"each step needs exactly one of {', '.join(KINDS)}: {step!r}")
        kind, target = kinds[0], step[kinds[0]]
        if kind == "scroll":
            if isinstance(target, bool) or not isinstance(target, int) or not 1 <= target <= MAX_TIMES:
                raise ActionError(f"scroll takes a number of screens from 1 to {MAX_TIMES}")
        elif not isinstance(target, str) or not target.strip():
            raise ActionError(f"{kind} needs a target: {step!r}")
        if kind == "press" and target not in KEYS:
            raise ActionError(f"press takes one of {', '.join(sorted(KEYS))}")
        if kind in ("fill", "select") and not isinstance(step.get("value"), str):
            raise ActionError(f"{kind} needs a value: {step!r}")
        times = step.get("times", 1)
        if isinstance(times, bool) or not isinstance(times, int) or not 1 <= times <= MAX_TIMES:
            raise ActionError(f"times must be from 1 to {MAX_TIMES}")
    return actions


async def guard_navigation(page, start_url: str, allowed: Callable[[str], Awaitable[bool]]) -> List[str]:
    """Stop page loads that the steps set off and that a polite visitor would not make.

    Returns the list that the refused addresses are added to.
    """
    home = site_key(urlsplit(start_url).netloc)
    refused: List[str] = []

    async def decide(route):
        request = route.request
        if request.is_navigation_request() and request.frame == page.main_frame:
            off_site = site_key(urlsplit(request.url).netloc) != home
            if request.method != "GET" or off_site or is_action(request.url) or not await allowed(request.url):
                refused.append(request.url)
                await route.fulfill(status=204)  # "no content" leaves the browser on the page it is on
                return
        await route.fallback()

    await page.route("**/*", decide)
    return refused


async def _visible(page, selector: str) -> bool:
    try:
        return await page.locator(selector).first.is_visible()
    except Exception:  # an unreadable selector, or a page between two documents
        return False


async def _refuse_risky(locator, kind: str):
    described = await locator.evaluate(
        "el => ({type: el.type || '', label: (el.innerText || el.value || el.getAttribute('aria-label') || '')})",
        timeout=STEP_TIMEOUT)
    if described["type"] == "password":
        raise ActionError("password fields are never filled or clicked")
    if kind == "click" and REFUSED_LABEL.search(described["label"][:100]):
        raise ActionError(f"not pressing {described['label'][:60]!r}: it may change something")


async def run(page, actions: List[Dict[str, Any]], settle: Callable[[], Awaitable[None]]) -> List[Dict[str, Any]]:
    """Take the steps in order. Returns what happened at each; a failed step ends the run."""
    log: List[Dict[str, Any]] = []
    for step in actions:
        kind = next(kind for kind in KINDS if kind in step)
        target, until = step[kind], step.get("until")
        done, error = 0, None
        times = target if kind == "scroll" else step.get("times", MAX_TIMES if until else 1)
        try:
            for _ in range(times):
                if until and await _visible(page, until):
                    break
                if kind == "scroll":
                    await page.evaluate("() => window.scrollBy(0, window.innerHeight)")
                elif kind == "press":
                    await page.keyboard.press(target)
                elif kind == "wait":
                    await page.locator(target).first.wait_for(timeout=STEP_TIMEOUT)
                else:
                    locator = page.locator(target).first
                    if kind == "click" and done and not await _visible(page, target):
                        break  # the button is gone: nothing more to load
                    await _refuse_risky(locator, kind)
                    if kind == "click":
                        await locator.click(timeout=STEP_TIMEOUT)
                    elif kind == "fill":
                        await locator.fill(step["value"], timeout=STEP_TIMEOUT)
                    else:
                        await locator.select_option(step["value"], timeout=STEP_TIMEOUT)
                done += 1
                await settle()
        except ActionError as e:
            error = str(e)
        except Exception as e:  # Playwright raises its own types, depending on what went wrong
            error = str(e).splitlines()[0] if str(e) else type(e).__name__
        log.append({"step": step, "times": done, **({"error": error} if error else {})})
        if error:
            break
    return log
