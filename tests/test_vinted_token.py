"""Tests parsing token Vinted → storage_state."""

import pytest

from vinted_bot.services.vinted_token import (
    VintedTokenError,
    parse_vinted_token_to_storage_state,
)


def test_parse_raw_access_token() -> None:
    state = parse_vinted_token_to_storage_state("abc123tokenvalue")
    cookie = state["cookies"][0]
    assert cookie["name"] == "access_token_web"
    assert cookie["value"] == "abc123tokenvalue"
    assert cookie["domain"] == ".vinted.fr"


def test_parse_cookie_line() -> None:
    state = parse_vinted_token_to_storage_state("access_token_web=secret_jwt_here")
    assert state["cookies"][0]["value"] == "secret_jwt_here"


def test_parse_storage_state_json() -> None:
    raw = (
        '{"cookies":[{"name":"access_token_web","value":"x","domain":".vinted.fr"}],'
        '"origins":[]}'
    )
    state = parse_vinted_token_to_storage_state(raw)
    assert state["cookies"][0]["value"] == "x"


def test_parse_refresh_jwt_uses_refresh_cookie_name() -> None:
    import base64
    import json

    payload = base64.urlsafe_b64encode(
        json.dumps({"purpose": "refresh"}).encode()
    ).decode().rstrip("=")
    token = f"aaa.{payload}.bbb"
    state = parse_vinted_token_to_storage_state(token)
    assert state["cookies"][0]["name"] == "refresh_token_web"


def test_parse_empty_raises() -> None:
    with pytest.raises(VintedTokenError):
        parse_vinted_token_to_storage_state("   ")
