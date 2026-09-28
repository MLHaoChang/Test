"""`ReplayHttpClient`: serves recorded responses from `<dir>/index.yaml` (plan 5.4).

Every test in this repository runs against this client (never `NetworkHttpClient`), pointed at
committed fixtures, so the whole suite works with real sockets disabled
(`tests/http/test_no_network.py`). The CLI's `--http-replay DIR` option (`PG_HTTP_REPLAY`) uses
it too, so a real end-to-end run can be repeated offline once its responses are recorded
(`RecordingHttpClient`, `record.py`).

`index.yaml` layout (one file per replay directory, referencing sibling files by relative path)::

    requests:
      - method: GET
        url: https://stooq.com/q/d/l/
        query: {s: sap.de, d1: "20240101", d2: "20241231", i: d}
        response: {status: 200, file: stooq/sap.de.csv}
      - method: POST
        url: https://api.openfigi.com/v3/mapping
        body: '[{"idType": "ID_ISIN", "idValue": "DE0007164600"}]'
        response: {status: 200, file: openfigi/DE0007164600.json, headers: {Content-Type: application/json}}

A request matches an entry on method, URL, query parameters (as a set of key-value pairs, order
never matters) and a body hash (plan 5.4): `query` (default `{}`) must equal the request's
`params` exactly; `body`, when the entry has one, must hash to the same SHA-256 as the request's
body (a GET with no body matches an entry with no `body` key). `response.file` is a path relative
to this same directory; `response.body` is used instead for a short inline response. Nothing here
ever opens a socket.
"""

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from playground.core.errors import PlaygroundError
from playground.http.client import HttpRequest, HttpResponse, NoResponseError

INDEX_FILE_NAME = "index.yaml"


class UnrecordedRequestError(NoResponseError):
    """A request was sent to `ReplayHttpClient` that `index.yaml` has no matching entry for.

    Like a network that cannot be reached, it is a request without a response
    (`NoResponseError`), so the data clients report it the same plain way.
    """


class FixtureIndexError(PlaygroundError):
    """`index.yaml` itself is missing or malformed."""


def _body_sha256(body: bytes | None) -> str:
    return hashlib.sha256(body or b"").hexdigest()


class _RecordedRequest:
    """One parsed `requests:` entry of `index.yaml`."""

    def __init__(self, entry: Mapping[str, Any], directory: Path) -> None:
        self.method: str = str(entry["method"]).upper()
        self.url: str = str(entry["url"])
        self.query: dict[str, str] = {str(k): str(v) for k, v in (entry.get("query") or {}).items()}
        body = entry.get("body")
        self.body_sha256: str | None = _body_sha256(body.encode("utf-8")) if body is not None else None

        response = entry.get("response")
        if not isinstance(response, dict):
            raise FixtureIndexError(
                f"{directory / INDEX_FILE_NAME}: entry for {self.method} {self.url} has no response."
            )
        self.status: int = int(response.get("status", 200))
        self.headers: dict[str, str] = {str(k): str(v) for k, v in (response.get("headers") or {}).items()}
        self.file: str | None = response.get("file")
        inline = response.get("body")
        self.inline_body: bytes | None = inline.encode("utf-8") if isinstance(inline, str) else None
        if self.file is None and self.inline_body is None:
            raise FixtureIndexError(
                f"{directory / INDEX_FILE_NAME}: entry for {self.method} {self.url} has neither a file nor a body."
            )

    def matches(self, request: HttpRequest, body_hash: str) -> bool:
        if self.method != request.method.upper() or self.url != request.url:
            return False
        if self.query != {str(k): str(v) for k, v in request.params.items()}:
            return False
        if self.body_sha256 is None:
            return not request.body
        return self.body_sha256 == body_hash

    def body(self, directory: Path) -> bytes:
        if self.inline_body is not None:
            return self.inline_body
        if self.file is None:
            raise FixtureIndexError(f"{directory / INDEX_FILE_NAME}: entry for {self.method} {self.url} has no body.")
        path = directory / self.file
        if not path.is_file():
            raise FixtureIndexError(f"{INDEX_FILE_NAME} points at {self.file}, which does not exist under {directory}.")
        return path.read_bytes()


class ReplayHttpClient:
    """Serves responses recorded under `directory` (plan 5.4). Never sends a real request."""

    def __init__(self, directory: Path) -> None:
        self._directory = Path(directory)
        self._entries = self._load()

    def _load(self) -> list[_RecordedRequest]:
        index_path = self._directory / INDEX_FILE_NAME
        if not index_path.is_file():
            raise FixtureIndexError(f"No {INDEX_FILE_NAME} under {self._directory}.")
        payload = yaml.safe_load(index_path.read_text(encoding="utf-8")) or {}
        raw_entries = payload.get("requests") or []
        return [_RecordedRequest(entry, self._directory) for entry in raw_entries]

    def send(self, request: HttpRequest) -> HttpResponse:
        body_hash = _body_sha256(request.body)
        for entry in self._entries:
            if entry.matches(request, body_hash):
                return HttpResponse(status=entry.status, headers=dict(entry.headers), body=entry.body(self._directory))
        raise UnrecordedRequestError(
            f"No recorded response for {request.method} {request.url} with params "
            f"{dict(request.params)!r} under {self._directory}. Record one first with --http-record, "
            "or add a fixture to index.yaml."
        )
