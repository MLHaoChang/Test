"""Tests for network isolation."""

import socket

import pytest
from pytest_socket import SocketBlockedError


def test_socket_disabled() -> None:
    """Test that socket creation is disabled in tests."""
    with pytest.raises(SocketBlockedError):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)
