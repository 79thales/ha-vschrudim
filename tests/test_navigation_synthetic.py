"""Izolované regrese navigace na smyšlených stránkách, bez účtů a cookies."""

import sys
import unittest
from pathlib import Path
from types import ModuleType

import requests

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "vschrudim"
PACKAGE = ModuleType("vschrudim_test")
PACKAGE.__path__ = [str(ROOT)]
sys.modules["vschrudim_test"] = PACKAGE

from bs4 import BeautifulSoup  # noqa: E402
from vschrudim_test.profile_navigation import (  # noqa: E402
    form_payload,
    navigation_candidate,
    profile_diagnostics,
)
from vschrudim_test.vschrudim_client import (  # noqa: E402
    VSChrudimAuthError,
    VSChrudimClient,
    VSChrudimError,
    VSChrudimNoReadingsError,
)

BASE = "https://synthetic.invalid"
MAIN = BASE + "/UserData/MainInfo.aspx"
PROFILE = BASE + "/UserData/ProfileData.aspx"
PLACE = {
    "index": 0,
    "evidencni_cislo": "A-123",
    "technicke_cislo": "T-456",
    "active": True,
}
CONTEXT = (
    '<input name="ctl$edCpId" value="A-123"><input name="ctl$edCpEvNum" value="T-456">'
)
EXPORT = '<a href="../DocumentShow.aspx?DATA_ID=synthetic">CSV</a>'
FILTER = (
    '<select name="ctl$edGraphLength" id="ctl_edGraphLength">'
    '<option value="M" selected>M</option><option value="Y">Y</option></select>'
)
PROFILE_HTML = (
    '<form action="./ProfileData.aspx">' + CONTEXT + FILTER + EXPORT + "</form>"
)


class Response:
    def __init__(self, text="", url=MAIN, status=200, content=None, headers=None):
        self.text, self.url, self.status_code = text, url, status
        self.content = content if content is not None else text.encode("utf-8")
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("syntetická chyba HTTP")


class Session:
    def __init__(self, *, gets=(), posts=()):
        self.gets, self.posts = list(gets), list(posts)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if not self.gets:
            raise AssertionError("Neočekávaný GET")
        return self.gets.pop(0)

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if not self.posts:
            raise AssertionError("Neočekávaný POST")
        return self.posts.pop(0)


def client_with_main(html, *, url=MAIN, gets=(), posts=(), verified=True):
    client = VSChrudimClient("synthetic", "synthetic", BASE)
    client.s = Session(gets=gets, posts=posts)
    client._selected_place = dict(PLACE)
    client._selected_response = Response(html, url)
    client._context_verified = verified
    return client


class NavigationTests(unittest.TestCase):
    def test_original_postback_uses_action_and_all_successful_fields(self):
        html = (
            '<form action="./CurrentForm.aspx" method="post">'
            + CONTEXT
            + '<input name="__VIEWSTATE" value="state">'
            + '<input name="filter" value="2026">'
            + '<input type="checkbox" name="check" value="on" checked>'
            + '<input type="checkbox" name="skip" value="x">'
            + '<select name="point"><option value="one">A</option>'
            + '<option value="two" selected>B</option></select>'
            + '<a id="ctl_MainMenu1_btnProfileData" href="javascript:__doPostBack('
            + "'ctl00$menu$profile','argument'"
            + ')">Naměřené stavy</a></form>'
        )
        client = client_with_main(html, posts=[Response(PROFILE_HTML, PROFILE)])
        text, url = client.open_profile_data()
        self.assertEqual((text, url), (PROFILE_HTML, PROFILE))
        method, url, details = client.s.calls[0]
        self.assertEqual((method, url), ("POST", BASE + "/UserData/CurrentForm.aspx"))
        payload = dict(details["data"])
        self.assertEqual(payload["__VIEWSTATE"], "state")
        self.assertEqual(payload["filter"], "2026")
        self.assertEqual(payload["point"], "two")
        self.assertEqual(payload["check"], "on")
        self.assertNotIn("skip", payload)
        self.assertEqual(payload["__EVENTTARGET"], "ctl00$menu$profile")
        self.assertEqual(payload["__EVENTARGUMENT"], "argument")

    def test_alternate_button_and_direct_profile(self):
        html = (
            '<form action="./other.aspx">'
            + CONTEXT
            + '<input name="other" value="preserved">'
            + '<button name="new$profile" type="submit">Naměřené stavy</button></form>'
        )
        client = client_with_main(html, posts=[Response(PROFILE_HTML, PROFILE)])
        client.open_profile_data()
        self.assertIn(("new$profile", ""), client.s.calls[0][2]["data"])
        self.assertIn(("other", "preserved"), client.s.calls[0][2]["data"])
        direct = client_with_main(PROFILE_HTML, url=PROFILE)
        self.assertEqual(direct.open_profile_data()[1], PROFILE)
        self.assertEqual(direct.s.calls, [])

    def test_unknown_javascript_is_not_executed(self):
        html = (
            "<form>"
            + CONTEXT
            + '<a id="ctl_btnProfileData" href="javascript:alert(1)">'
            + "Naměřené stavy</a></form>"
        )
        self.assertIsNone(navigation_candidate(html))
        client = client_with_main(html, gets=[Response(PROFILE_HTML, PROFILE)])
        client.open_profile_data()
        self.assertEqual(client.s.calls[0][0], "GET")
        self.assertEqual(client.s.calls[0][1], PROFILE)
        self.assertEqual(len(client.s.calls), 1)

    def test_link_onclick_uses_only_recognized_postback(self):
        html = (
            '<form action="./MainInfo.aspx">'
            + CONTEXT
            + '<a id="new_btnProfileData" href="#" onclick="'
            + "__doPostBack(&quot;menu$profile&quot;, 'arg')"
            + '">Naměřené stavy</a></form>'
        )
        client = client_with_main(html, posts=[Response(PROFILE_HTML, PROFILE)])
        client.open_profile_data()
        payload = dict(client.s.calls[0][2]["data"])
        self.assertEqual(payload["__EVENTTARGET"], "menu$profile")
        self.assertEqual(payload["__EVENTARGUMENT"], "arg")

    def test_fallback_requires_matching_context_and_filter(self):
        html = "<form>" + CONTEXT + "</form>"
        wrong = PROFILE_HTML.replace("A-123", "A-999")
        for response in (Response(wrong, PROFILE), Response(CONTEXT + EXPORT, PROFILE)):
            with self.subTest(response=response.text):
                client = client_with_main(html, gets=[response])
                with self.assertRaises(VSChrudimError):
                    client.open_profile_data()
        client = client_with_main("<form></form>", verified=False)
        with self.assertRaises(VSChrudimError):
            client.open_profile_data()
        self.assertEqual(client.s.calls, [])

    def test_http_login_and_other_host_are_not_accepted(self):
        login = Response('<form><input type="password"></form>', MAIN)
        client = client_with_main(login.text)
        with self.assertRaises(VSChrudimAuthError):
            client.open_profile_data()
        client = client_with_main(CONTEXT)
        client._selected_response = Response(CONTEXT, MAIN, 503)
        with self.assertRaises(requests.HTTPError):
            client.open_profile_data()
        html = (
            "<form>"
            + CONTEXT
            + '<a id="btnProfileData" href="https://other.invalid/ProfileData.aspx">Odečty</a></form>'
        )
        client = client_with_main(html)
        with self.assertRaises(VSChrudimError):
            client.open_profile_data()
        self.assertEqual(client.s.calls, [])

    def test_original_csv_download_and_parser_still_work(self):
        """Navazující původní CSV klient zůstává volatelný a beze změny."""
        content = "MERIDLO;CAS;STAV\nX;01.01.2026 00:00:00;1,25\n"
        client = client_with_main(
            "", gets=[Response(content, BASE + "/DocumentShow.aspx")]
        )
        text = client._download_csv_text("../DocumentShow.aspx?DATA_ID=test", PROFILE)
        rows = client.parse_csv_text(text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["value"], 1.25)

    def test_unusable_export_does_not_report_success(self):
        """HTML místo CSV nesmí označit historii za úspěšně doplněnou."""
        client = client_with_main("")
        client.login = lambda: None
        client.list_consumption_places = lambda: [PLACE]
        client.select_consumption_place = lambda index: None
        client.open_profile_data = lambda: (PROFILE_HTML, PROFILE)
        client._download_csv_text = lambda href, url: "<html>chyba</html>"
        with self.assertRaises(VSChrudimError):
            client.get_latest_readings("M")

    def test_valid_place_remains_available_when_another_export_is_empty(self):
        """Neplatný export jednoho místa neskryje druhé s platným odečtem."""
        first = {**PLACE, "adresa": "A"}
        second = {
            **PLACE,
            "index": 1,
            "technicke_cislo": "T-789",
            "adresa": "B",
        }
        selected = [0]
        client = client_with_main("")
        client.login = lambda: None
        client.list_consumption_places = lambda: [first, second]
        client.select_consumption_place = lambda index: selected.__setitem__(0, index)
        client.open_profile_data = lambda: (PROFILE_HTML, PROFILE)
        client._download_csv_text = lambda href, url: (
            "MERIDLO;CAS;STAV\nX;01.01.2026 00:00:00;1,25\n"
            if selected[0] == 0
            else "<html>chyba</html>"
        )
        result = client.get_latest_readings("M")
        self.assertEqual(list(result), ["T-456"])
        self.assertEqual(result["T-456"]["value"], 1.25)

    def test_external_redirect_is_rejected_before_sending_a_request(self):
        redirect = Response(
            "", PROFILE, 302, headers={"Location": "https://elsewhere.invalid/steal"}
        )
        client = client_with_main(
            "<form>"
            + CONTEXT
            + '<a href="./ProfileData.aspx">Naměřené stavy</a></form>',
            gets=[redirect],
        )
        with self.assertRaises(VSChrudimError):
            client.open_profile_data()
        self.assertEqual(len(client.s.calls), 1)
        self.assertEqual(client.s.calls[0][1], PROFILE)
        self.assertFalse(client.s.calls[0][2]["allow_redirects"])

    def test_login_redirect_is_classified_as_session_expiry(self):
        redirect = Response("", PROFILE, 302, headers={"Location": "/Default.aspx"})
        login = Response("<form><input type='password'></form>", BASE + "/Default.aspx")
        client = client_with_main(
            "<form>"
            + CONTEXT
            + '<a href="./ProfileData.aspx">Naměřené stavy</a></form>',
            gets=[redirect, login],
        )
        with self.assertRaises(VSChrudimAuthError):
            client.open_profile_data()
        self.assertEqual([call[0] for call in client.s.calls], ["GET", "GET"])

    def test_successful_controls_include_repeated_values(self):
        soup = BeautifulSoup(
            '<form><fieldset disabled><input name="disabled" value="1"></fieldset>'
            '<select name="x" multiple><option selected value="1"></option>'
            '<option selected value="2"></option></select>'
            '<textarea name="comment">ok</textarea>'
            '<input name="save" type="submit" value="yes"></form>',
            "html.parser",
        )
        self.assertEqual(
            form_payload(soup.form), [("x", "1"), ("x", "2"), ("comment", "ok")]
        )

    def test_changed_place_order_is_rejected_before_post(self):
        grid = (
            '<table id="gvConsumptionPlaces"><tr><td>x</td><td>other</td><td>T-456</td>'
            "<td>n/a</td><td>n/a</td><td>od 01.01.2020</td></tr></table>"
        )
        client = VSChrudimClient("synthetic", "synthetic", BASE)
        client._places_by_index = {0: PLACE}
        client.s = Session(gets=[Response(grid, BASE + "/ConsumptionPlaceList.aspx")])
        with self.assertRaises(VSChrudimError):
            client.select_consumption_place(0)
        self.assertEqual([call[0] for call in client.s.calls], ["GET"])

    def test_selects_requested_place_then_uses_fresh_response(self):
        grid = (
            '<form action="./Select.aspx"><input name="__VIEWSTATE" value="state">'
            '<input name="filter" value="keep"><table id="gvConsumptionPlaces">'
            "<tr><td>x</td><td>A-123</td><td>T-456</td>"
            "<td>n/a</td><td>n/a</td><td>od 01.01.2020</td></tr>"
            "<tr><td>x</td><td>A-999</td><td>T-999</td><td>n/a</td><td>n/a</td>"
            "<td>od 01.01.2020</td></tr></table></form>"
        )
        main = (
            '<form action="./MainInfo.aspx">'
            + CONTEXT
            + '<a id="ctl_MainMenu1_btnProfileData" href="javascript:__doPostBack('
            + "'menu$profile',''"
            + ')">Naměřené stavy</a></form>'
        )
        selection = Response(
            "1|#||pageRedirect||/UserData/MainInfo.aspx|",
            BASE + "/ConsumptionPlaceList.aspx",
        )
        client = VSChrudimClient("synthetic", "synthetic", BASE)
        client._places_by_index = {0: PLACE}
        client.s = Session(
            gets=[
                Response(grid, BASE + "/ConsumptionPlaceList.aspx"),
                Response(main, MAIN),
            ],
            posts=[selection, Response(PROFILE_HTML, PROFILE)],
        )
        client.select_consumption_place(0)
        self.assertTrue(client._context_verified)
        self.assertEqual(client.s.calls[1][1], BASE + "/Select.aspx")
        self.assertEqual(dict(client.s.calls[1][2]["data"])["filter"], "keep")
        self.assertEqual(client.open_profile_data()[1], PROFILE)
        self.assertEqual(
            [call[0] for call in client.s.calls], ["GET", "POST", "GET", "POST"]
        )

    def test_unverified_direct_profile_is_not_accepted(self):
        client = client_with_main(
            "<form>" + FILTER + EXPORT + "</form>", url=PROFILE, verified=False
        )
        with self.assertRaises(VSChrudimError):
            client.open_profile_data()
        self.assertEqual(client.s.calls, [])


class EmptyProfileTests(unittest.TestCase):
    """Izolace místa bez měřidla při skutečném průchodu navigačním klientem."""

    @staticmethod
    def client_for_profiles(profiles):
        """Smyšlená místa mají vlastní kontext; HTTP i CSV zůstávají bez sítě.

        Každé otevření profilu spotřebuje právě jednu připravenou odpověď.
        Seznam downloaded zachycuje pořadí míst, u nichž se opravdu četlo CSV;
        neobsahuje účty, cookies ani skutečné identifikátory.
        """
        places = [
            {
                **PLACE,
                "index": index,
                "adresa": f"Smyšlené místo {index + 1}",
                "evidencni_cislo": f"A-{index}",
                "technicke_cislo": f"T-{index}",
            }
            for index in range(len(profiles))
        ]
        responses = []
        for index, response in enumerate(profiles):
            place = places[index]
            html = response.text.replace("A-123", place["evidencni_cislo"])
            html = html.replace("T-456", place["technicke_cislo"])
            responses.append(Response(html, response.url, response.status_code))
        client = VSChrudimClient("synthetic", "synthetic", BASE)
        client.s = Session(gets=responses)
        client.login = lambda: None
        client.list_consumption_places = lambda: places
        selected, downloaded = [0], []

        def select(index):
            selected[0] = index
            client._selected_place = places[index]
            html = CONTEXT.replace("A-123", places[index]["evidencni_cislo"])
            html = html.replace("T-456", places[index]["technicke_cislo"])
            client._selected_response = Response(html, MAIN)
            client._context_verified = True

        def download(href, profile_url):
            downloaded.append(selected[0])
            return "MERIDLO;CAS;STAV\nX;01.01.2026 00:00:00;1,25\n"

        client.select_consumption_place = select
        client._download_csv_text = download
        return client, downloaded

    @staticmethod
    def empty_profile():
        """Prázdný select značí absenci voleb, nikoli nulový stav vodoměru."""
        points = '<select name="ctl$edMeteringPoint"></select>'
        return PROFILE_HTML.replace(EXPORT, points)

    def test_valid_first_place_survives_empty_second_profile(self):
        """Druhé místo bez měřidla nesmí zahodit už načtené první místo."""
        client, downloaded = self.client_for_profiles(
            [Response(PROFILE_HTML, PROFILE), Response(self.empty_profile(), PROFILE)]
        )
        result = client.get_latest_readings("M")
        self.assertEqual(list(result), ["T-0"])
        self.assertEqual(result["T-0"]["value"], 1.25)
        self.assertEqual(downloaded, [0])

    def test_empty_first_profile_does_not_block_valid_second_place(self):
        """Průchod musí pokračovat i při opačném pořadí dostupných odečtů."""
        client, downloaded = self.client_for_profiles(
            [Response(self.empty_profile(), PROFILE), Response(PROFILE_HTML, PROFILE)]
        )
        result = client.get_latest_readings("M")
        self.assertEqual(list(result), ["T-1"])
        self.assertEqual(result["T-1"]["value"], 1.25)
        self.assertEqual(downloaded, [1])

    def test_all_profiles_without_meters_still_fail(self):
        """Účet bez odečtů nesmí potvrdit úspěšné naplnění prázdné historie."""
        client, downloaded = self.client_for_profiles(
            [Response(self.empty_profile(), PROFILE)] * 2
        )
        with self.assertRaisesRegex(
            VSChrudimError, "Portál nevrátil použitelné odečty žádného místa"
        ):
            client.get_latest_readings("M")
        self.assertEqual(downloaded, [])

    def test_year_history_requests_only_valid_place_profile(self):
        """První roční backfill přepíná filtr jen místu s dostupným měřidlem."""
        for valid_index in (0, 1):
            with self.subTest(valid_index=valid_index):
                profiles = [Response(self.empty_profile(), PROFILE)] * 2
                profiles[valid_index] = Response(PROFILE_HTML, PROFILE)
                client, downloaded = self.client_for_profiles(profiles)
                year_html = PROFILE_HTML.replace("A-123", f"A-{valid_index}")
                year_html = year_html.replace("T-456", f"T-{valid_index}")
                client.s.posts = [Response(year_html, PROFILE)]
                result = client.get_latest_readings("Y")
                self.assertEqual(list(result), [f"T-{valid_index}"])
                self.assertEqual(downloaded, [valid_index])
                self.assertEqual([call[0] for call in client.s.calls].count("POST"), 1)

    def test_empty_profile_without_ids_uses_fresh_verified_selection(self):
        """Skutečný portál může na profilu vynechat ID ověřené na MainInfo."""
        for valid_index in (0, 1):
            with self.subTest(valid_index=valid_index):
                no_ids = self.empty_profile().replace(CONTEXT, "")
                profiles = [Response(no_ids, PROFILE)] * 2
                profiles[valid_index] = Response(PROFILE_HTML, PROFILE)
                client, downloaded = self.client_for_profiles(profiles)
                result = client.get_latest_readings("M")
                self.assertEqual(list(result), [f"T-{valid_index}"])
                self.assertEqual(downloaded, [valid_index])

    def test_empty_profile_without_any_verified_context_is_rejected(self):
        """Prázdné kontrolky samy nesmějí potvrdit neověřenou identitu."""
        no_ids = self.empty_profile().replace(CONTEXT, "")
        client = client_with_main(no_ids, url=PROFILE, verified=False)
        with self.assertRaises(VSChrudimError) as error:
            client.open_profile_data()
        self.assertNotIsInstance(error.exception, VSChrudimNoReadingsError)
        self.assertEqual(client.s.calls, [])

    def test_new_selection_clears_previous_verified_context_before_failure(self):
        """Kontext předchozího místa nelze převzít po selhání další volby."""
        client = client_with_main(PROFILE_HTML, url=PROFILE, verified=True)
        with self.assertRaises(VSChrudimError):
            client.select_consumption_place(1)
        self.assertFalse(client._context_verified)
        self.assertIsNone(client._selected_place)
        self.assertIsNone(client._selected_response)

    def test_empty_profile_does_not_hide_unknown_or_mismatched_pages(self):
        """Pouhá absence odkazu nestačí; chybný kontext či struktura dál selže."""
        empty = self.empty_profile()
        cases = {
            "cizí identita": Response(empty.replace("A-123", "OTHER"), PROFILE),
            "chybějící filtr": Response(empty.replace(FILTER, ""), PROFILE),
            "chybějící select měřidla": Response(
                empty.replace('<select name="ctl$edMeteringPoint"></select>', ""),
                PROFILE,
            ),
            "existující volba měřidla": Response(
                empty.replace(
                    '<select name="ctl$edMeteringPoint"></select>',
                    '<select name="ctl$edMeteringPoint"><option value="X">'
                    "Měřidlo</option></select>",
                ),
                PROFILE,
            ),
            "pouze prázdná volba": Response(
                empty.replace(
                    '<select name="ctl$edMeteringPoint"></select>',
                    '<select name="ctl$edMeteringPoint"><option value="">'
                    "Vyberte</option></select>",
                ),
                PROFILE,
            ),
            "duplicitní výběr měřidla": Response(
                empty.replace(
                    "</form>",
                    '<select name="other$edMeteringPoint"></select></form>',
                ),
                PROFILE,
            ),
            "profil bez formuláře": Response(
                empty.replace('<form action="./ProfileData.aspx">', "").replace(
                    "</form>", ""
                ),
                PROFILE,
            ),
            "filtr bez měsíční volby": Response(
                empty.replace('<option value="M" selected>M</option>', ""),
                PROFILE,
            ),
            "jiná stránka": Response(empty, MAIN),
            "neznámé HTML": Response("<html>Chyba aplikace</html>", PROFILE),
            "filtr v jiném formuláři": Response(
                empty.replace(FILTER, "") + "<form>" + FILTER + "</form>", PROFILE
            ),
            "cizí cíl formuláře": Response(
                empty.replace("./ProfileData.aspx", "https://elsewhere.invalid/"),
                PROFILE,
            ),
            "odlišný cíl formuláře": Response(
                empty.replace("./ProfileData.aspx", "./MainInfo.aspx"), PROFILE
            ),
            "jiný server": Response(
                empty, "https://elsewhere.invalid/UserData/ProfileData.aspx"
            ),
        }
        for name, response in cases.items():
            with self.subTest(case=name):
                client, downloaded = self.client_for_profiles(
                    [Response(PROFILE_HTML, PROFILE), response]
                )
                with self.assertRaises(VSChrudimError) as error:
                    client.get_latest_readings("M")
                self.assertNotIsInstance(error.exception, VSChrudimNoReadingsError)
                self.assertEqual(downloaded, [0])

    def test_empty_profile_does_not_hide_expired_session(self):
        """Přihlašovací stránka má přednost před známkami prázdného profilu."""
        client, downloaded = self.client_for_profiles(
            [
                Response(PROFILE_HTML, PROFILE),
                Response(self.empty_profile() + '<input type="password">', PROFILE),
            ]
        )
        with self.assertRaises(VSChrudimAuthError):
            client.get_latest_readings("M")
        self.assertEqual(downloaded, [0])

    def test_empty_profile_does_not_hide_failed_http(self):
        """Ani profil se správnou strukturou nesmí zamaskovat HTTP selhání."""
        for status in (401, 403, 500, 503):
            with self.subTest(status=status):
                client, downloaded = self.client_for_profiles(
                    [
                        Response(PROFILE_HTML, PROFILE),
                        Response(self.empty_profile(), PROFILE, status=status),
                    ]
                )
                with self.assertRaises(requests.HTTPError):
                    client.get_latest_readings("M")
                self.assertEqual(downloaded, [0])

    def test_timeout_after_valid_place_is_not_reported_as_success(self):
        """Síťová chyba dalšího místa se nesmí změnit v částečný úspěch."""
        client, downloaded = self.client_for_profiles(
            [Response(PROFILE_HTML, PROFILE), Response(self.empty_profile(), PROFILE)]
        )
        original_get = client.s.get

        def get(url, **kwargs):
            if len(client.s.gets) == 1:
                raise requests.Timeout("Syntetický timeout bez skutečné URL")
            return original_get(url, **kwargs)

        client.s.get = get
        with self.assertRaises(requests.Timeout):
            client.get_latest_readings("M")
        self.assertEqual(downloaded, [0])

    def test_direct_empty_profile_needs_no_second_request(self):
        """Již otevřený prázdný profil se ověří bez opakovaného GET."""
        client = client_with_main(self.empty_profile(), url=PROFILE)
        with self.assertRaises(VSChrudimNoReadingsError):
            client.open_profile_data()
        self.assertEqual(client.s.calls, [])

    def test_empty_profile_after_postback_is_recognized(self):
        """Stejné rozlišení platí i po původním WebForms postbacku."""
        main = (
            '<form action="./MainInfo.aspx">'
            + CONTEXT
            + '<button name="menu$profile" type="submit">Naměřené stavy</button>'
            + "</form>"
        )
        client = client_with_main(main, posts=[Response(self.empty_profile(), PROFILE)])
        with self.assertRaises(VSChrudimNoReadingsError):
            client.open_profile_data()
        self.assertEqual([call[0] for call in client.s.calls], ["POST"])

    def test_existing_export_is_preserved_even_with_empty_selector(self):
        """Existující export zachová původní cestu stahování a CSV parser."""
        client, downloaded = self.client_for_profiles(
            [
                Response(PROFILE_HTML, PROFILE),
                Response(
                    self.empty_profile().replace("</form>", EXPORT + "</form>"), PROFILE
                ),
            ]
        )
        result = client.get_latest_readings("M")
        self.assertEqual(list(result), ["T-0", "T-1"])
        self.assertEqual(downloaded, [0, 1])

    def test_profile_diagnostics_do_not_return_private_values(self):
        """Libovolný obsah a query se nesmějí promítnout do logovacího slovníku."""
        html = self.empty_profile() + '<input value="private@example.invalid">'
        result = profile_diagnostics(html, PROFILE + "?secret=not-for-logs")
        self.assertEqual(result["meter_options"], [0])
        self.assertTrue(result["month_option"])
        self.assertTrue(result["same_form"])
        serialized = str(result)
        for private in (
            "private@example.invalid",
            "not-for-logs",
            "A-123",
            "T-456",
            "synthetic.invalid",
            "<input",
        ):
            self.assertNotIn(private, serialized)

    def test_skipped_profile_log_contains_only_ordinal(self):
        """Nový log nesmí obsahovat identifikátory míst ani obsah odpovědi."""
        client, _ = self.client_for_profiles(
            [Response(PROFILE_HTML, PROFILE), Response(self.empty_profile(), PROFILE)]
        )
        with self.assertLogs("vschrudim_test.vschrudim_client", level="DEBUG") as logs:
            client.get_latest_readings("M")
        logged = "\n".join(logs.output)
        self.assertIn("pořadí 2 z 2", logged)
        for forbidden in ("A-1", "T-1", "Smyšlené místo", "synthetic", "<select"):
            self.assertNotIn(forbidden, logged)


if __name__ == "__main__":
    unittest.main()
