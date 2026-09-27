"""OpenFIGI ISIN mapping suggestions (plan 5.6, 6.7).

`suggest` is read-only and prints only: nothing it returns is ever stored, and nothing runs it but
`pg instruments suggest ISIN` -- the only command that sends an ISIN anywhere outbound (5.6, 6.7;
spec 3.10 limits outbound requests to tickers and dates, and an ISIN identifies a security, not
a person). The suggested Stooq symbol is a convenience guess from the exchange code; you still map
the instrument yourself with `pg instruments map` (nothing is applied for you, plan 5.6).
"""

import json
from dataclasses import dataclass
from typing import Any

from playground.core.errors import PlaygroundError
from playground.http.client import HttpClient, HttpRequest

DEFAULT_URL = "https://api.openfigi.com/v3/mapping"
API_KEY_HEADER = "X-OPENFIGI-APIKEY"
API_KEY_ENV_VAR = "OPENFIGI_API_KEY"

#: Listing exchange code (OpenFIGI's `exchCode`) to the Stooq suffix it suggests (6.7).
_EXCHANGE_CODE_TO_STOOQ_SUFFIX = {"GY": "de", "GR": "de", "US": "us", "LN": "uk"}


class OpenFigiError(PlaygroundError):
    """OpenFIGI refused the request (for example, rate-limited) or returned something unreadable."""


@dataclass(frozen=True)
class ListingSuggestion:
    """One OpenFIGI listing for an ISIN: "suggestion, not applied" (6.7)."""

    ticker: str | None
    exchange_code: str | None
    name: str | None
    security_type: str | None

    @property
    def stooq_symbol(self) -> str | None:
        """A possible Stooq symbol for this listing, or `None` when the exchange code is not one of 6.7's."""
        if not self.ticker or not self.exchange_code:
            return None
        suffix = _EXCHANGE_CODE_TO_STOOQ_SUFFIX.get(self.exchange_code)
        return f"{self.ticker.lower()}.{suffix}" if suffix else None


@dataclass(frozen=True)
class OpenFigiClient:
    http: HttpClient
    api_key: str | None = None
    url: str = DEFAULT_URL

    def suggest(self, isin: str) -> list[ListingSuggestion]:
        """Ask OpenFIGI for the listings of `isin`. Stores nothing; the caller only ever prints this."""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers[API_KEY_HEADER] = self.api_key
        body = json.dumps([{"idType": "ID_ISIN", "idValue": isin}]).encode("utf-8")

        response = self.http.send(HttpRequest(method="POST", url=self.url, headers=headers, body=body))
        if response.status == 429:
            raise OpenFigiError("OpenFIGI rate-limited this request. Wait a while, then try again.")
        if response.status != 200:
            raise OpenFigiError(f"OpenFIGI returned status {response.status} for {isin}.")

        try:
            payload = json.loads(response.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise OpenFigiError(f"OpenFIGI's response for {isin} was not readable JSON.") from exc
        if not isinstance(payload, list) or not payload:
            raise OpenFigiError(f"OpenFIGI's response for {isin} had no result.")

        result: dict[str, Any] = payload[0]
        if "error" in result:
            return []
        return [
            ListingSuggestion(
                ticker=item.get("ticker"),
                exchange_code=item.get("exchCode"),
                name=item.get("name"),
                security_type=item.get("securityType"),
            )
            for item in result.get("data", [])
        ]
