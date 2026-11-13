"""Ověření navigace WebForms bez vykonávání JavaScriptu a bez síťového I/O."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

_POSTBACK = re.compile(
    r"""^\s*(?:javascript:\s*)?__doPostBack\(\s*(['"])([A-Za-z0-9_$:.\-]+)\1"""
    r"""\s*,\s*(['"])([A-Za-z0-9_$:.,|;/ \-]*)\3\s*\)\s*;?\s*$""",
    re.IGNORECASE,
)


def safe_url(base_url: str, current_url: str, target: str) -> str:
    """Povolit pouze skutečnou adresu stejného serveru a stejného protokolu."""
    result = urljoin(current_url, target)
    base, parsed = urlsplit(base_url), urlsplit(result)
    if (
        parsed.scheme.lower() not in ("http", "https")
        or parsed.scheme.lower() != base.scheme.lower()
        or parsed.netloc.lower() != base.netloc.lower()
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("Adresa navigace není na původním portálu")
    return result


def login_page(html: str, url: str) -> bool:
    """Rozpoznat přihlašovací formulář nebo přesměrování na úvodní stránku."""
    soup = BeautifulSoup(html, "html.parser")
    return bool(
        soup.find("input", attrs={"type": re.compile(r"^password$", re.I)})
        or urlsplit(url).path.casefold().endswith("/default.aspx")
    )


def context_status(html: str, expected: dict) -> bool | None:
    """Ověřit konkrétní kontrolky místa; None znamená nedoloženou identitu.

    Odběrné místo se nesmí určit z libovolné shody textu, adresy nebo CSV.
    Odlišný obsah kterékoliv známé kontrolky znamená konflikt.
    """
    soup = BeautifulSoup(html, "html.parser")
    expected_values = {
        "edcpid": str(expected.get("evidencni_cislo") or ""),
        "edcpevnum": str(expected.get("technicke_cislo") or ""),
    }

    def normalized(value: str) -> str:
        return "".join(value.split()).casefold()

    found = False
    for suffix, expected_value in expected_values.items():
        if not expected_value:
            continue
        values = []
        for node in soup.find_all(attrs={"id": True}) + soup.find_all(
            attrs={"name": True}
        ):
            if any(
                str(node.get(attr, "")).casefold().endswith(suffix)
                for attr in ("id", "name")
            ):
                raw = (
                    node.get("value")
                    if node.name == "input"
                    else node.get_text(" ", strip=True)
                )
                if raw:
                    values.append(normalized(str(raw)))
        if values:
            found = True
            if any(value != normalized(expected_value) for value in values):
                return False
    return True if found else None


def profile_ready(html: str, *, require_filter: bool) -> bool:
    """Profil vyžaduje export a při přímém fallbacku také filtr období."""
    soup = BeautifulSoup(html, "html.parser")
    export = soup.find("a", href=re.compile(r"DocumentShow\.aspx(?:\?|$)", re.I))
    if not export:
        return False
    if not require_filter:
        return True
    return bool(
        soup.find("select", attrs={"name": re.compile(r"edGraphLength$", re.I)})
    )


def profile_diagnostics(html: str, url: str) -> dict:
    """Vrátit jen počty a příznaky známých kontrolek; nikdy hodnoty ani HTML.

    Výstup je vhodný pro log odmítnuté odpovědi. Neobsahuje URL, query,
    identifikátory, názvy polí s dynamickým obsahem ani přihlašovací údaje.
    O povolení odečtů nerozhoduje, slouží pouze k rozlišení struktury stránky.
    """
    soup = BeautifulSoup(html, "html.parser")
    points = soup.find_all(
        "select", attrs={"name": re.compile(r"edMeteringPoint$", re.I)}
    )
    periods = soup.find_all(
        "select", attrs={"name": re.compile(r"edGraphLength$", re.I)}
    )
    form = points[0].find_parent("form") if points else None
    action_ok = False
    if form:
        try:
            action = safe_url(url, url, str(form.get("action") or url))
            action_ok = urlsplit(action).path.casefold() == "/userdata/profiledata.aspx"
        except ValueError:
            pass
    return {
        "profile_path": urlsplit(url).path.casefold() == "/userdata/profiledata.aspx",
        "meter_selects": len(points),
        "meter_selects_by_id": len(
            soup.find_all("select", id=re.compile(r"edMeteringPoint$", re.I))
        ),
        "meter_options": [len(point.find_all("option")) for point in points],
        "period_filters": len(periods),
        "month_option": any(
            period.find("option", attrs={"value": "M"}) for period in periods
        ),
        "same_form": bool(form and periods and periods[0].find_parent("form") is form),
        "action_profile": action_ok,
    }


def profile_without_meter(html: str, url: str) -> bool:
    """Rozpoznat pouze známý profil bez nabízeného dálkového měřidla.

    Jde o strukturální kontrolu HTML bez sítě: očekává stránku ProfileData,
    jediný prázdný select edMeteringPoint a filtr s volbou měsíce ve stejném
    formuláři. Přítomný export, jiná stránka či neznámá struktura vrací False.
    Žádné odečty ani nulovou spotřebu nevytváří. Úspěšné HTTP, relaci a identitu
    právě zvoleného místa musí před použitím výsledku ověřit volající.
    """
    if urlsplit(url).path.casefold() != "/userdata/profiledata.aspx":
        return False
    soup = BeautifulSoup(html, "html.parser")
    if soup.find("a", href=re.compile(r"DocumentShow\.aspx(?:\?|$)", re.I)):
        return False
    points = soup.find_all(
        "select", attrs={"name": re.compile(r"edMeteringPoint$", re.I)}
    )
    if len(points) != 1 or points[0].find("option") is not None:
        return False
    form = points[0].find_parent("form")
    if form is None:
        return False
    period = form.find("select", attrs={"name": re.compile(r"edGraphLength$", re.I)})
    if period is None or period.find("option", attrs={"value": "M"}) is None:
        return False
    # Cizí či odlišný cíl formuláře není známý prázdný profil.
    try:
        action = safe_url(url, url, str(form.get("action") or url))
    except ValueError:
        return False
    return urlsplit(action).path.casefold() == "/userdata/profiledata.aspx"


def navigation_candidate(html: str):
    """Vrátit jen rozpoznaný profilový odkaz, postback nebo submit ovládání."""
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.find_all(("a", "button", "input")):
        description = " ".join(
            str(node.get(key, "")) for key in ("id", "name", "title", "aria-label")
        )
        description += " " + node.get_text(" ", strip=True)
        normalized = "".join(
            char
            for char in unicodedata.normalize("NFKD", description.casefold())
            if not unicodedata.combining(char)
        )
        if "btnprofiledata" not in normalized and "namerene stavy" not in normalized:
            continue
        actions = [str(node.get(key) or "").strip() for key in ("href", "onclick")]
        for action in actions:
            if match := _POSTBACK.fullmatch(action):
                return "postback", node, match.group(2), match.group(4)
        action = actions[0] if node.name == "a" else actions[1]
        if (
            node.name == "a"
            and action
            and not action.casefold().startswith("javascript:")
            and (
                urlsplit(action).path.casefold().endswith("/profiledata.aspx")
                or urlsplit(action).path.casefold() == "profiledata.aspx"
            )
        ):
            return "link", node, action, ""
        if node.name in ("button", "input") and not action and node.get("name"):
            button_type = str(
                node.get("type") or ("submit" if node.name == "button" else "text")
            ).casefold()
            if button_type == "submit":
                return "submit", node, str(node["name"]), str(node.get("value", ""))
    return None


def _disabled(node) -> bool:
    """Vyřadit disabled ovládání včetně fieldsetu mimo jeho první legend."""
    if node.has_attr("disabled"):
        return True
    for fieldset in node.find_parents("fieldset"):
        if not fieldset.has_attr("disabled"):
            continue
        legend = fieldset.find("legend", recursive=False)
        if legend is None or legend not in node.parents:
            return True
    return False


def form_payload(form) -> list[tuple[str, str]]:
    """Úspěšné položky formuláře v pořadí, včetně opakovaných názvů.

    Zahrnuje hidden, text, textarea, označené radio/checkbox a vybrané volby.
    Neaktivní ovládání a nekliknuté submit/image prvky neodesílá.
    """
    fields: list[tuple[str, str]] = []
    for node in form.find_all(("input", "textarea", "select")):
        name = node.get("name")
        if not name or _disabled(node):
            continue
        if node.name == "input":
            kind = str(node.get("type", "text")).casefold()
            if kind in ("submit", "button", "image", "reset", "file"):
                continue
            if kind in ("checkbox", "radio") and not node.has_attr("checked"):
                continue
            fields.append(
                (
                    str(name),
                    str(
                        node.get("value", "on" if kind in ("checkbox", "radio") else "")
                    ),
                )
            )
        elif node.name == "textarea":
            fields.append((str(name), node.get_text()))
        else:
            options = [
                item
                for item in node.find_all("option")
                if not item.has_attr("disabled")
                and not (
                    item.parent.name == "optgroup" and item.parent.has_attr("disabled")
                )
            ]
            selected = [item for item in options if item.has_attr("selected")]
            if not selected and options and not node.has_attr("multiple"):
                selected = options[:1]
            for option in selected:
                fields.append((str(name), str(option.get("value", option.get_text()))))
    return fields
