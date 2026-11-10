"""Update notice (stage 1): asks GitHub for the latest release and tells the user.

Nothing is downloaded or run: the window and the tray only show "Nova versão X.Y.Z
disponível" and a link to the release page; installing stays manual. Off by default (opt-in):
with the option off, no request is ever made.
"""

from __future__ import annotations

import json
import logging
import queue
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import __version__
from .system import MAX_VERSION_DIGITS

log = logging.getLogger(__name__)

# GitHub repository ("owner/name") whose releases announce new versions. Change it here if
# the project moves; everything below is derived from it.
UPDATE_REPO = "daniloporfirio/fervura"
API_URL = f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{UPDATE_REPO}/releases/"
LATEST_PAGE_URL = RELEASES_URL + "latest"

USER_AGENT = f"Fervura/{__version__}"  # generic: no e-mail, user or machine data
TIMEOUT = 10  # seconds, per network operation
MAX_RESPONSE_BYTES = 1024 * 1024  # the real answer has a few KiB
MAX_URL_LENGTH = 2048
FIRST_CHECK_DELAY = 30  # seconds after start
CHECK_INTERVAL = 24 * 3600  # then once a day while the program stays open
MIN_CHECK_SPACING = 60  # turning the option off and on again does not repeat the request
_MAX_WAIT = 60  # the thread wakes at least this often to look at the clock

_TAG = re.compile(r"v?([0-9]{1,%d})\.([0-9]{1,%d})\.([0-9]{1,%d})" % ((MAX_VERSION_DIGITS,) * 3))
# what may follow RELEASES_URL in html_url (e.g. "tag/v1.2.0"); no "%" (an encoded "." could
# form a ".." segment), no spaces, quotes or other characters a shell could care about
_URL_TAIL = re.compile(r"[A-Za-z0-9._~+/-]*")


def parse_tag(tag) -> tuple[int, int, int] | None:
    """'v1.2.3' or '1.2.3' -> (1, 2, 3). Exactly three parts of ASCII digits (up to
    MAX_VERSION_DIGITS each); anything else (suffixes, '٢', '２', spaces...) gives None."""
    if not isinstance(tag, str):
        return None
    match = _TAG.fullmatch(tag)
    return None if match is None else tuple(int(g) for g in match.groups())


CURRENT_VERSION = parse_tag(__version__)


def version_text(version: tuple[int, int, int]) -> str:
    return ".".join(map(str, version))


def page_url(url) -> str:
    """html_url of the release if it is a page of UPDATE_REPO's releases; otherwise the
    generic "latest release" page of the same repository."""
    if isinstance(url, str) and len(url) <= MAX_URL_LENGTH and url.startswith(RELEASES_URL):
        tail = url[len(RELEASES_URL):]
        if _URL_TAIL.fullmatch(tail) and ".." not in tail.split("/"):
            return url
    return LATEST_PAGE_URL


@dataclass(frozen=True)
class Release:
    version: tuple[int, int, int]
    url: str

    @property
    def text(self) -> str:
        return version_text(self.version)


def parse_release(raw: bytes) -> Release | None:
    """Release announced by the API answer. None when there is nothing to offer (draft,
    prerelease, missing or invalid tag); ValueError when the answer itself is unusable."""
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("response larger than 1 MiB")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):  # RecursionError: deeply nested JSON
        raise ValueError("invalid JSON") from None
    if not isinstance(data, dict):
        raise ValueError("response is not a JSON object")
    # /releases/latest never returns these, but if one comes, it is not offered
    if data.get("draft", False) is not False or data.get("prerelease", False) is not False:
        log.debug("ignoring release: draft or prerelease")
        return None
    version = parse_tag(data.get("tag_name"))
    if version is None:
        log.debug("ignoring release: invalid tag %.40r", data.get("tag_name"))
        return None
    return Release(version, page_url(data.get("html_url")))


def _https_context() -> ssl.SSLContext:
    """Certificate and host name always verified, TLS 1.2 or newer.

    On Windows the trusted certificates come from the Windows certificate store (ROOT and CA,
    only those marked for server authentication) instead of SSL_CERT_FILE/SSL_CERT_DIR, which
    the default context would honor and this elevated process inherits from the normal user's
    environment. ssl.enum_certificates also returns the CurrentUser stores, which the normal
    user can write to: this protects against the network and the proxy, but not against code
    already running in the user's account (it can add a root and intercept the answer).
    Acceptable in stage 1 because the opened page is pinned to UPDATE_REPO, the tag is
    validated and nothing is run; stage 2 must trust the downloaded installer through its
    Authenticode signature, never through TLS alone."""
    if sys.platform != "win32":
        return ssl.create_default_context()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # CERT_REQUIRED and check_hostname
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    server_auth = ssl.Purpose.SERVER_AUTH.oid
    for store in ("ROOT", "CA"):
        certs = bytearray()
        try:
            for cert, encoding, trust in ssl.enum_certificates(store):
                if encoding == "x509_asn" and (trust is True or server_auth in trust):
                    certs.extend(cert)
        except OSError as e:
            log.warning("could not read the %s certificate store: %s", store, e)
        if certs:
            ctx.load_verify_locations(cadata=bytes(certs))
    return ctx


class _GitHubOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only to another HTTPS address of the GitHub API."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith("https://api.github.com/"):
            raise urllib.error.HTTPError(req.full_url, code, "redirect outside api.github.com",
                                         headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _default_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=_https_context()),
                                       _GitHubOnlyRedirect())


def fetch_latest(opener) -> bytes:
    """Raw answer of the "latest release" API, at most MAX_RESPONSE_BYTES. Raises urllib
    errors, OSError (timeout, network) or ValueError (unexpected answer)."""
    request = urllib.request.Request(API_URL, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with opener.open(request, timeout=TIMEOUT) as response:
        status = getattr(response, "status", 200)
        if status != 200:
            raise ValueError(f"unexpected HTTP status {status}")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("response larger than 1 MiB")
    return raw


_UNSET = object()


class UpdateChecker(threading.Thread):
    """Background thread: checks FIRST_CHECK_DELAY s after start and then every CHECK_INTERVAL
    while enabled; turning it on checks right away (at most once per MIN_CHECK_SPACING).
    Each successful check puts the newer Release, or None, in `results`. Failures are only
    logged, once per state, and retried at the next scheduled check."""

    def __init__(self, enabled: bool, current: tuple[int, int, int] | None = CURRENT_VERSION,
                 opener=None, first_delay: float = FIRST_CHECK_DELAY,
                 interval: float = CHECK_INTERVAL, min_spacing: float = MIN_CHECK_SPACING):
        super().__init__(daemon=True, name="update-check")
        self.results: queue.Queue = queue.Queue()
        self.current = current or (0, 0, 0)
        self.first_delay, self.interval, self.min_spacing = first_delay, interval, min_spacing
        self._opener = opener
        self._lock = threading.Lock()
        self._enabled = enabled
        self._check_requested = False
        self._wake = threading.Event()
        self._stop_event = threading.Event()
        self._last_request: float | None = None
        self._last_problem: str | None = None  # avoids repeating the same warning every day
        self._announced = _UNSET

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = enabled
            self._check_requested = enabled
        self._wake.set()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake.set()

    def run(self) -> None:
        next_check = time.monotonic() + self.first_delay
        while not self._stop_event.is_set():
            with self._lock:
                enabled, requested = self._enabled, self._check_requested
                self._check_requested = False
            now = time.monotonic()
            if requested:
                earliest = now if self._last_request is None else self._last_request + self.min_spacing
                next_check = min(next_check, max(now, earliest))
            if enabled and now >= next_check:
                self._last_request = now
                self.check_once()
                next_check = time.monotonic() + self.interval
                continue
            self._wake.wait(min(max(0.0, next_check - now), _MAX_WAIT) if enabled else None)
            self._wake.clear()

    def _report(self, message: str) -> None:
        """Logs a problem once, until it changes or a check succeeds."""
        if message != self._last_problem:
            log.warning("%s", message)
            self._last_problem = message

    def check_once(self) -> None:
        """One request; never raises."""
        try:
            release = parse_release(fetch_latest(self._opener or _default_opener()))
        except urllib.error.HTTPError as e:  # 404 while the repository has no release, 403 rate limit
            e.close()
            self._report(f"update check failed: HTTP {e.code}")
            return
        except urllib.error.URLError as e:
            self._report(f"update check failed: {e.reason}")
            return
        except Exception as e:  # timeout, network, TLS, invalid answer: never kills the thread
            self._report(f"update check failed: {e}")
            return
        if self._last_problem is not None:
            log.info("update check working again")
            self._last_problem = None
        newer = release if release is not None and release.version > self.current else None
        key = None if newer is None else newer.version
        if key != self._announced:
            if newer is None:
                log.info("no update available (current %s)", version_text(self.current))
            else:
                log.info("update available: %s (%s)", newer.text, newer.url)
            self._announced = key
        self.results.put(newer)
