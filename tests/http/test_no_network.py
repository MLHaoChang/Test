"""Tests for network isolation (plan 7.1, AC9).

`pytest-socket` disables real sockets for the whole suite (`test_socket_disabled`), and
`ReplayHttpClient` raises rather than falling back to the network when a request has no matching
fixture (`test_replay_raises_on_an_unrecorded_request`) -- exactly the two checks plan 7.1 names
for this file. The rest of the injectable HTTP layer (matching, secrets, retries) is covered in
`test_replay.py`, `test_record.py` and `test_client.py`.
"""

import socket

import pytest
from pytest_socket import SocketBlockedError

from playground.http.client import HttpRequest
from playground.http.replay import ReplayHttpClient, UnrecordedRequestError


def test_socket_disabled() -> None:
    """Test that socket creation is disabled in tests."""
    with pytest.raises(SocketBlockedError):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)


def test_replay_raises_on_an_unrecorded_request(replay_http_client: ReplayHttpClient) -> None:
    """A request with no matching `index.yaml` entry is refused, never silently sent for real."""
    with pytest.raises(UnrecordedRequestError):
        replay_http_client.send(HttpRequest(method="GET", url="https://stooq.com/q/d/l/", params={"s": "nope.de"}))
