"""Update notice (stage 1): version parsing, GitHub answer, scheduling, window and tray.
No real network: the opener is always a fake."""

import io
import json
import logging
import os
import queue
import re
import sys
import threading
import types
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import fervura
from fervura import app, shellexec, updates
from fervura.updates import Release, UpdateChecker, parse_release, parse_tag

PROJECT = Path(__file__).resolve().parent.parent
REPO_PAGE = f"https://github.com/{updates.UPDATE_REPO}/releases/"


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, status: int = 200):
        super().__init__(body)
        self.status = status
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


class FakeOpener:
    """Stands in for urllib's opener: answers from a list (bytes, FakeResponse or exception)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []
        self.responses = []

    def open(self, request, timeout=None):
        self.requests.append((request, timeout))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, BaseException):
            raise answer
        response = answer if isinstance(answer, FakeResponse) else FakeResponse(answer)
        self.responses.append(response)
        return response


def body(tag="v1.2.0", url=None, **extra) -> bytes:
    data = {"tag_name": tag, "html_url": url or REPO_PAGE + f"tag/{tag}", **extra}
    return json.dumps(data).encode()


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(updates.API_URL, code, "error", {}, io.BytesIO(b""))


class VersionTest(unittest.TestCase):
    def test_accepted_tags(self):
        cases = {"v1.2.3": (1, 2, 3), "1.2.3": (1, 2, 3), "v0.0.1": (0, 0, 1),
                 "10.20.30": (10, 20, 30), "123456789.0.0": (123456789, 0, 0)}
        for tag, expected in cases.items():
            with self.subTest(tag=tag):
                self.assertEqual(parse_tag(tag), expected)

    def test_invalid_tags(self):
        for tag in ("", "v", "1", "1.2", "1.2.3.4", "1.2.3-beta", "v1.2.3rc1", " 1.2.3",
                    "1.2.3 ", "1.2.3\n", "V1.2.3", "vv1.2.3", "release-1.2.3", "1..3", "1.2.x",
                    "-1.2.3", "+1.2.3"):
            with self.subTest(tag=tag):
                self.assertIsNone(parse_tag(tag))

    def test_unicode_digits_are_refused(self):
        for tag in ("٢.٠.٠", "１.２.３", "1.².3", "v1.2.٣", "1.2.3\u200b"):
            with self.subTest(tag=tag):
                self.assertIsNone(parse_tag(tag))

    def test_too_many_digits_without_exception(self):
        for tag in ("1234567890.0.0", "1.0." + "9" * 10, "9" * 5000 + ".0.0", "1." * 5000 + "1"):
            with self.subTest(tag=tag[:12]):
                self.assertIsNone(parse_tag(tag))

    def test_non_text_tags(self):
        for tag in (None, 1, 1.2, b"1.2.3", ["1.2.3"], {"v": "1.2.3"}, True):
            with self.subTest(tag=tag):
                self.assertIsNone(parse_tag(tag))

    def test_numeric_comparison(self):
        checker = UpdateChecker(True, current=(1, 9, 0), opener=FakeOpener(body("v1.10.0")))
        checker.check_once()
        self.assertEqual(checker.results.get_nowait().version, (1, 10, 0))  # not "1.10" < "1.9"
        for tag in ("v1.9.0", "1.8.99", "v0.99.99"):  # same or older: no update
            with self.subTest(tag=tag):
                checker = UpdateChecker(True, current=(1, 9, 0), opener=FakeOpener(body(tag)))
                checker.check_once()
                self.assertIsNone(checker.results.get_nowait())
        checker = UpdateChecker(True, current=(1, 9, 0), opener=FakeOpener(body("v2.0.0-beta")))
        checker.check_once()
        self.assertIsNone(checker.results.get_nowait())  # invalid tag = no update


class SingleVersionTest(unittest.TestCase):
    """fervura.__version__ is the only version: app, update check and installer use it."""

    def test_version_format(self):
        self.assertRegex(fervura.__version__, r"\A[0-9]+\.[0-9]+\.[0-9]+\Z")
        self.assertEqual(updates.CURRENT_VERSION, parse_tag(fervura.__version__))
        self.assertIsNotNone(updates.CURRENT_VERSION)
        self.assertIs(app.__version__, fervura.__version__)
        self.assertEqual(updates.USER_AGENT, f"Fervura/{fervura.__version__}")

    def test_build_passes_the_version_to_inno_setup(self):
        bat = (PROJECT / "build.bat").read_bytes()
        self.assertNotIn(b"\n", bat.replace(b"\r\n", b""), "build.bat must keep CRLF")
        text = bat.decode("ascii")
        self.assertIn("fervura.__version__", text)
        self.assertRegex(text, r'"%ISCC%" /DAppVersion=%APPVER% installer\\Fervura\.iss')

    def test_installer_takes_the_version_from_the_build(self):
        raw = (PROJECT / "installer" / "Fervura.iss").read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), "Fervura.iss must keep the UTF-8 BOM")
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""), "Fervura.iss must keep CRLF")
        text = raw.decode("utf-8-sig")
        self.assertRegex(text, r"#ifndef AppVersion\r\n\s*#define AppVersion \"[^\"]+\"\r\n#endif")
        self.assertEqual(len(re.findall(r"#define AppVersion", text)), 1)  # only the fallback
        self.assertNotIn(f'"{fervura.__version__}"', text)  # never hard-coded again
        self.assertIn("AppVersion={#AppVersion}", text)
        self.assertIn("OutputBaseFilename=Fervura-Setup-{#AppVersion}", text)
        self.assertIn("AppId={{49026729-6B1B-48E0-BE60-D5E0C6352593}", text)

    def test_build_runs_every_test_file(self):
        text = (PROJECT / "build.bat").read_text(encoding="ascii")
        line = next(l for l in text.splitlines() if "-m unittest" in l)
        for path in sorted((PROJECT / "tests").glob("test_*.py")):
            with self.subTest(file=path.name):
                self.assertIn(f"tests\\{path.name}", line)


class PageUrlTest(unittest.TestCase):
    def test_urls_come_from_the_repo_constant(self):
        repo = updates.UPDATE_REPO
        self.assertEqual(updates.API_URL, f"https://api.github.com/repos/{repo}/releases/latest")
        self.assertEqual(updates.LATEST_PAGE_URL, f"https://github.com/{repo}/releases/latest")

    def test_release_page_of_the_repo_is_accepted(self):
        for url in (REPO_PAGE + "tag/v1.2.0", REPO_PAGE + "latest", REPO_PAGE + "tag/1.2.0"):
            with self.subTest(url=url):
                self.assertEqual(updates.page_url(url), url)

    def test_anything_else_falls_back_to_latest_page(self):
        repo = updates.UPDATE_REPO
        for url in (f"http://github.com/{repo}/releases/tag/v1.2.0",
                    f"https://github.com/{repo}-evil/releases/tag/v1.2.0",
                    f"https://github.com/{repo}/releasesx/tag/v1.2.0",
                    f"https://github.com.evil.example/{repo}/releases/tag/v1.2.0",
                    f"https://github.com/outro/repo/releases/tag/v1.2.0",
                    f"https://evil.example/?https://github.com/{repo}/releases/",
                    REPO_PAGE + "../../../outro/repo", REPO_PAGE + "tag/%2e%2e/%2e%2e/x",
                    REPO_PAGE + "tag/v1 2", REPO_PAGE + 'tag/"x"', REPO_PAGE + "tag/v1\n",
                    REPO_PAGE + "tag/a&calc", REPO_PAGE + "tag/" + "a" * 3000,
                    REPO_PAGE.upper(), "", None, 1, ["x"]):
            with self.subTest(url=str(url)[:60]):
                self.assertEqual(updates.page_url(url), updates.LATEST_PAGE_URL)


class ParseReleaseTest(unittest.TestCase):
    def test_valid_answer(self):
        release = parse_release(body("v1.2.0"))
        self.assertEqual(release, Release((1, 2, 0), REPO_PAGE + "tag/v1.2.0"))
        self.assertEqual(release.text, "1.2.0")

    def test_unusable_answers_raise_value_error(self):
        for raw in (b"{lixo", b"", b"\xff\xfe", b"[" * 100000, b'{"a":' * 100000,
                    b"1" * 5000):
            with self.subTest(raw=raw[:8]):
                with self.assertRaises(ValueError):
                    parse_release(raw)

    def test_non_object_json(self):
        for raw in (b"[]", b"null", b'"v1.2.0"', b"123", b"true", b'["v1.2.0"]'):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    parse_release(raw)

    def test_larger_than_limit(self):
        raw = body("v9.9.9", pad="x" * updates.MAX_RESPONSE_BYTES)
        with self.assertRaises(ValueError):
            parse_release(raw)

    def test_missing_or_wrong_fields_give_no_update(self):
        for data in ({}, {"html_url": REPO_PAGE + "tag/v1.2.0"}, {"tag_name": None},
                     {"tag_name": 120}, {"tag_name": ["v1.2.0"]}, {"tag_name": {"x": 1}},
                     {"tag_name": "nightly"}):
            with self.subTest(data=data):
                self.assertIsNone(parse_release(json.dumps(data).encode()))

    def test_bad_html_url_uses_latest_page(self):
        for url in (None, 1, "https://evil.example/x", "javascript:alert(1)"):
            with self.subTest(url=url):
                raw = json.dumps({"tag_name": "v1.2.0", "html_url": url}).encode()
                self.assertEqual(parse_release(raw).url, updates.LATEST_PAGE_URL)
        self.assertEqual(parse_release(b'{"tag_name": "v1.2.0"}').url, updates.LATEST_PAGE_URL)

    def test_draft_and_prerelease_are_ignored(self):
        for extra in ({"draft": True}, {"prerelease": True}, {"draft": "yes"},
                      {"prerelease": 1}, {"draft": None}):
            with self.subTest(extra=extra):
                self.assertIsNone(parse_release(body("v9.0.0", **extra)))
        release = parse_release(body("v9.0.0", draft=False, prerelease=False))
        self.assertEqual(release.version, (9, 0, 0))


class CheckOnceTest(unittest.TestCase):
    def warnings(self, logs):
        return [r.getMessage() for r in logs.records if r.levelno == logging.WARNING]

    def test_request_is_https_with_generic_headers(self):
        opener = FakeOpener(body("v1.2.0"))
        UpdateChecker(True, current=(1, 0, 0), opener=opener).check_once()
        (request, timeout), = opener.requests
        self.assertEqual(request.full_url, updates.API_URL)
        self.assertTrue(request.full_url.startswith("https://api.github.com/"))
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertEqual(timeout, updates.TIMEOUT)
        headers = {k.lower(): v for k, v in request.header_items()}
        self.assertEqual(headers["user-agent"], f"Fervura/{fervura.__version__}")
        self.assertEqual(headers["accept"], "application/vnd.github+json")
        self.assertEqual(set(headers), {"user-agent", "accept", "x-github-api-version"})
        self.assertNotIn("@", headers["user-agent"])

    def test_newer_version_is_published(self):
        checker = UpdateChecker(True, current=(1, 0, 0), opener=FakeOpener(body("v1.2.0")))
        with self.assertLogs("fervura.updates", logging.INFO) as logs:
            checker.check_once()
        self.assertEqual(checker.results.get_nowait(), Release((1, 2, 0), REPO_PAGE + "tag/v1.2.0"))
        self.assertTrue(any("update available: 1.2.0" in m for m in logs.output))

    def test_reads_at_most_the_limit(self):
        big = FakeResponse(body("v1.2.0", pad="x" * (2 * updates.MAX_RESPONSE_BYTES)))
        checker = UpdateChecker(True, current=(1, 0, 0), opener=FakeOpener(big))
        with self.assertLogs("fervura.updates", logging.WARNING) as logs:
            checker.check_once()
        self.assertEqual(big.read_sizes, [updates.MAX_RESPONSE_BYTES + 1])
        self.assertTrue(checker.results.empty())
        self.assertIn("1 MiB", self.warnings(logs)[0])

    def test_network_errors_never_raise(self):
        for error in (http_error(404), http_error(403), http_error(500),
                      urllib.error.URLError("getaddrinfo failed"), TimeoutError("timed out"),
                      ConnectionResetError("reset"), OSError("ssl"), RuntimeError("odd"),
                      FakeResponse(b"{lixo"), FakeResponse(b"[]"), FakeResponse(b"", status=204)):
            with self.subTest(error=repr(error)[:40]):
                checker = UpdateChecker(True, current=(1, 0, 0), opener=FakeOpener(error))
                with self.assertLogs("fervura.updates", logging.WARNING):
                    checker.check_once()  # must not raise
                self.assertTrue(checker.results.empty())  # nothing shown to the user

    def test_failures_logged_once_per_state(self):
        opener = FakeOpener(http_error(404), http_error(404), http_error(404), http_error(403),
                            http_error(403), body("v1.0.0"), http_error(404))
        checker = UpdateChecker(True, current=(1, 0, 0), opener=opener)
        with self.assertLogs("fervura.updates", logging.INFO) as logs:
            for _ in range(7):
                checker.check_once()
        self.assertEqual(self.warnings(logs), ["update check failed: HTTP 404",
                                               "update check failed: HTTP 403",
                                               "update check failed: HTTP 404"])
        self.assertTrue(any("working again" in m for m in logs.output))
        self.assertEqual(len(opener.requests), 7)

    def test_same_result_logged_once(self):
        checker = UpdateChecker(True, current=(1, 0, 0), opener=FakeOpener(body("v1.2.0")))
        with self.assertLogs("fervura.updates", logging.INFO) as logs:
            for _ in range(3):
                checker.check_once()
        self.assertEqual(sum("update available" in m for m in logs.output), 1)
        self.assertEqual(checker.results.qsize(), 3)


class SchedulingTest(unittest.TestCase):
    def start(self, checker):
        checker.start()
        self.addCleanup(lambda: (checker.stop(), checker.join(3)))
        return checker

    def test_disabled_makes_no_request(self):
        opener = FakeOpener(body("v9.0.0"))
        checker = self.start(UpdateChecker(False, opener=opener, first_delay=0, interval=0.01))
        with self.assertRaises(queue.Empty):
            checker.results.get(timeout=0.3)
        self.assertEqual(opener.requests, [])

    def test_turning_on_checks_right_away(self):
        opener = FakeOpener(body("v9.0.0"))
        checker = self.start(UpdateChecker(False, opener=opener, first_delay=3600))
        checker.set_enabled(True)
        self.assertEqual(checker.results.get(timeout=3).version, (9, 0, 0))
        # off and on again right away: no new request before MIN_CHECK_SPACING
        checker.set_enabled(False)
        checker.set_enabled(True)
        with self.assertRaises(queue.Empty):
            checker.results.get(timeout=0.3)
        self.assertEqual(len(opener.requests), 1)

    def test_first_check_after_delay_then_daily(self):
        opener = FakeOpener(body("v9.0.0"))
        checker = self.start(UpdateChecker(True, opener=opener, first_delay=0.05))
        self.assertEqual(checker.results.get(timeout=3).version, (9, 0, 0))
        with self.assertRaises(queue.Empty):  # next one only after CHECK_INTERVAL
            checker.results.get(timeout=0.3)
        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(updates.FIRST_CHECK_DELAY, 30)
        self.assertEqual(updates.CHECK_INTERVAL, 24 * 3600)

    def test_no_request_before_first_delay(self):
        opener = FakeOpener(body("v9.0.0"))
        self.start(UpdateChecker(True, opener=opener, first_delay=3600))
        threading.Event().wait(0.2)
        self.assertEqual(opener.requests, [])

    def test_stop_ends_the_thread(self):
        checker = UpdateChecker(False, opener=FakeOpener(body()))
        checker.start()
        checker.stop()
        checker.join(3)
        self.assertFalse(checker.is_alive())
        self.assertTrue(checker.daemon)


class TransportTest(unittest.TestCase):
    def test_redirect_only_to_the_github_api(self):
        handler = updates._GitHubOnlyRedirect()
        request = urllib.request.Request(updates.API_URL)
        for url in ("http://api.github.com/x", "https://evil.example/", "https://github.com/x",
                    "https://api.github.com.evil.example/"):
            with self.subTest(url=url):
                with self.assertRaises(urllib.error.HTTPError):
                    handler.redirect_request(request, io.BytesIO(), 301, "Moved", {}, url)
        moved = handler.redirect_request(request, io.BytesIO(), 301, "Moved", {},
                                         "https://api.github.com/repositories/1/releases/latest")
        self.assertEqual(moved.full_url, "https://api.github.com/repositories/1/releases/latest")

    @unittest.skipUnless(sys.platform == "win32", "Windows certificate store")
    def test_certificates_verified_with_the_windows_store_only(self):
        with mock.patch.object(updates.ssl, "create_default_context",
                               side_effect=AssertionError("would honor SSL_CERT_FILE")), \
                mock.patch.dict(os.environ, {"SSL_CERT_FILE": r"C:\nao\existe.pem"}):
            ctx = updates._https_context()
        self.assertEqual(ctx.verify_mode, updates.ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)
        self.assertGreater(len(ctx.get_ca_certs()), 0)


class ShellExecTest(unittest.TestCase):
    def test_safe_urls(self):
        self.assertTrue(shellexec.is_safe_url(REPO_PAGE + "tag/v1.2.0"))
        self.assertTrue(shellexec.is_safe_url(updates.LATEST_PAGE_URL))
        for url in ("http://github.com/x", "file:///C:/Windows/System32/calc.exe", "calc.exe",
                    "https://x/ y", 'https://x/"', "https://x/\n", "https://x/%2e", "https://x/|a",
                    "https://x/" + "a" * 3000, "", None, 1):
            with self.subTest(url=url):
                self.assertFalse(shellexec.is_safe_url(url))

    def run_open(self, url):
        done = queue.Queue()
        with mock.patch.object(shellexec, "shell_execute_unelevated") as run:
            shellexec.open_url(url, done.put)
            result = done.get(timeout=3)
        return run, result

    def test_unsafe_url_is_never_executed(self):
        run, error = self.run_open("file:///C:/Windows/System32/calc.exe")
        run.assert_not_called()
        self.assertIsNotNone(error)

    def test_safe_url_goes_to_the_desktop_shell(self):
        run, error = self.run_open(updates.LATEST_PAGE_URL)
        run.assert_called_once_with(updates.LATEST_PAGE_URL)
        self.assertIsNone(error)

    def test_failure_is_reported_not_raised(self):
        done = queue.Queue()
        with mock.patch.object(shellexec, "shell_execute_unelevated",
                               side_effect=OSError("Explorer elevado")):
            shellexec.open_url(updates.LATEST_PAGE_URL, done.put)
            self.assertEqual(done.get(timeout=3), "Explorer elevado")

    def test_no_elevated_fallback_in_the_code(self):
        """Never webbrowser, os.startfile, ShellExecuteW or explorer.exe (they would inherit
        the elevated token)."""
        source = Path(shellexec.__file__).read_text(encoding="utf-8")
        code = "\n".join(l for l in source.splitlines() if not l.lstrip().startswith("#"))
        code = re.sub(r'"""[\s\S]*?"""', "", code)
        for name in ("webbrowser", "startfile", "ShellExecuteW", "ShellExecuteExW",
                     "explorer.exe", "subprocess"):
            with self.subTest(name=name):
                self.assertNotIn(name, code)
        self.assertIn("CLSCTX_LOCAL_SERVER", code)


class AppUpdateTest(unittest.TestCase):
    """Window and tray side, without Tk."""

    def make_app(self, check_updates=True):
        a = app.App.__new__(app.App)
        a.conf = {"source": "cpu", "check_updates": check_updates}
        a.save_problem = ""
        a.release = None
        a.events = queue.Queue()
        a.checker = mock.Mock(results=queue.Queue())
        a.check_updates_var, a.status_var, a.update_var = mock.Mock(), mock.Mock(), mock.Mock()
        a.update_frame = mock.Mock()
        a.root = mock.Mock()
        a.show = mock.Mock()
        return a

    def test_update_text_and_tray_tip(self):
        release = Release((1, 2, 0), updates.LATEST_PAGE_URL)
        self.assertEqual(app.update_text(release), "Nova versão 1.2.0 disponível")
        self.assertIsNone(app.update_text(None))
        self.assertEqual(app.tray_tip(54, "conectado em COM3", None), "Fervura: 54\nconectado em COM3")
        self.assertEqual(app.tray_tip(None, "x", release), "Fervura: --\nNova versão 1.2.0 disponível\nx")
        self.assertEqual(len(app.tray_tip(54, "s" * 300, release)), 127)
        self.assertIn("Nova versão 1.2.0", app.tray_tip(54, "s" * 300, release))

    def test_result_shown_only_with_option_on(self):
        release = Release((1, 2, 0), updates.LATEST_PAGE_URL)
        a = self.make_app(check_updates=True)
        a.checker.results.put(None)
        a.checker.results.put(release)
        a.poll_updates()
        self.assertEqual(a.release, release)
        a.update_var.set.assert_called_with("Nova versão 1.2.0 disponível")
        a.update_frame.grid.assert_called()

        a = self.make_app(check_updates=False)
        a.checker.results.put(release)  # answer of a request made before turning it off
        a.poll_updates()
        self.assertIsNone(a.release)

    def test_turning_off_hides_the_notice_and_saves(self):
        a = self.make_app(check_updates=True)
        a.release = Release((1, 2, 0), updates.LATEST_PAGE_URL)
        with mock.patch.object(app.settings, "save") as save:
            a.set_check_updates(False)
        a.checker.set_enabled.assert_called_once_with(False)
        self.assertIsNone(a.release)
        a.update_frame.grid_remove.assert_called()
        save.assert_called_once_with({"source": "cpu", "check_updates": False})
        with mock.patch.object(app.settings, "save"):
            a.set_check_updates(True)
        a.checker.set_enabled.assert_called_with(True)
        self.assertTrue(a.conf["check_updates"])

    def test_open_failure_shows_copy_link(self):
        a = self.make_app()
        release = Release((1, 2, 0), REPO_PAGE + "tag/v1.2.0")
        a.release = release
        with mock.patch.object(shellexec, "open_url",
                               side_effect=lambda url, done: done("Explorer elevado")) as open_url:
            a.open_release_page()
        open_url.assert_called_once()
        self.assertEqual(open_url.call_args.args[0], release.url)
        with self.assertLogs("fervura.app", logging.WARNING):
            a.events.get_nowait()()  # runs on the Tk thread in the real program
        a.update_var.set.assert_called_with(f"Nova versão 1.2.0 disponível — {app.OPEN_PAGE_ERROR}")
        a.show.assert_called_once()

    def test_copy_link(self):
        a = self.make_app()
        a.release = Release((1, 2, 0), REPO_PAGE + "tag/v1.2.0")
        a.copy_release_link()
        a.root.clipboard_append.assert_called_once_with(REPO_PAGE + "tag/v1.2.0")


class TrayUpdateItemTest(unittest.TestCase):
    def test_menu_item_follows_the_notice(self):
        items = []

        class MenuItem:
            def __init__(self, text, action, **kw):
                self.text, self.action, self.kw = text, action, kw
                items.append(self)

        pystray = types.ModuleType("pystray")
        pystray.MenuItem = MenuItem
        pystray.Menu = mock.Mock()
        pystray.Menu.SEPARATOR = None
        pystray.Icon = mock.Mock()
        pil = types.ModuleType("PIL")
        pil.Image, pil.ImageDraw, pil.ImageFont = mock.Mock(), mock.Mock(), mock.Mock()
        saved = sys.modules.pop("fervura.tray", None)
        self.addCleanup(lambda: sys.modules.__setitem__("fervura.tray", saved) if saved
                        else sys.modules.pop("fervura.tray", None))
        with mock.patch.dict(sys.modules, {"pystray": pystray, "PIL": pil}):
            import fervura.tray as tray
            notice = {"text": None}
            on_update = mock.Mock()
            tray.Tray(on_open=mock.Mock(), on_quit=mock.Mock(), get_source=lambda: "cpu",
                      set_source=mock.Mock(), get_update=lambda: notice["text"],
                      on_update=on_update)
        item = next(i for i in items if callable(i.text))
        self.assertFalse(item.kw["visible"](item))
        notice["text"] = "Nova versão 1.2.0 disponível"
        self.assertTrue(item.kw["visible"](item))
        self.assertEqual(item.text(item), "Nova versão 1.2.0 disponível")
        item.action()
        on_update.assert_called_once()


if __name__ == "__main__":
    unittest.main()
