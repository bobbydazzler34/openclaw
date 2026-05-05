"""Log a Stessa transaction from a natural-language instruction (Telegram)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from functools import partial
from pathlib import Path
from typing import Any

import yaml

from openclaw.llm.gemini import api_key_from_env, generate_text

LOGGER = logging.getLogger(__name__)

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PACKAGE_DIR / "config.yaml"

LOGIN_URL = "https://app.stessa.com/login"

CATEGORIES: dict[str, dict[str, Any]] = {
    "rental_income": {
        "label": "Rental Income",
        "subcategories": {
            "rents": "Rents",
        },
    },
    "management_fees": {
        "label": "Management Fees",
        "subcategories": {
            "property_management": "Property Management",
        },
    },
    "taxes": {
        "label": "Taxes",
        "subcategories": {
            "city_state_local": "City, State, & Local Taxes",
        },
    },
    "repairs_maintenance": {
        "label": "Repairs & Maintenance",
        "subcategories": {
            "appliance_repairs": "Appliance Repairs",
        },
    },
    "utilities": {
        "label": "Utilities",
        "subcategories": {
            "water_sewer": "Water & Sewer",
        },
    },
    "mortgages_loans": {
        "label": "Mortgages & Loans",
        "subcategories": {
            "mortgage_payment": "Mortgage Payment",
            "interest": "Interest",
            "principal": "Principal",
        },
    },
}

DEFAULT_GEMINI_KEY_ENV = "GOOGLE_API_KEY"
DEFAULT_LLM_MODEL = "gemini-2.5-flash"


@dataclass(slots=True)
class ParsedTransaction:
    """Structured transaction from the LLM."""

    amount: float
    date: date
    category_key: str
    subcategory_key: str
    property_alias: str
    transaction_name: str | None
    notes: str | None

    @property
    def category_label(self) -> str:
        return str(CATEGORIES[self.category_key]["label"])

    @property
    def subcategory_label(self) -> str:
        subs = CATEGORIES[self.category_key]["subcategories"]
        return str(subs[self.subcategory_key])


def load_skill_config(config_path: Path | str | None = None) -> dict[str, Any]:
    """Load YAML config for this skill."""
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        msg = f"Expected mapping in config: {path}"
        raise ValueError(msg)
    return data


def strip_json_fence(text: str) -> str:
    """Remove optional markdown code fences from model output."""
    t = text.strip()
    if not t.startswith("```"):
        return t
    lines = t.split("\n")
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def build_parse_system_prompt() -> str:
    """System prompt: JSON only with schema and valid category keys."""
    cat_lines = []
    for cat_key, meta in CATEGORIES.items():
        subs = meta["subcategories"]
        sub_pairs = ", ".join(f'"{k}"' for k in subs)
        cat_lines.append(f'  - category "{cat_key}" with subcategory one of: {sub_pairs}')
    categories_block = "\n".join(cat_lines)
    year = datetime.now().year
    return f"""You extract real-estate transaction fields for Stessa from a short user message.
Return ONLY valid JSON (no markdown, no code fences, no backticks, no commentary).
Use this exact shape:
{{
  "amount": <float, always positive>,
  "date": "YYYY-MM-DD",
  "category": "<category_key>",
  "subcategory": "<subcategory_key>",
  "property_alias": "<string>",
  "name": "<transaction name string or null>",
  "notes": "<optional string or null>"
}}

Rules:
- If the year is not mentioned, assume calendar year {year}.
- If any required field cannot be determined from the message, set it to null (JSON null).
- amount must be positive when present.

Valid category_key and subcategory_key pairs (use exactly these keys):
{categories_block}
"""


def parse_instruction_llm(instruction: str, config: dict[str, Any]) -> dict[str, Any]:
    """Call Gemini and return parsed JSON as a dict."""
    env_name = str(config.get("gemini_api_key_env", DEFAULT_GEMINI_KEY_ENV))
    api_key = api_key_from_env(env_name)
    if not api_key:
        msg = f"Set environment variable {env_name} for Gemini."
        raise ValueError(msg)

    model = str(config.get("llm_model", DEFAULT_LLM_MODEL))
    system = build_parse_system_prompt()
    raw = generate_text(system, instruction, model=model, api_key=api_key)
    cleaned = strip_json_fence(raw)
    return json.loads(cleaned)


def validate_parsed(data: dict[str, Any]) -> str | None:
    """Return an error string if required fields are missing or invalid; else None."""
    missing: list[str] = []

    amount = data.get("amount")
    if amount is None:
        missing.append("amount")
    else:
        try:
            if float(amount) <= 0:
                return "❌ amount must be a positive number."
        except (TypeError, ValueError):
            return "❌ amount could not be read as a positive number."

    if data.get("date") in (None, ""):
        missing.append("date")
    else:
        try:
            datetime.strptime(str(data["date"]), "%Y-%m-%d")
        except ValueError:
            return "❌ date must be YYYY-MM-DD."

    for key in ("category", "subcategory", "property_alias"):
        if data.get(key) in (None, ""):
            missing.append(key)

    if missing:
        return (
            "❌ Could not determine from your message: "
            + ", ".join(missing)
            + ". Please clarify (amount, date, category, subcategory, property)."
        )

    cat = data["category"]
    sub = data["subcategory"]
    if cat not in CATEGORIES:
        return f"❌ Invalid category key: {cat!r}. Use one of the configured category keys."
    if sub not in CATEGORIES[cat]["subcategories"]:
        return f"❌ Invalid subcategory for {cat!r}: {sub!r}."

    return None


def to_parsed_transaction(data: dict[str, Any]) -> ParsedTransaction:
    """Build ParsedTransaction after validation."""
    d = datetime.strptime(str(data["date"]), "%Y-%m-%d").date()
    notes_val = data.get("notes")
    notes: str | None = None
    if notes_val is not None and str(notes_val).strip():
        notes = str(notes_val).strip()
    name_val = data.get("name")
    parsed_name: str | None = None
    if name_val is not None and str(name_val).strip():
        parsed_name = str(name_val).strip()
    return ParsedTransaction(
        amount=float(data["amount"]),
        date=d,
        category_key=str(data["category"]),
        subcategory_key=str(data["subcategory"]),
        property_alias=str(data["property_alias"]).strip(),
        transaction_name=parsed_name,
        notes=notes,
    )


def error_timestamp_path() -> Path:
    """Screenshot path with timestamp."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(f"/tmp/stessa_error_{ts}.png")


def format_success_message(parsed: ParsedTransaction) -> str:
    amt = f"${parsed.amount:,.2f}"
    prop = parsed.property_alias
    ds = parsed.date.isoformat()
    payer_payee = parsed.transaction_name or "N/A"
    return (
        f"✅ Transaction logged: {amt} · {parsed.category_label} / "
        f"{parsed.subcategory_label} · {prop} · {ds} · Payer/Payee: {payer_payee}"
    )


def _resolved_transaction_name(parsed: ParsedTransaction, config: dict[str, Any]) -> str:
    """Resolve transaction name; ignore category-like names mistakenly parsed from instruction."""
    default_name = str(config.get("transaction_name", "Metropole Properties")).strip()
    if not parsed.transaction_name:
        return default_name
    candidate = parsed.transaction_name.strip()
    low = candidate.lower()
    # Reject generic/category-like names from LLM such as "Rent Payment".
    category_like_exact = {
        "rent",
        "rents",
        "rental income",
        "income",
        parsed.category_label.lower(),
        parsed.subcategory_label.lower(),
    }
    category_like_substrings = (
        "rent",
        "income",
        "payment",
        "mortgage",
        "utility",
        "repair",
    )
    matched_tokens = [token for token in category_like_substrings if token in low]
    if low in category_like_exact or bool(matched_tokens):
        _debug_log(
            "H18",
            "stessa_add_transaction.py:_resolved_transaction_name",
            "name_overridden_to_default",
            {"parsed_name": candidate, "default_name": default_name},
        )
        return default_name
    return candidate


def _extract_payee_from_instruction(instruction: str) -> str | None:
    """Extract payer/payee from common phrasing: '... to X for property ...'."""
    m = re.search(r"\bto\s+(.+?)\s+for\s+property\b", instruction, re.I)
    if not m:
        return None
    candidate = m.group(1).strip(" .,:;")
    return candidate or None


def _norm_amount_snippets(amount: float) -> set[str]:
    """Strings that might appear in the UI for this amount."""
    s = set()
    s.add(f"{amount:.2f}")
    s.add(f"{amount:,.2f}")
    plain = f"{amount:g}"
    s.add(plain)
    return s


def _debug_log(hypothesis_id: str, location: str, message: str, data: dict[str, Any]) -> None:
    """Debug hook intentionally left as no-op."""
    _ = (hypothesis_id, location, message, data)


def _coerce_bool(value: Any, default: bool) -> bool:
    """Coerce config flags to bool."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _login_frames(page: Any) -> list[Any]:
    """Main frame first, then embedded frames (Auth0 / Roofstock often uses iframes)."""
    return [page.main_frame] + [f for f in page.frames if f != page.main_frame]


def _react_safe_fill(locator: Any, text: str) -> None:
    """Fill inputs that use React/controlled components; verify value landed."""
    locator.click(timeout=8_000)
    locator.fill("")
    locator.fill(text)
    try:
        got = locator.input_value(timeout=3_000)
    except Exception:
        got = ""
    if text.strip() != got.strip():
        locator.click(timeout=3_000)
        locator.press_sequentially(text, delay=35)


def _is_fillable_input(locator: Any) -> bool:
    """Return True when locator points to an editable text/date/number input."""
    try:
        input_type = (locator.get_attribute("type") or "").strip().lower()
        if input_type in {"button", "submit", "reset", "checkbox", "radio", "file"}:
            return False
        return bool(locator.is_visible() and locator.is_enabled() and locator.is_editable())
    except Exception:
        return False


def _press_enter_for_payee_commit(page: Any, field: Any, expected_value: str, strategy: str) -> None:
    """Press Tab to leave payee field and commit selection."""
    try:
        field.focus(timeout=2_000)
    except Exception:
        pass
    try:
        field.press("Tab")
    except Exception:
        return
    _ = (page, expected_value, strategy)


def _fill_payee_field(field: Any, expected_value: str, strategy: str) -> bool:
    """Fill payee/name with React-safe strategy and verify landed value."""
    _react_safe_fill(field, expected_value)
    landed_value = ""
    try:
        landed_value = field.input_value(timeout=1_500)
    except Exception:
        landed_value = ""
    _ = strategy
    return landed_value.strip() == expected_value.strip()


def _try_fill_email_in_frame(frame: Any, username: str) -> bool:
    """Find the visible email/username field Roofstock/Auth0 actually validates."""
    # TODO: update if Auth0 / Roofstock markup changes
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    # Placeholder text (common on Auth0 universal login / Roofstock)
    for placeholder_rx in (
        r"email\s*address",
        r"\be\s*-?\s*mail\b",
        r"enter\s+your\s+email",
        r"you@",
        r"yahoo|gmail|outlook|hotmail",
    ):
        try:
            ph = frame.get_by_placeholder(re.compile(placeholder_rx, re.I)).first
            ph.wait_for(state="visible", timeout=2_000)
            _react_safe_fill(ph, str(username))
            return True
        except Exception:
            continue

    # Accessible label
    try:
        lab = frame.get_by_label(re.compile(r"email|e-mail|username", re.I)).first
        lab.wait_for(state="visible", timeout=2_000)
        _react_safe_fill(lab, str(username))
        return True
    except Exception:
        pass

    # Visible CSS order matters: avoid .first on a long OR chain picking a hidden node
    visible_selectors = (
        'input[name="username"]:visible',
        'input#username:visible',
        'input[type="email"]:visible',
        'input[type="text"][autocomplete="username"]:visible',
        'input[autocomplete="username"]:visible',
        'input[autocomplete="email"]:visible',
        'input.auth0-lock-input:visible',
        'div[class*="auth"] input[type="text"]:visible',
    )
    for sel in visible_selectors:
        loc = frame.locator(sel).first
        try:
            loc.wait_for(state="visible", timeout=2_500)
            if not loc.is_enabled():
                continue
            _react_safe_fill(loc, str(username))
            return True
        except PlaywrightTimeoutError:
            continue
        except Exception:
            continue

    return False


def _try_fill_password_in_frame(frame: Any, password: str) -> bool:
    """Fill visible password field."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    password_sel = (
        'input[type="password"]:visible',
        'input#password:visible',
        'input[name="password"]:visible',
        'input[autocomplete="current-password"]:visible',
    )
    for sel in password_sel:
        loc = frame.locator(sel).first
        try:
            loc.wait_for(state="visible", timeout=2_500)
            _react_safe_fill(loc, str(password))
            return True
        except PlaywrightTimeoutError:
            continue
        except Exception:
            continue
    return False


def _stessa_roofstock_login(page: Any, username: str, password: str) -> None:
    """Complete Stessa login including redirect to Roofstock Auth0 (auth.roofstock.com).

    Stessa may redirect from app.stessa.com/login to hosted OAuth on auth.roofstock.com.
    Fields may live in the main document or an iframe; Auth0 may use a two-step flow.
    """
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    page.goto(LOGIN_URL, wait_until="domcontentloaded")

    # OAuth chain: stay tolerant until we're on Stessa app or Roofstock auth host
    try:
        page.wait_for_url(
            re.compile(r"https://(auth\.roofstock\.com|app\.stessa\.com)/"),
            timeout=90_000,
        )
    except PlaywrightTimeoutError:
        LOGGER.warning(
            "Did not match auth.roofstock.com or app.stessa.com URL within 90s; "
            "attempting field detection anyway.",
        )

    # Fill email/username (visible fields only — avoid hidden duplicates that fail validation)
    email_ok = False
    deadline = time.monotonic() + 65.0
    while time.monotonic() < deadline and not email_ok:
        for frame in _login_frames(page):
            if _try_fill_email_in_frame(frame, str(username)):
                email_ok = True
                break
        if not email_ok:
            time.sleep(0.35)

    if not email_ok:
        raise RuntimeError(
            "Could not find email/username field on Stessa or Roofstock (auth.roofstock.com) login.",
        )

    # Password may appear immediately or after "Continue" (Auth0 universal login)
    pwd_ok = False
    pwd_deadline = time.monotonic() + 55.0
    while time.monotonic() < pwd_deadline and not pwd_ok:
        for frame in _login_frames(page):
            if _try_fill_password_in_frame(frame, str(password)):
                pwd_ok = True
                break
        if pwd_ok:
            break
        # Advance multi-step Auth0 / Roofstock wizard
        clicked = False
        for frame in _login_frames(page):
            for pattern in (r"^continue$", r"^next$", r"continue with email", r"submit"):
                btn = frame.get_by_role("button", name=re.compile(pattern, re.I))
                if btn.count():
                    try:
                        btn.first.click(timeout=3_000)
                        clicked = True
                        break
                    except Exception:
                        continue
            if clicked:
                break
        time.sleep(0.45)

    if not pwd_ok:
        raise RuntimeError(
            "Could not find or fill password field (Auth0 / Roofstock flow may have changed).",
        )

    # Submit final login (prefer Sign in / Log in over Continue — Continue can submit empty step-1)
    submitted = False
    for pattern in (
        r"sign\s*in",
        r"log\s*in",
        r"verify",
        r"submit",
        r"^continue$",
    ):
        for frame in _login_frames(page):
            btn = frame.get_by_role("button", name=re.compile(pattern, re.I))
            if btn.count():
                try:
                    btn.first.click(timeout=8_000)
                    submitted = True
                    break
                except Exception:
                    continue
        if submitted:
            break

    if not submitted:
        for frame in _login_frames(page):
            sub = frame.locator('button[type="submit"], input[type="submit"]')
            if sub.count():
                sub.first.click(timeout=8_000)
                submitted = True
                break

    if not submitted:
        raise RuntimeError("Could not find login submit button on Roofstock/Stessa login.")


def _session_is_authenticated(page: Any) -> bool:
    """Best-effort check whether current browser context is already signed into Stessa."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    stage = "start"
    try:
        # TODO: refine dashboard selectors if Stessa changes app shell.
        _debug_log(
            "H6",
            "stessa_add_transaction.py:_session_is_authenticated",
            "auth_check_start",
            {"precheck_url": page.url},
        )
        stage = "goto_root"
        page.goto("https://app.stessa.com/", wait_until="domcontentloaded")
        stage = "wait_networkidle"
        try:
            page.wait_for_load_state("networkidle", timeout=5_000)
            _debug_log(
                "H11",
                "stessa_add_transaction.py:_session_is_authenticated",
                "networkidle_reached",
                {"url": page.url},
            )
        except PlaywrightTimeoutError:
            # Stessa keeps background network activity; networkidle may never settle.
            _debug_log(
                "H11",
                "stessa_add_transaction.py:_session_is_authenticated",
                "networkidle_timeout_ignored",
                {"url": page.url},
            )
        stage = "read_url"
        current = page.url.lower()
        stage = "count_link_hits"
        link_hits = page.get_by_role(
            "link",
            name=re.compile(r"transactions|portfolio|properties", re.I),
        ).count()
        stage = "count_text_hits"
        text_hits = page.get_by_text(re.compile(r"transactions|portfolio|properties", re.I)).count()
        _debug_log(
            "H6",
            "stessa_add_transaction.py:_session_is_authenticated",
            "auth_check_state",
            {
                "current_url": current,
                "link_hits": link_hits,
                "text_hits": text_hits,
                "contains_login": "/login" in current,
                "contains_auth_host": "auth.roofstock.com" in current,
            },
        )
        if "auth.roofstock.com" in current:
            return False
        if "/login" in current:
            return False
        # Positive hints for authenticated app views.
        if link_hits:
            return True
        if text_hits:
            return True
        return "app.stessa.com" in current
    except Exception as exc:
        _debug_log(
            "H6",
            "stessa_add_transaction.py:_session_is_authenticated",
            "auth_check_exception",
            {
                "precheck_url": page.url,
                "stage": stage,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        return False


def _navigate_to_transactions_surface(page: Any) -> bool:
    """Land on transactions page and stay there (no property-page hopping)."""
    # TODO: adjust route/nav labels if Stessa IA changes.
    _debug_log(
        "H9",
        "stessa_add_transaction.py:_navigate_to_transactions_surface",
        "start_transactions_navigation",
        {"current_url": page.url},
    )

    current = page.url.lower()
    if "/web3/transactions" in current:
        _debug_log(
            "H9",
            "stessa_add_transaction.py:_navigate_to_transactions_surface",
            "already_on_transactions",
            {"url": page.url},
        )
        return True

    # Single direct route attempt only; do not cascade to /web3 or dashboard.
    try:
        page.goto("https://app.stessa.com/web3/transactions", wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle", timeout=20_000)
        _debug_log(
            "H9",
            "stessa_add_transaction.py:_navigate_to_transactions_surface",
            "visited_route_candidate",
            {"url": page.url},
        )
        if "/web3/transactions" in page.url.lower():
            _debug_log(
                "H9",
                "stessa_add_transaction.py:_navigate_to_transactions_surface",
                "route_accepted",
                {"url": page.url},
            )
            return True
    except Exception:
        _debug_log(
            "H9",
            "stessa_add_transaction.py:_navigate_to_transactions_surface",
            "route_navigation_failed",
            {"route": "https://app.stessa.com/web3/transactions"},
        )
        # Runtime evidence: Stessa can land on /web3/transactions but still timeout on goto/wait.
        if "/web3/transactions" in page.url.lower():
            _debug_log(
                "H9",
                "stessa_add_transaction.py:_navigate_to_transactions_surface",
                "route_effectively_loaded_after_exception",
                {"url": page.url},
            )
            return True

    # Explicitly click Transactions if route isn't directly loaded.
    _debug_log(
        "H12",
        "stessa_add_transaction.py:_navigate_to_transactions_surface",
        "begin_transactions_nav_fallback_scan",
        {"url": page.url},
    )
    for role in ("link", "button", "tab"):
        nav = page.get_by_role(role, name=re.compile(r"transactions", re.I))
        nav_count = nav.count()
        _debug_log(
            "H12",
            "stessa_add_transaction.py:_navigate_to_transactions_surface",
            "transactions_nav_role_count",
            {"role": role, "count": nav_count},
        )
        if nav_count:
            try:
                nav.first.click(timeout=6_000)
                page.wait_for_load_state("networkidle", timeout=20_000)
                _debug_log(
                    "H9",
                    "stessa_add_transaction.py:_navigate_to_transactions_surface",
                    "clicked_nav_fallback",
                    {"role": role, "url": page.url},
                )
                if "/web3/transactions" in page.url.lower():
                    _debug_log(
                        "H9",
                        "stessa_add_transaction.py:_navigate_to_transactions_surface",
                        "route_accepted_after_nav_click",
                        {"url": page.url},
                    )
                    return True
                continue
            except Exception as exc:
                _debug_log(
                    "H12",
                    "stessa_add_transaction.py:_navigate_to_transactions_surface",
                    "transactions_nav_click_exception",
                    {"role": role, "error_type": type(exc).__name__, "error": str(exc)},
                )
                continue
    _debug_log(
        "H9",
        "stessa_add_transaction.py:_navigate_to_transactions_surface",
        "transactions_surface_unreachable",
        {"final_url": page.url},
    )
    return False


def _collect_property_candidates(page: Any) -> list[tuple[str, Any]]:
    """Collect probable property labels and clickable elements from scoped containers."""
    # TODO: tighten selectors once stable Stessa DOM signatures are confirmed.
    candidates: list[tuple[str, Any]] = []
    seen: set[str] = set()

    container_selectors = (
        '[data-testid*="property" i]',
        '[class*="property" i]',
        '[class*="portfolio" i]',
        'main section',
    )
    for container_sel in container_selectors:
        container = page.locator(container_sel)
        container_count = container.count()
        _debug_log(
            "H2",
            "stessa_add_transaction.py:_collect_property_candidates",
            "container_scan",
            {"selector": container_sel, "count": container_count},
        )
        if container_count == 0:
            continue
        links = container.locator("a")
        for i in range(min(links.count(), 200)):
            link = links.nth(i)
            try:
                text = link.inner_text(timeout=1200).strip()
            except Exception:
                continue
            if not text or len(text) > 140:
                continue
            low = text.lower()
            if low in seen:
                continue
            # Filter obvious global-nav/footer noise.
            if re.search(r"\b(manage|buy|sell|terms|privacy|new|track rental income)\b", low):
                continue
            if text.count("$") > 0 and len(text) < 6:
                continue
            seen.add(low)
            candidates.append((text, link))

    # Fallback if container scoping returned nothing useful.
    if not candidates:
        _debug_log(
            "H3",
            "stessa_add_transaction.py:_collect_property_candidates",
            "using_main_anchor_fallback",
            {"reason": "no_scoped_candidates"},
        )
        links = page.locator("main a")
        for i in range(min(links.count(), 200)):
            link = links.nth(i)
            try:
                text = link.inner_text(timeout=1200).strip()
            except Exception:
                continue
            if not text or len(text) > 140:
                continue
            low = text.lower()
            if low in seen:
                continue
            if re.search(r"\b(manage|buy|sell|terms|privacy|new)\b", low):
                continue
            seen.add(low)
            candidates.append((text, link))

    # Non-anchor fallback: many Stessa views render property cards as buttons/divs.
    if not candidates:
        _debug_log(
            "H5",
            "stessa_add_transaction.py:_collect_property_candidates",
            "using_non_anchor_fallback",
            {"reason": "no_anchor_candidates"},
        )
        non_anchor_selectors = (
            'main button:visible',
            '[role="button"]:visible',
            '[role="link"]:visible',
            '[role="listitem"]:visible',
            'main [class*="card" i]:visible',
            'main [class*="property" i]:visible',
        )
        for sel in non_anchor_selectors:
            nodes = page.locator(sel)
            count = min(nodes.count(), 200)
            _debug_log(
                "H5",
                "stessa_add_transaction.py:_collect_property_candidates",
                "non_anchor_scan",
                {"selector": sel, "count": count},
            )
            for i in range(count):
                node = nodes.nth(i)
                try:
                    text = node.inner_text(timeout=1200).strip()
                except Exception:
                    continue
                if not text or len(text) > 160:
                    continue
                # Favor address-like entries to avoid nav controls.
                if not re.search(r"\d+.*\b(st|street|ave|avenue|rd|road|dr|drive|ct|court|ln|lane|blvd)\b", text, re.I):
                    continue
                low = text.lower()
                if low in seen:
                    continue
                seen.add(low)
                candidates.append((text, node))

    _debug_log(
        "H2",
        "stessa_add_transaction.py:_collect_property_candidates",
        "candidate_collection_complete",
        {"candidate_count": len(candidates), "sample": [t for t, _ in candidates[:10]]},
    )
    return candidates


def _click_property_alias(page: Any, property_alias: str) -> tuple[bool, list[str]]:
    """Click property by alias from a scoped property listing."""
    alias_low = property_alias.lower().strip()
    candidates = _collect_property_candidates(page)
    _debug_log(
        "H4",
        "stessa_add_transaction.py:_click_property_alias",
        "alias_match_attempt",
        {"alias": property_alias, "candidate_count": len(candidates)},
    )
    if not candidates:
        return False, []

    names: list[str] = [text for text, _ in candidates]

    # Prefer whole-word-ish match, fallback to substring.
    for text, link in candidates:
        low = text.lower()
        if re.search(rf"(^|\W){re.escape(alias_low)}(\W|$)", low):
            _debug_log(
                "H4",
                "stessa_add_transaction.py:_click_property_alias",
                "whole_word_match",
                {"alias": property_alias, "matched": text},
            )
            link.click()
            return True, names
    for text, link in candidates:
        if alias_low in text.lower():
            _debug_log(
                "H4",
                "stessa_add_transaction.py:_click_property_alias",
                "substring_match",
                {"alias": property_alias, "matched": text},
            )
            link.click()
            return True, names
    # Last resort: direct text target in viewport (for virtualized lists/cards).
    direct = page.get_by_text(re.compile(re.escape(property_alias), re.I))
    if direct.count():
        try:
            direct.first.click(timeout=6_000)
            _debug_log(
                "H5",
                "stessa_add_transaction.py:_click_property_alias",
                "direct_text_click",
                {"alias": property_alias},
            )
            return True, names
        except Exception:
            pass
    _debug_log(
        "H4",
        "stessa_add_transaction.py:_click_property_alias",
        "no_match_found",
        {"alias": property_alias, "names_sample": names[:15]},
    )
    return False, names


def _select_property_in_form(page: Any, property_alias: str) -> bool:
    """Select property alias from transaction form controls when on global transactions page."""
    alias_low = property_alias.lower().strip()
    # TODO: update selectors if Stessa transaction form changes.
    # First try combobox/select labeled Property.
    for role in ("combobox", "button"):
        picker = page.get_by_role(role, name=re.compile(r"property", re.I))
        _debug_log(
            "H10",
            "stessa_add_transaction.py:_select_property_in_form",
            "picker_count",
            {"role": role, "count": picker.count()},
        )
        if picker.count():
            try:
                picker.first.click(timeout=5_000)
                option = page.get_by_role(
                    "option",
                    name=re.compile(re.escape(property_alias), re.I),
                )
                option_count = option.count()
                _debug_log(
                    "H10",
                    "stessa_add_transaction.py:_select_property_in_form",
                    "role_option_count",
                    {"role": role, "option_count": option_count, "alias": property_alias},
                )
                if option_count:
                    option.first.click(timeout=5_000)
                    _debug_log(
                        "H10",
                        "stessa_add_transaction.py:_select_property_in_form",
                        "selected_property_option",
                        {"via": role, "alias": property_alias},
                    )
                    return True
            except Exception:
                continue

    # Common Stessa control appears as "All properties"; open and pick text match.
    all_props = page.get_by_role("button", name=re.compile(r"all\s+properties", re.I))
    if all_props.count():
        try:
            all_props.first.click(timeout=5_000)
            text_option = page.get_by_text(re.compile(re.escape(property_alias), re.I))
            _debug_log(
                "H10",
                "stessa_add_transaction.py:_select_property_in_form",
                "all_properties_text_option_count",
                {"count": text_option.count(), "alias": property_alias},
            )
            if text_option.count():
                text_option.first.click(timeout=5_000)
                _debug_log(
                    "H10",
                    "stessa_add_transaction.py:_select_property_in_form",
                    "selected_property_via_all_properties",
                    {"alias": property_alias},
                )
                return True
        except Exception:
            pass

    # Fallback: text list item in open menu/dialog.
    opts = page.get_by_text(re.compile(re.escape(property_alias), re.I))
    _debug_log(
        "H10",
        "stessa_add_transaction.py:_select_property_in_form",
        "text_option_fallback_count",
        {"count": opts.count(), "alias": property_alias},
    )
    if opts.count():
        try:
            opts.first.click(timeout=5_000)
            _debug_log(
                "H10",
                "stessa_add_transaction.py:_select_property_in_form",
                "selected_property_text",
                {"alias": property_alias},
            )
            return True
        except Exception:
            pass

    _debug_log(
        "H10",
        "stessa_add_transaction.py:_select_property_in_form",
        "property_not_selected",
        {"alias": property_alias, "alias_low": alias_low},
    )
    return False


def _select_dropdown_option(
    page: Any,
    *,
    field_regex: str,
    option_labels: list[str],
    hypothesis_id: str,
) -> bool:
    """Select an option from a dropdown-like control with robust fallbacks."""
    field_re = re.compile(field_regex, re.I)

    # Open picker by role, then choose role=option first.
    for role in ("combobox", "button"):
        picker = page.get_by_role(role, name=field_re)
        _debug_log(
            hypothesis_id,
            "stessa_add_transaction.py:_select_dropdown_option",
            "picker_probe",
            {"role": role, "field_regex": field_regex, "count": picker.count()},
        )
        if picker.count():
            try:
                picker.first.click(timeout=5_000)
            except Exception:
                continue
            for option_label in option_labels:
                option_re = re.compile(re.escape(option_label), re.I)
                option = page.get_by_role("option", name=option_re)
                _debug_log(
                    hypothesis_id,
                    "stessa_add_transaction.py:_select_dropdown_option",
                    "role_option_probe",
                    {"option_label": option_label, "count": option.count()},
                )
                if option.count():
                    option.first.click(timeout=6_000)
                    return True
                # Fallback to generic text option in opened menu/listbox.
                txt = page.get_by_text(option_re)
                if txt.count():
                    txt.first.click(timeout=6_000)
                    return True
    return False


def _select_direction_for_transaction(page: Any, parsed: ParsedTransaction) -> None:
    """Set Money In / Money Out based on parsed transaction semantics."""
    # Current rule: rental income is money in; all configured expense categories are money out.
    desired = "Money In" if parsed.category_key == "rental_income" else "Money Out"
    _debug_log(
        "H20",
        "stessa_add_transaction.py:_select_direction_for_transaction",
        "direction_target",
        {"category_key": parsed.category_key, "desired": desired},
    )

    # Try radios by accessible label first.
    radio = page.get_by_role("radio", name=re.compile(re.escape(desired), re.I))
    if radio.count():
        try:
            radio.first.check(timeout=4_000)
            _debug_log(
                "H20",
                "stessa_add_transaction.py:_select_direction_for_transaction",
                "direction_set_by_role_radio",
                {"desired": desired},
            )
            return
        except Exception:
            pass

    # Fallback: label text click (common when radio is custom-styled).
    lbl = page.get_by_text(re.compile(rf"^{re.escape(desired)}$", re.I))
    if lbl.count():
        try:
            lbl.first.click(timeout=4_000)
            _debug_log(
                "H20",
                "stessa_add_transaction.py:_select_direction_for_transaction",
                "direction_set_by_text_click",
                {"desired": desired},
            )
            return
        except Exception:
            pass

    _debug_log(
        "H20",
        "stessa_add_transaction.py:_select_direction_for_transaction",
        "direction_not_set",
        {"desired": desired},
    )


def run_sync_playwright(parsed: ParsedTransaction, config: dict[str, Any]) -> str:
    """Browser automation: login, property, add transaction, verify."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    username = config.get("stessa_username")
    password = config.get("stessa_password")
    if not username or not password:
        return "❌ Missing stessa_username or stessa_password in config.yaml."

    cdp_url = config.get("playwright_cdp_url") or config.get("playwright_cdp_endpoint")
    require_cdp_session = _coerce_bool(config.get("require_cdp_session"), default=False)
    cdp_require_authenticated = _coerce_bool(
        config.get("cdp_require_authenticated_session"),
        default=True,
    )
    allow_login_fallback = _coerce_bool(config.get("allow_login_fallback"), default=False)

    def fail_with_shot(page: Any | None, msg: str) -> str:
        path = error_timestamp_path()
        _debug_log(
            "H14",
            "stessa_add_transaction.py:run_sync_playwright",
            "fail_with_shot_start",
            {"msg": msg, "has_page": page is not None},
        )
        try:
            if page is not None:
                page.screenshot(path=str(path), full_page=True)
                _debug_log(
                    "H14",
                    "stessa_add_transaction.py:run_sync_playwright",
                    "fail_with_shot_saved",
                    {"path": str(path)},
                )
                return f"❌ {msg} — screenshot saved to {path}"
        except Exception:
            LOGGER.exception("Screenshot failed")
            _debug_log(
                "H14",
                "stessa_add_transaction.py:run_sync_playwright",
                "fail_with_shot_exception",
                {"path": str(path)},
            )
        return f"❌ {msg}"

    with sync_playwright() as p:
        browser = None
        page = None
        stage = "init"
        try:
            # TODO: update launch/connect options if Stessa requires a specific viewport
            if cdp_url:
                stage = "connect_over_cdp"
                browser = p.chromium.connect_over_cdp(str(cdp_url))
                contexts = browser.contexts
                context = contexts[0] if contexts else browser.new_context()
                pages = context.pages
                _debug_log(
                    "H7",
                    "stessa_add_transaction.py:run_sync_playwright",
                    "cdp_pages_detected",
                    {"page_count": len(pages), "urls": [pg.url for pg in pages[:10]]},
                )
                page = pages[0] if pages else context.new_page()
            else:
                if require_cdp_session:
                    return (
                        "❌ require_cdp_session is enabled but no playwright_cdp_url is configured. "
                        "Set playwright_cdp_url to a running Chromium CDP endpoint."
                    )
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()

            stage = "set_default_timeout"
            page.set_default_timeout(30_000)

            stage = "auth_check"
            authed = _session_is_authenticated(page) if cdp_url else False
            if not authed:
                if cdp_url and cdp_require_authenticated and not allow_login_fallback:
                    return fail_with_shot(
                        page,
                        (
                            "CDP session is not authenticated in Stessa. "
                            "Complete login (including human verification) once in the attached browser, "
                            "then retry."
                        ),
                    )
                # --- Login (Stessa -> Roofstock Auth0 at auth.roofstock.com in production)
                stage = "login_flow"
                _stessa_roofstock_login(page, str(username), str(password))
                try:
                    stage = "wait_post_login_url"
                    page.wait_for_url(re.compile(r".*app\.stessa\.com(?!/login).*"), timeout=90_000)
                except PlaywrightTimeoutError:
                    err_el = page.locator('[role="alert"], .error, [class*="error"]').first
                    detail = ""
                    if err_el.count():
                        try:
                            detail = err_el.inner_text(timeout=2000)
                        except Exception:
                            detail = ""
                    raise RuntimeError(f"Login failed or still on login page. {detail}".strip())

            # --- Transactions page (stay on this surface; do not hop to properties page) ---
            stage = "navigate_transactions_surface"
            transactions_ready = _navigate_to_transactions_surface(page)
            _debug_log(
                "H12",
                "stessa_add_transaction.py:run_sync_playwright",
                "transactions_surface_gate",
                {"ready": transactions_ready, "url": page.url},
            )
            if not transactions_ready:
                return fail_with_shot(
                    page,
                    (
                        "Could not reach https://app.stessa.com/web3/transactions in this CDP session. "
                        "Open that page in the attached browser and retry."
                    ),
                )
            stage = "wait_transactions_networkidle"
            try:
                page.wait_for_load_state("networkidle", timeout=5_000)
                _debug_log(
                    "H16",
                    "stessa_add_transaction.py:run_sync_playwright",
                    "transactions_networkidle_reached",
                    {"url": page.url},
                )
            except PlaywrightTimeoutError:
                _debug_log(
                    "H16",
                    "stessa_add_transaction.py:run_sync_playwright",
                    "transactions_networkidle_timeout_ignored",
                    {"url": page.url},
                )

            # --- Add transaction ---
            # TODO: update Add transaction trigger if button text changes
            stage = "scan_add_button"
            add_btn_count = page.get_by_role("button", name=re.compile(r"add\s*transaction", re.I)).count()
            _debug_log(
                "H13",
                "stessa_add_transaction.py:run_sync_playwright",
                "add_transaction_role_button_count",
                {"count": add_btn_count, "url": page.url},
            )
            visible_button_texts: list[str] = []
            btns = page.locator("button:visible")
            for i in range(min(btns.count(), 25)):
                try:
                    text = btns.nth(i).inner_text(timeout=800).strip()
                except Exception:
                    continue
                if text:
                    visible_button_texts.append(text)
            _debug_log(
                "H13",
                "stessa_add_transaction.py:run_sync_playwright",
                "visible_buttons_sample",
                {"sample": visible_button_texts[:15]},
            )
            add_btn = page.get_by_role("button", name=re.compile(r"add\s*transaction", re.I))
            if add_btn.count() == 0:
                # Stessa transactions page currently exposes a generic "+ Add" CTA.
                add_btn = page.get_by_role("button", name=re.compile(r"^\+?\s*add$", re.I))
                _debug_log(
                    "H13",
                    "stessa_add_transaction.py:run_sync_playwright",
                    "using_add_cta_button_fallback",
                    {"fallback_count": add_btn.count()},
                )
            if add_btn.count() == 0:
                add_btn = page.get_by_text(re.compile(r"add\s*transaction", re.I))
                _debug_log(
                    "H13",
                    "stessa_add_transaction.py:run_sync_playwright",
                    "using_add_transaction_text_fallback",
                    {"fallback_count": add_btn.count()},
                )
            stage = "click_add_button"
            add_btn.first.click()

            mmddyyyy = parsed.date.strftime("%m/%d/%Y")
            amt_str = f"{parsed.amount:.2f}"
            transaction_name = _resolved_transaction_name(parsed, config)
            parsed.transaction_name = transaction_name
            form_scope = page.locator(
                '[role="dialog"]:visible, [class*="modal" i]:visible, [class*="drawer" i]:visible, form:visible',
            ).first

            # Fill transaction name first so it is set even if later field mapping fails.
            name_filled = False
            for label_re in (
                re.compile(r"^name$", re.I),
                re.compile(r"transaction\s*name", re.I),
                re.compile(r"payer\s*/?\s*payee", re.I),
                re.compile(r"payee", re.I),
                re.compile(r"payer", re.I),
            ):
                loc = form_scope.get_by_label(label_re)
                if loc.count() and _is_fillable_input(loc.first):
                    landed = _fill_payee_field(loc.first, transaction_name, "label")
                    _press_enter_for_payee_commit(page, loc.first, transaction_name, "label")
                    if landed:
                        name_filled = True
                        _debug_log(
                            "H18",
                            "stessa_add_transaction.py:run_sync_playwright",
                            "name_filled_by_label",
                            {"label_regex": label_re.pattern, "value": transaction_name},
                        )
                        break
            if not name_filled:
                by_ph = form_scope.locator(
                    'input[placeholder*="name" i]:visible, input[name*="name" i]:visible, '
                    'input[placeholder*="payee" i]:visible, input[name*="payee" i]:visible, '
                    'input[placeholder*="payer" i]:visible, input[name*="payer" i]:visible',
                ).first
                if by_ph.count() and _is_fillable_input(by_ph):
                    landed = _fill_payee_field(by_ph, transaction_name, "placeholder")
                    _press_enter_for_payee_commit(page, by_ph, transaction_name, "placeholder")
                    if landed:
                        name_filled = True
                        _debug_log(
                            "H18",
                            "stessa_add_transaction.py:run_sync_playwright",
                            "name_filled_by_placeholder",
                            {"value": transaction_name},
                        )
            if not name_filled:
                _debug_log(
                    "H18",
                    "stessa_add_transaction.py:run_sync_playwright",
                    "name_not_filled",
                    {"value": transaction_name},
                )
                # Positional fallback from runtime evidence: form exposes unlabeled text/tel/text inputs.
                plain_text_inputs = form_scope.locator(
                    'input[type="text"]:visible, input[type="search"]:visible, [role="combobox"] input:visible',
                )
                _debug_log(
                    "H29",
                    "stessa_add_transaction.py:run_sync_playwright",
                    "payee_fallback_candidate_count",
                    {"count": plain_text_inputs.count()},
                )
                for idx in range(min(plain_text_inputs.count(), 8)):
                    first_text = plain_text_inputs.nth(idx)
                    if _is_fillable_input(first_text):
                        landed = _fill_payee_field(
                            first_text,
                            transaction_name,
                            f"positional_text_fallback_{idx}",
                        )
                        _press_enter_for_payee_commit(
                            page,
                            first_text,
                            transaction_name,
                            f"positional_text_fallback_{idx}",
                        )
                        if landed:
                            name_filled = True
                            _debug_log(
                                "H18",
                                "stessa_add_transaction.py:run_sync_playwright",
                                "name_filled_by_positional_text_fallback",
                                {"value": transaction_name, "count": plain_text_inputs.count(), "index": idx},
                            )
                            break

            # TODO: update form field locators if Stessa transaction modal changes
            date_filled = False
            _debug_log(
                "H15",
                "stessa_add_transaction.py:run_sync_playwright",
                "date_fill_start",
                {"url": page.url},
            )
            for label_re in (
                re.compile(r"^date$", re.I),
                re.compile(r"transaction\s*date", re.I),
            ):
                loc = form_scope.get_by_label(label_re)
                if loc.count():
                    candidate = loc.first
                    if _is_fillable_input(candidate):
                        candidate.fill(mmddyyyy)
                        date_filled = True
                        _debug_log(
                            "H15",
                            "stessa_add_transaction.py:run_sync_playwright",
                            "date_filled_by_label",
                            {"label_regex": label_re.pattern},
                        )
                        break
            if not date_filled:
                ph = form_scope.locator('input[placeholder*="date" i], input[name*="date" i]').first
                if ph.count():
                    if _is_fillable_input(ph):
                        ph.fill(mmddyyyy)
                        date_filled = True
                        _debug_log(
                            "H15",
                            "stessa_add_transaction.py:run_sync_playwright",
                            "date_filled_by_placeholder",
                            {"strategy": "placeholder_or_name"},
                        )
                else:
                    # Runtime evidence in this UI variant: date control appears as input[type="tel"].
                    tel_date = form_scope.locator('input[type="tel"]:visible').first
                    if tel_date.count() and _is_fillable_input(tel_date):
                        tel_date.fill(mmddyyyy)
                        _debug_log(
                            "H15",
                            "stessa_add_transaction.py:run_sync_playwright",
                            "date_filled_by_tel_input",
                            {"value": mmddyyyy},
                        )
                        try:
                            _debug_log(
                                "H15",
                                "stessa_add_transaction.py:run_sync_playwright",
                                "date_tel_value_after_fill",
                                {"value": tel_date.input_value()},
                            )
                        except Exception:
                            pass
                        date_filled = True
                    # Some Stessa variants use a date button that opens a popover calendar/input.
                    date_button = form_scope.get_by_role("button", name=re.compile(r"date", re.I))
                    if (not date_filled) and date_button.count():
                        try:
                            date_button.first.click(timeout=5_000)
                            popup_inputs = page.locator(
                                '[role="dialog"] input:visible:not([type="button"]):not([type="submit"]):not([type="reset"]), '
                                '[role="dialog"] [role="textbox"]:visible',
                            )
                            _debug_log(
                                "H15",
                                "stessa_add_transaction.py:run_sync_playwright",
                                "date_button_popup_probe",
                                {"input_count": popup_inputs.count()},
                            )
                            if popup_inputs.count():
                                popup_inputs.first.fill(mmddyyyy)
                                popup_inputs.first.press("Enter")
                                date_filled = True
                                _debug_log(
                                    "H15",
                                    "stessa_add_transaction.py:run_sync_playwright",
                                    "date_filled_via_button_popup",
                                    {"strategy": "date_button_popup_input"},
                                )
                        except Exception:
                            pass
                    if date_filled:
                        pass
                    else:
                    # Fallback: prefer date-like input attributes, then first editable non-button input.
                        fallback = form_scope.locator(
                            'input[type="date"]:visible, input[name*="date" i]:visible, input[placeholder*="date" i]:visible, '
                            'input[autocomplete*="date" i]:visible',
                        )
                        fb_count = fallback.count()
                        _debug_log(
                            "H15",
                            "stessa_add_transaction.py:run_sync_playwright",
                            "date_fallback_probe",
                            {"visible_input_count": fb_count},
                        )
                        if fb_count:
                            first = fallback.first
                            if _is_fillable_input(first):
                                first.fill(mmddyyyy)
                                date_filled = True
                                _debug_log(
                                    "H15",
                                    "stessa_add_transaction.py:run_sync_playwright",
                                    "date_filled_by_generic_fallback",
                                    {"strategy": "date_like_visible_input"},
                                )
                        if not date_filled:
                            # Log a sample of visible input attributes for diagnosis.
                            samples: list[str] = []
                            all_inputs = form_scope.locator("input:visible")
                            for i in range(min(all_inputs.count(), 12)):
                                node = all_inputs.nth(i)
                                try:
                                    t = node.get_attribute("type") or ""
                                    n = node.get_attribute("name") or ""
                                    p = node.get_attribute("placeholder") or ""
                                    samples.append(f"type={t}|name={n}|ph={p}")
                                except Exception:
                                    continue
                            _debug_log(
                                "H15",
                                "stessa_add_transaction.py:run_sync_playwright",
                                "date_field_not_found",
                                {"visible_input_samples": samples},
                            )
                        # Positional fallback: choose the last unlabeled text input (name is usually first).
                        plain_text_inputs = form_scope.locator('input[type="text"]:visible')
                        if plain_text_inputs.count() >= 2:
                            idx = plain_text_inputs.count() - 1
                            candidate = plain_text_inputs.nth(idx)
                            if _is_fillable_input(candidate):
                                candidate.fill(mmddyyyy)
                                date_filled = True
                                _debug_log(
                                    "H15",
                                    "stessa_add_transaction.py:run_sync_playwright",
                                    "date_filled_by_positional_text_fallback",
                                    {"index": idx, "count": plain_text_inputs.count()},
                                )
                        if not date_filled:
                            raise RuntimeError("Could not find date field for transaction form.")

            amt_filled = False
            for label_re in (re.compile(r"^amount$", re.I), re.compile(r"amount\s*\$?", re.I)):
                loc = form_scope.get_by_label(label_re)
                if loc.count():
                    candidate = loc.first
                    if _is_fillable_input(candidate):
                        candidate.fill(amt_str)
                        amt_filled = True
                        _debug_log(
                            "H17",
                            "stessa_add_transaction.py:run_sync_playwright",
                            "amount_filled_by_label",
                            {"label_regex": label_re.pattern},
                        )
                        break
                    _debug_log(
                        "H17",
                        "stessa_add_transaction.py:run_sync_playwright",
                        "amount_label_not_fillable",
                        {
                            "label_regex": label_re.pattern,
                            "type": candidate.get_attribute("type") or "",
                            "name": candidate.get_attribute("name") or "",
                            "value": candidate.get_attribute("value") or "",
                        },
                    )
            if not amt_filled:
                pamt = form_scope.locator(
                    'input[inputmode="decimal"], input[type="number"], input[name*="amount" i]',
                ).first
                if pamt.count() and _is_fillable_input(pamt):
                    pamt.fill(amt_str)
                    amt_filled = True
                    _debug_log(
                        "H17",
                        "stessa_add_transaction.py:run_sync_playwright",
                        "amount_filled_by_inputmode_fallback",
                        {"strategy": "decimal_number_name_amount"},
                    )
                else:
                    # Last resort: editable input with amount-like placeholder/name.
                    editable_amount = form_scope.locator(
                        'input:visible:not([type="button"]):not([type="submit"]):not([type="reset"])',
                    )
                    for i in range(min(editable_amount.count(), 10)):
                        node = editable_amount.nth(i)
                        name_attr = (node.get_attribute("name") or "").lower()
                        ph_attr = (node.get_attribute("placeholder") or "").lower()
                        if "amount" in name_attr or "amount" in ph_attr or "$" in ph_attr:
                            if _is_fillable_input(node):
                                node.fill(amt_str)
                                amt_filled = True
                                _debug_log(
                                    "H17",
                                    "stessa_add_transaction.py:run_sync_playwright",
                                    "amount_filled_by_editable_fallback",
                                    {"index": i, "name": name_attr, "placeholder": ph_attr},
                                )
                                break
                    if not amt_filled:
                        raise RuntimeError("Could not find amount field for transaction form.")

            # Property selection (required on global transactions page).
            _select_property_in_form(page, parsed.property_alias)
            _select_direction_for_transaction(page, parsed)

            cat_label = parsed.category_label
            sub_label = parsed.subcategory_label
            category_options = (
                ["Income", cat_label]
                if parsed.category_key == "rental_income"
                else [cat_label]
            )

            # TODO: update category/subcategory controls if Stessa uses different widgets
            if not _select_dropdown_option(
                page,
                field_regex=r"category",
                option_labels=category_options,
                hypothesis_id="H19",
            ):
                raise RuntimeError(f"Could not select category option: {category_options}")

            time.sleep(0.5)
            if not _select_dropdown_option(
                page,
                field_regex=r"subcategory",
                option_labels=[sub_label],
                hypothesis_id="H19",
            ):
                _debug_log(
                    "H19",
                    "stessa_add_transaction.py:run_sync_playwright",
                    "subcategory_picker_missing_try_category_fallback",
                    {"subcategory": sub_label},
                )
                # Some Stessa variants expose a single category picker with nested items.
                if not _select_dropdown_option(
                    page,
                    field_regex=r"category|all\s+categories",
                    option_labels=[sub_label],
                    hypothesis_id="H19",
                ):
                    raise RuntimeError(f"Could not select subcategory option: {sub_label}")
                _debug_log(
                    "H19",
                    "stessa_add_transaction.py:run_sync_playwright",
                    "subcategory_selected_via_category_picker_fallback",
                    {"subcategory": sub_label},
                )

            if parsed.notes:
                notes_box = page.get_by_label(re.compile(r"note|memo|description", re.I))
                if notes_box.count():
                    notes_box.first.fill(parsed.notes)

            # Reassert Name right before save in case earlier form interactions reset it.
            name_confirmed = False
            visible_text_inputs = form_scope.locator('input[type="text"]:visible')
            for i in range(min(visible_text_inputs.count(), 6)):
                node = visible_text_inputs.nth(i)
                try:
                    val = (node.input_value(timeout=1000) or "").strip()
                except Exception:
                    val = ""
                if val == transaction_name:
                    name_confirmed = True
                    _debug_log(
                        "H18",
                        "stessa_add_transaction.py:run_sync_playwright",
                        "name_confirmed_before_save",
                        {"index": i, "value": val},
                    )
                    break
            if not name_confirmed and visible_text_inputs.count():
                # Prefer first visible text input as name slot in current UI variant.
                candidate = visible_text_inputs.first
                if _is_fillable_input(candidate):
                    candidate.fill(transaction_name)
                    _debug_log(
                        "H18",
                        "stessa_add_transaction.py:run_sync_playwright",
                        "name_reasserted_before_save",
                        {"value": transaction_name, "text_input_count": visible_text_inputs.count()},
                    )

            # TODO: update save control label if it changes
            save_btn = form_scope.get_by_role(
                "button",
                name=re.compile(r"save|submit|add|done|create|confirm|record", re.I),
            )
            _debug_log(
                "H22",
                "stessa_add_transaction.py:run_sync_playwright",
                "save_role_button_count",
                {"count": save_btn.count()},
            )
            if save_btn.count():
                save_btn.first.click()
            else:
                submit_input = form_scope.locator('input[type="submit"]:visible, button[type="submit"]:visible')
                _debug_log(
                    "H22",
                    "stessa_add_transaction.py:run_sync_playwright",
                    "save_submit_input_count",
                    {"count": submit_input.count()},
                )
                if submit_input.count():
                    submit_input.first.click()
                else:
                    # Last fallback: scoped text button-ish element in the form.
                    save_text = form_scope.get_by_text(re.compile(r"save|submit|add|done|create|confirm|record", re.I))
                    _debug_log(
                        "H22",
                        "stessa_add_transaction.py:run_sync_playwright",
                        "save_text_fallback_count",
                        {"count": save_text.count()},
                    )
                    if save_text.count():
                        save_text.first.click()
                    else:
                        raise RuntimeError("Could not find form submit/save control for transaction dialog.")

            try:
                page.wait_for_load_state("networkidle", timeout=5_000)
            except PlaywrightTimeoutError:
                pass
            time.sleep(1.5)

            # --- Verify in list ---
            snippets = _norm_amount_snippets(parsed.amount)
            found = False
            verify_evidence: dict[str, Any] = {"amount_snippets": list(snippets), "amount_match": False}
            # Signal 1: amount appears in visible page text.
            for snip in snippets:
                if page.get_by_text(snip, exact=False).count() > 0:
                    found = True
                    verify_evidence["amount_match"] = True
                    verify_evidence["amount_snippet_hit"] = snip
                    break
            # Signal 2: success toast/message appears.
            if not found:
                toast = page.get_by_text(re.compile(r"saved|added|success|transaction", re.I))
                if toast.count():
                    found = True
                    verify_evidence["toast_match"] = True
                    verify_evidence["toast_count"] = toast.count()
            # Signal 3: add form closes (inputs from dialog no longer visible).
            if not found:
                dialog_inputs = page.locator(
                    '[role="dialog"] input:visible, [class*="modal" i] input:visible, [class*="drawer" i] input:visible',
                )
                verify_evidence["dialog_input_count"] = dialog_inputs.count()
                if dialog_inputs.count() == 0:
                    found = True
                    verify_evidence["form_closed"] = True

            _debug_log(
                "H21",
                "stessa_add_transaction.py:run_sync_playwright",
                "post_save_verification",
                verify_evidence,
            )
            if not found:
                return fail_with_shot(
                    page,
                    "Saved but could not verify the transaction in the list (amount not found).",
                )

            return format_success_message(parsed)

        except PlaywrightTimeoutError as e:
            _debug_log(
                "H16",
                "stessa_add_transaction.py:run_sync_playwright",
                "playwright_timeout_stage",
                {"stage": stage, "error": str(e), "url": page.url if page is not None else ""},
            )
            return fail_with_shot(page, f"Timed out waiting for UI ({e!s})")
        except RuntimeError as e:
            return fail_with_shot(page, str(e))
        except Exception as e:
            return fail_with_shot(page, f"{type(e).__name__}: {e}")
        finally:
            if browser is not None:
                browser.close()


def run(instruction: str, *, config_path: Path | str | None = None) -> str:
    """Parse instruction with Gemini, then log the transaction in Stessa via Playwright."""
    try:
        config = load_skill_config(config_path)
    except Exception as e:
        return f"❌ Could not load config: {type(e).__name__}: {e}"

    try:
        data = parse_instruction_llm(instruction, config)
    except json.JSONDecodeError as e:
        return f"❌ Could not parse JSON from model: {e}"
    except ValueError as e:
        return f"❌ {e}"
    except Exception as e:
        return f"❌ Instruction parsing failed ({type(e).__name__}): {e}"

    verr = validate_parsed(data)
    if verr:
        return verr

    try:
        parsed = to_parsed_transaction(data)
    except Exception as e:
        return f"❌ Could not build transaction ({type(e).__name__}): {e}"

    extracted_payee = _extract_payee_from_instruction(instruction)
    if extracted_payee:
        low = (parsed.transaction_name or "").strip().lower()
        category_like_exact = {
            "rent",
            "rents",
            "rental income",
            "income",
            parsed.category_label.lower(),
            parsed.subcategory_label.lower(),
        }
        category_like_substrings = ("rent", "income", "payment", "mortgage", "utility", "repair")
        if (not low) or (low in category_like_exact) or any(token in low for token in category_like_substrings):
            parsed.transaction_name = extracted_payee

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        fut = loop.run_in_executor(
            None,
            partial(run_sync_playwright, parsed, config),
        )
        return loop.run_until_complete(fut)
    except Exception as e:
        return f"❌ Browser automation failed ({type(e).__name__}): {e}"
    finally:
        loop.close()


def _demo_instructions() -> list[str]:
    return [
        "Add $1200 rent for property ABC on 2025-05-01",
        "Log $85 water bill for ABC dated April 15",
        "Record a $1,950 mortgage payment for ABC on 2025-04-30",
    ]


def _fake_gemini_json_for_demo(instr: str) -> str:
    """Deterministic JSON for __main__ demos (no API calls)."""
    if "1200" in instr:
        return json.dumps(
            {
                "amount": 1200.0,
                "date": "2025-05-01",
                "category": "rental_income",
                "subcategory": "rents",
                "property_alias": "ABC",
                "notes": None,
                "name": "Metropole Properties",
            },
        )
    if "85" in instr:
        return json.dumps(
            {
                "amount": 85.0,
                "date": "2025-04-15",
                "category": "utilities",
                "subcategory": "water_sewer",
                "property_alias": "ABC",
                "notes": "Water bill",
                "name": "Metropole Properties",
            },
        )
    return json.dumps(
        {
            "amount": 1950.0,
            "date": "2025-04-30",
            "category": "mortgages_loans",
            "subcategory": "mortgage_payment",
            "property_alias": "ABC",
            "notes": None,
            "name": "Metropole Properties",
        },
    )


if __name__ == "__main__":
    import sys
    from unittest.mock import patch

    demo_cfg: dict[str, Any] = {
        "stessa_username": "demo@example.com",
        "stessa_password": "secret",
        "gemini_api_key_env": "GOOGLE_API_KEY",
        "llm_model": "gemini-2.5-flash",
    }

    # Same module object as this file (avoids double-import when run as -m __main__)
    _stessa_mod = sys.modules[__name__]

    for instr in _demo_instructions():
        fake_json = _fake_gemini_json_for_demo(instr)
        with (
            patch.object(_stessa_mod, "load_skill_config", return_value=demo_cfg),
            patch.object(_stessa_mod, "api_key_from_env", return_value="dummy-key-for-demo"),
            patch.object(_stessa_mod, "generate_text", return_value=fake_json),
            patch.object(
                _stessa_mod,
                "run_sync_playwright",
                return_value="✅ Transaction logged: (mock browser)",
            ),
        ):
            parsed_dict = json.loads(strip_json_fence(fake_json))
            print("Instruction:", instr)
            print("Parsed JSON:", json.dumps(parsed_dict))
            print("Result:", _stessa_mod.run(instr))
            print()
