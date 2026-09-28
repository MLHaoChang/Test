"""`HttpRequest`, `HttpResponse`, the `HttpClient` protocol, and the real network client (plan 5.4).

`HttpRequest` and `HttpResponse` are plain dataclasses, independent of any HTTP library, so
`ReplayHttpClient` and `RecordingHttpClient` (and every test) can build and inspect them without
importing `httpx`. `NetworkHttpClient` is the one class in the app that does import `httpx`
(plan 3.1); nothing under `tests/` ever constructs one with a real transport, since the whole
suite runs with sockets disabled (`tests/http/test_no_network.py`).
"""

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

import httpx

import playground
from playground.config import Settings
from playground.core.errors import PlaygroundError

#: plan 5.4: "20 s timeout, 3 retries with backoff on 5xx and timeouts, 1 request per second
#: per host, User-Agent 'playground/<version>'".
DEFAULT_TIMEOUT_SECONDS = 20.0
MAX_RETRIES = 3
MIN_SECONDS_BETWEEN_REQUESTS_PER_HOST = 1.0
_RETRY_STATUS_CODES = frozenset({500, 502, 503, 504})
_BACKOFF_BASE_SECONDS = 0.5


class HttpConfigurationError(PlaygroundError):
    """`Settings.http_mode` needs a directory (`--http-replay` or `--http-record`) that is missing."""


class NoResponseError(PlaygroundError):
    """A request got no response at all, so there is no status to look at.

    `NetworkError` when the network could not be reached; `replay.UnrecordedRequestError` when,
    in replay mode, nothing was recorded for the request. Each data client turns it into its own
    "source unavailable" error, with a message that says what to do instead.
    """


class NetworkError(NoResponseError):
    """The connection failed, a proxy refused it, or no answer came in time, even after the retries.

    `host` is the host that could not be reached, and `detail` says why in plain words, for example
    "the connection failed ([Errno 111] Connection refused)". The message is "Could not reach
    <host>: <detail>."
    """

    def __init__(self, host: str, detail: str) -> None:
        super().__init__(f"Could not reach {host}: {detail}.")
        self.host = host
        self.detail = detail


@dataclass(frozen=True)
class HttpRequest:
    """One outbound HTTP request, independent of any HTTP library (plan 5.4)."""

    method: str
    url: str
    params: Mapping[str, str] = field(default_factory=dict)
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes | None = None


@dataclass(frozen=True)
class HttpResponse:
    """One HTTP response, independent of any HTTP library (plan 5.4)."""

    status: int
    headers: Mapping[str, str]
    body: bytes


class HttpClient(Protocol):
    """Something that can send an `HttpRequest` and return an `HttpResponse`."""

    def send(self, request: HttpRequest) -> HttpResponse:
        """Send `request` and return its response. Never raises for a non-2xx status.

        Raises `NoResponseError` when no response arrives at all.
        """
        ...


class NetworkHttpClient:
    """Sends requests over the real network, through `httpx` (plan 5.4).

    This is the only class in the whole app that imports `httpx` (3.1); every data client takes
    an `HttpClient` and never builds one itself. A 5xx status or a timeout is retried up to
    `MAX_RETRIES` times with a short exponential backoff; requests to the same host are spaced at
    least `MIN_SECONDS_BETWEEN_REQUESTS_PER_HOST` apart. A request that gets no response (a
    failed connection, a proxy that refuses it, no answer after the retries) raises `NetworkError`
    with a plain message, never an `httpx` exception. A failed connection is not retried: the
    same connection a moment later fails the same way. `transport` is a testing seam
    (`httpx.MockTransport`): it lets a test exercise the retry and throttling logic entirely
    in-process, with no real socket, so it works under `pytest-socket`.
    """

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._client = httpx.Client(timeout=timeout, transport=transport)
        self._timeout = timeout
        self._sleep = sleep if sleep is not None else time.sleep
        self._last_request_monotonic: dict[str, float] = {}

    def send(self, request: HttpRequest) -> HttpResponse:
        host = httpx.URL(request.url).host
        # Throttled once per logical request, not per retry attempt: the backoff sleep below
        # already spaces retries out (0.5 s, 1 s, 2 s), and this is what keeps two *different*
        # requests to the same host at least a second apart.
        self._throttle(host)
        headers = {"User-Agent": f"playground/{playground.__version__}", **request.headers}
        last_error: httpx.TimeoutException | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self._client.request(
                    request.method, request.url, params=dict(request.params), headers=headers, content=request.body
                )
            except httpx.TimeoutException as exc:
                last_error = exc
            except httpx.RequestError as exc:
                raise NetworkError(host, _failure_detail(exc)) from exc
            else:
                if response.status_code not in _RETRY_STATUS_CODES or attempt == MAX_RETRIES:
                    return HttpResponse(
                        status=response.status_code, headers=dict(response.headers), body=response.content
                    )
                last_error = None
            if attempt < MAX_RETRIES:
                self._sleep(_BACKOFF_BASE_SECONDS * (2**attempt))
        if last_error is not None:
            raise NetworkError(
                host, f"no answer within {self._timeout:g} seconds, {MAX_RETRIES + 1} times"
            ) from last_error
        raise RuntimeError("NetworkHttpClient.send: exhausted retries without a response.")  # pragma: no cover

    def _throttle(self, host: str) -> None:
        last = self._last_request_monotonic.get(host)
        now = time.monotonic()
        if last is not None:
            wait = MIN_SECONDS_BETWEEN_REQUESTS_PER_HOST - (now - last)
            if wait > 0:
                self._sleep(wait)
        self._last_request_monotonic[host] = time.monotonic()


def _failure_detail(exc: httpx.RequestError) -> str:
    """Why a request got no response, in plain words, with httpx's own text in brackets."""
    text = str(exc).strip()
    if isinstance(exc, httpx.ProxyError):
        what = "the proxy refused the connection"
    elif isinstance(exc, httpx.ConnectError):
        what = "the connection failed"
    else:
        what = "the request failed"
    return f"{what} ({text})" if text else what


def make_http_client(settings: Settings) -> HttpClient:
    """Build the `HttpClient` `settings.http_mode` calls for (plan 5.4).

    `network` (the default) returns a plain `NetworkHttpClient`; `replay` and `record` need
    `settings.http_replay_dir` / `settings.http_record_dir` to be set (the CLI's `--http-replay`
    and `--http-record` options, or `PG_HTTP_REPLAY` / `PG_HTTP_RECORD`) and raise
    `HttpConfigurationError` with a plain-English message otherwise.
    """
    # Imported here, not at module level, so that importing this module never requires the
    # replay/record modules to exist yet -- handy while the three files are built in one commit,
    # and it keeps a plain "network mode" import light.
    from playground.http.record import RecordingHttpClient
    from playground.http.replay import ReplayHttpClient

    if settings.http_mode == "network":
        return NetworkHttpClient()
    if settings.http_mode == "replay":
        if settings.http_replay_dir is None:
            raise HttpConfigurationError("http_mode is 'replay' but no --http-replay directory was given.")
        return ReplayHttpClient(settings.http_replay_dir)
    if settings.http_mode == "record":
        if settings.http_record_dir is None:
            raise HttpConfigurationError("http_mode is 'record' but no --http-record directory was given.")
        return RecordingHttpClient(NetworkHttpClient(), settings.http_record_dir)
    raise HttpConfigurationError(f"Unknown http_mode: {settings.http_mode!r}.")  # pragma: no cover
