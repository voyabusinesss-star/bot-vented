"""Tests Whop webhooks + mapping produits / signature."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from types import SimpleNamespace

from vinted_bot.db.user_filters import plan_filter_limit
from vinted_bot.interactions.handlers import _private_filters_gate_message
from vinted_bot.services.whop_webhooks import (
    extract_discord_user_id,
    extract_product_id,
    handle_whop_event,
    parse_whop_envelope,
    plan_for_product_id,
    product_plan_map,
    roles_for_plan,
    verify_whop_signature,
)


def test_plan_limits_match_marketing() -> None:
    assert plan_filter_limit("starter") == 0
    assert plan_filter_limit("premium") == 10
    assert plan_filter_limit("elite") == 30


def test_product_plan_map() -> None:
    settings = SimpleNamespace(
        whop_product_starter="prod_starter",
        whop_product_pro="prod_pro",
        whop_product_proplus="prod_plus",
    )
    assert product_plan_map(settings) == {
        "prod_starter": "starter",
        "prod_pro": "premium",
        "prod_plus": "elite",
    }
    assert plan_for_product_id("prod_pro", settings=settings) == "premium"
    assert plan_for_product_id("prod_unknown", settings=settings) is None


def test_verify_whop_signature_ok() -> None:
    secret = "test_whop_secret"
    body = b'{"type":"membership.activated","data":{}}'
    msg_id = "msg_123"
    ts = str(int(time.time()))
    signed = f"{msg_id}.{ts}.".encode() + body
    digest = base64.b64encode(
        hmac.new(secret.encode(), signed, hashlib.sha256).digest()
    ).decode()
    headers = {
        "webhook-id": msg_id,
        "webhook-timestamp": ts,
        "webhook-signature": f"v1,{digest}",
    }
    assert verify_whop_signature(body, headers, secret) is True
    assert verify_whop_signature(body, headers, "wrong") is False


def test_extract_discord_and_product() -> None:
    data = {
        "id": "mem_abc",
        "product": {"id": "prod_pro"},
        "metadata": {"discord_id": "123456789012345678"},
    }
    assert extract_product_id(data) == "prod_pro"
    assert extract_discord_user_id(data) == 123456789012345678


def test_parse_whop_envelope() -> None:
    raw = json.dumps(
        {
            "type": "membership.activated",
            "data": {"id": "mem_1", "product": {"id": "prod_x"}},
        }
    ).encode()
    event_type, data = parse_whop_envelope(raw)
    assert event_type == "membership.activated"
    assert data["id"] == "mem_1"


def test_verify_whop_signature_ws_prefix() -> None:
    secret = "ws_" + "ab" * 32
    body = b'{"type":"membership_activated","data":{}}'
    msg_id = "msg_ws"
    ts = str(int(time.time()))
    key = bytes.fromhex("ab" * 32)
    signed = f"{msg_id}.{ts}.".encode("utf-8") + body
    digest = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    headers = {
        "webhook-id": msg_id,
        "webhook-timestamp": ts,
        "webhook-signature": f"v1,{digest}",
    }
    assert verify_whop_signature(body, headers, secret) is True


def test_effective_whop_webhook_port_prefers_railway_port() -> None:
    from vinted_bot.config import Settings

    local = Settings(whop_webhook_port=8788, port=None)
    assert local.effective_whop_webhook_port() == 8788
    railway = Settings(whop_webhook_port=8788, port=8080)
    assert railway.effective_whop_webhook_port() == 8080


def test_database_url_railway_prefix() -> None:
    from vinted_bot.config import Settings

    s = Settings(database_url="postgresql://u:p@host:5432/db")
    assert s.database_url.startswith("postgresql+psycopg://")
    s2 = Settings(database_url="postgres://u:p@host:5432/db")
    assert s2.database_url.startswith("postgresql+psycopg://")


def test_handle_membership_activated_underscore(monkeypatch) -> None:
    settings = SimpleNamespace(
        whop_product_starter="prod_starter",
        whop_product_pro="",
        whop_product_proplus="",
        discord_role_sub_starter="111",
        discord_role_sub_pro="",
        discord_role_sub_proplus="",
        discord_role_resello_vip="",
        discord_guild_id="2",
        discord_bot_token="",
    )
    called = {"n": 0}

    def fake_activate(**kw):
        called["n"] += 1

    monkeypatch.setattr(
        "vinted_bot.services.whop_webhooks.activate_subscription",
        fake_activate,
    )
    status = handle_whop_event(
        "membership_activated",
        {
            "id": "mem_1",
            "product": {"id": "prod_starter"},
            "metadata": {"discord_id": "99"},
        },
        settings=settings,
    )
    assert status == "activated"
    assert called["n"] == 1


def test_handle_unknown_product(monkeypatch) -> None:
    settings = SimpleNamespace(
        whop_product_starter="",
        whop_product_pro="prod_pro",
        whop_product_proplus="",
        discord_role_resello_vip="1",
        discord_guild_id="2",
        discord_bot_token="",
    )
    status = handle_whop_event(
        "membership.activated",
        {
            "id": "mem_1",
            "product": {"id": "prod_other"},
            "metadata": {"discord_id": "99"},
        },
        settings=settings,
    )
    assert status == "unknown_product"


def test_handle_pending_discord(monkeypatch) -> None:
    settings = SimpleNamespace(
        whop_product_starter="",
        whop_product_pro="prod_pro",
        whop_product_proplus="",
        discord_role_resello_vip="1",
        discord_guild_id="2",
        discord_bot_token="",
    )
    status = handle_whop_event(
        "membership.activated",
        {"id": "mem_pending", "product": {"id": "prod_pro"}},
        settings=settings,
    )
    assert status == "pending_discord"


def test_roles_for_plan_stacked() -> None:
    settings = SimpleNamespace(
        discord_role_sub_starter="111",
        discord_role_sub_pro="222",
        discord_role_sub_proplus="333",
        discord_role_resello_vip="999",
    )
    assert roles_for_plan("starter", settings) == ["111"]
    assert roles_for_plan("premium", settings) == ["111", "222"]
    assert roles_for_plan("elite", settings) == ["111", "222", "333"]
    assert roles_for_plan("pro", settings) == ["111", "222"]


def test_roles_for_plan_vip_fallback() -> None:
    settings = SimpleNamespace(
        discord_role_sub_starter="",
        discord_role_sub_pro="",
        discord_role_sub_proplus="",
        discord_role_resello_vip="999",
    )
    assert roles_for_plan("elite", settings) == ["999"]


def test_plan_from_pro_role_is_premium_limit_10() -> None:
    from vinted_bot.db.user_filters import plan_filter_limit
    from vinted_bot.interactions.handlers import _private_filters_gate_message

    assert plan_filter_limit("premium") == 10
    assert plan_filter_limit("pro") == 10
    assert (
        _private_filters_gate_message(
            plan="premium",
            limit=10,
            subscription_active=True,
            has_vip=True,
        )
        is None
    )
    assert (
        _private_filters_gate_message(
            plan="starter",
            limit=0,
            subscription_active=False,
            has_vip=False,
        )
        is not None
    )
    assert "Starter" in (
        _private_filters_gate_message(
            plan="starter",
            limit=0,
            subscription_active=True,
            has_vip=True,
        )
        or ""
    )
    assert (
        _private_filters_gate_message(
            plan="premium",
            limit=10,
            subscription_active=False,
            has_vip=True,
        )
        is None
    )
