"""`RecordingHttpClient`: wraps the real network client and writes what it saw (plan 5.4).

Selected with `--http-record DIR` (`PG_HTTP_RECORD`), this is how a real Stooq, ECB or OpenFIGI
response becomes a fixture `ReplayHttpClient` can serve later (6.4: "recording one real response
... gives us a real fixture"), without ever hand-writing `index.yaml`. Every request still goes
out for real (this class only wraps `NetworkHttpClient`, it never replaces it); only what gets
written to disk has its secrets removed, so a recorded directory is safe to commit or share.
Recorded directories live under the data directory, which is `.gitignore`d (plan 2.4), but the
secret-stripping happens regardless of where they end up.
"""

from pathlib import Path
from typing import Any

import yaml

from playground.http.client import HttpClient, HttpRequest, HttpResponse
from playground.http.replay import INDEX_FILE_NAME

#: Query parameter names never written to a recorded index.yaml (case-insensitive).
SECRET_PARAM_NAMES = frozenset({"token", "apikey", "api_key"})
#: Header names never written to a recorded index.yaml (case-insensitive).
SECRET_HEADER_NAMES = frozenset({"authorization", "x-openfigi-apikey", "x-api-key"})

_EXTENSION_BY_CONTENT_TYPE = {
    "application/json": ".json",
    "application/zip": ".zip",
    "text/csv": ".csv",
    "text/plain": ".txt",
    "text/html": ".html",
}


def _strip_secret_params(params: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in params.items() if key.lower() not in SECRET_PARAM_NAMES}


def _strip_secret_headers(headers: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in headers.items() if key.lower() not in SECRET_HEADER_NAMES}


def _extension_for(response: HttpResponse) -> str:
    content_type = next((v for k, v in response.headers.items() if k.lower() == "content-type"), "")
    base_type = content_type.split(";", 1)[0].strip().lower()
    return _EXTENSION_BY_CONTENT_TYPE.get(base_type, ".bin")


class RecordingHttpClient:
    """Sends every request through `inner` for real, and records the response under `directory`.

    Appends to `directory/index.yaml` if it already holds recordings, rather than starting over,
    so `--http-record` can be run more than once into the same directory.
    """

    def __init__(self, inner: HttpClient, directory: Path) -> None:
        self._inner = inner
        self._directory = Path(directory)
        self._directory.mkdir(parents=True, exist_ok=True)
        self._index_path = self._directory / INDEX_FILE_NAME
        self._entries: list[dict[str, Any]] = self._read_existing_entries()

    def _read_existing_entries(self) -> list[dict[str, Any]]:
        if not self._index_path.is_file():
            return []
        payload = yaml.safe_load(self._index_path.read_text(encoding="utf-8")) or {}
        return list(payload.get("requests") or [])

    def send(self, request: HttpRequest) -> HttpResponse:
        response = self._inner.send(request)

        sequence = len(self._entries) + 1
        filename = f"recording-{sequence:04d}{_extension_for(response)}"
        (self._directory / filename).write_bytes(response.body)

        entry: dict[str, Any] = {
            "method": request.method.upper(),
            "url": request.url,
            "query": _strip_secret_params(dict(request.params)),
            "response": {
                "status": response.status,
                "file": filename,
                "headers": dict(response.headers),
            },
        }
        if request.body:
            entry["body"] = request.body.decode("utf-8", errors="replace")
        # A stripped header carries no information a fixture needs to match on later, so it is
        # dropped from the recorded request entirely rather than kept with an empty value.
        recorded_headers = _strip_secret_headers(dict(request.headers))
        if recorded_headers:
            entry["headers"] = recorded_headers

        self._entries.append(entry)
        self._index_path.write_text(
            yaml.safe_dump({"requests": self._entries}, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        return response
