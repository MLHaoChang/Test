"""The injectable HTTP layer (plan 5.4).

Every outbound request in this app -- Stooq, the ECB, OpenFIGI -- goes through one `HttpClient`
built once, from `Settings`, by `make_http_client` in `client.py`. No data client ever builds its
own network client, so the whole test suite can run with real sockets disabled
(`tests/http/test_no_network.py`) while still exercising every client against committed fixtures,
through `ReplayHttpClient` (`replay.py`). `RecordingHttpClient` (`record.py`) is the third mode:
it wraps the real network client and writes what it saw to a directory, so a real response can
become a new fixture without hand-writing one.
"""
