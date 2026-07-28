"""Tests parsing profil Vinted (checkout livraison)."""

from vinted_bot.services.vinted_profile import (
    CAPTCHA_HELP_MESSAGE,
    VintedProfileInfo,
    _carrier_display_name,
    _filter_pickup_only,
    _is_captcha_url,
    _is_home_delivery_label,
    _is_junk_pickup_label,
    _is_valid_purchase_id,
    _purchase_id_from_payload,
    build_profile_incomplete_message,
    parse_checkout_profile_data,
    profile_is_complete,
    profile_missing_fields,
)


CHECKOUT_FIXTURE = {
    "checkout": {
        "components": {
            "shipping_address": {
                "address": {
                    "name": "Faustin Lamotte",
                    "line1": "24 rue Francois bisiaux",
                    "postal_code": "59176",
                    "city": "Écaillon",
                    "entry_type": 1,
                },
                "shipping_order_id": 24077963350,
            },
            "shipping_pickup_details": {
                "shipping_order_id": 24077963350,
                "receiver_address": {
                    "name": "Faustin Lamotte",
                    "line1": "24 rue Francois bisiaux",
                    "postal_code": "59176",
                    "city": "Écaillon",
                },
                "pickup_types": {
                    "pickup": {
                        "shipping_options": [
                            {
                                "rate_uuid": "rate-mondial",
                                "title": "Mondial Relay",
                                "carrier_code": "MONDIALRELAY-FR",
                                "shipping_point": {
                                    "name": "BAR A QUI",
                                    "address_line1": "12 rue de la gare",
                                    "postal_code": "59176",
                                    "city": "Écaillon",
                                },
                            },
                            {
                                "rate_uuid": "rate-chrono",
                                "title": "Chronopost",
                                "carrier_code": "CHRONOPOST-FR",
                                "shipping_point": {
                                    "name": "LOCKER 24/7 ELECLERC 59146 PECQ",
                                    "address_line1": "ZAC",
                                    "postal_code": "59146",
                                    "city": "PECQ",
                                },
                            },
                            {
                                "rate_uuid": "rate-vgo",
                                "title": "Vinted Go",
                                "carrier_code": "VINTEDGO-SHOP-FR",
                                "shipping_point": {
                                    "name": "LOCKER 24/7 LIDL",
                                    "address_line1": "4 Place Roger Salengro",
                                    "postal_code": "59125",
                                    "city": "Trith Saint Leger",
                                },
                            },
                        ]
                    }
                },
            },
        }
    }
}


def test_carrier_display_name() -> None:
    assert _carrier_display_name(code="MONDIALRELAY-FR", title="") == "Mondial Relay"
    assert _carrier_display_name(code="CHRONOPOST-FR", title="") == "Chronopost"
    assert _carrier_display_name(code="VINTEDGO-SHOP-FR", title="Vinted Go") == "Vinted Go"


def test_purchase_id_validation() -> None:
    assert not _is_valid_purchase_id("checkout")
    assert not _is_valid_purchase_id("")
    assert _is_valid_purchase_id("24077963350")
    assert _is_valid_purchase_id("quRRydJEtZ3J0ZWpahusz")
    assert _purchase_id_from_payload({"checkout": {"purchase": {"id": 24077963350}}}) == "24077963350"


def test_captcha_url_detection() -> None:
    assert _is_captcha_url(
        "https://geo.captcha-delivery.com/captcha/?initialCid=abc"
    )
    assert not _is_captcha_url("https://www.vinted.fr/checkout?purchase_id=abc")
    assert "anti-robot" in CAPTCHA_HELP_MESSAGE.lower()


def test_parse_checkout_profile_data_plug_style_names() -> None:
    address, points = parse_checkout_profile_data(CHECKOUT_FIXTURE)
    assert address is not None
    assert "Faustin Lamotte" in address
    assert points == [
        "BAR A QUI",
        "LOCKER 24/7 ELECLERC 59146 PECQ",
        "LOCKER 24/7 LIDL",
    ]
    assert not any(point.lower().startswith("domicile") for point in points)
    assert not any("mondial relay" in point.lower() for point in points)


def test_filter_excludes_home_and_junk() -> None:
    assert _is_home_delivery_label("Domicile — Envoi à domicile")
    assert _is_junk_pickup_label("Vinted Go — À propos de Vinted")
    assert _is_junk_pickup_label("Vinted Go — Vinted Pro")
    assert _is_junk_pickup_label("Vinted Go — Vinted")
    filtered = _filter_pickup_only(
        [
            "Domicile — Envoi à domicile",
            "BAR A QUI",
            "Vinted Go — À propos de Vinted",
            "Point relais — Depuis un relais",
        ]
    )
    assert filtered == ["BAR A QUI"]


def test_profile_completeness() -> None:
    complete = VintedProfileInfo(
        address="24 rue",
        payment_label="Carte **** 4895",
    )
    assert profile_is_complete(complete)
    assert profile_missing_fields(complete) == []

    incomplete = VintedProfileInfo(address="24 rue")
    assert not profile_is_complete(incomplete)
    assert "moyen de paiement" in profile_missing_fields(incomplete)

    expired = VintedProfileInfo()
    msg = build_profile_incomplete_message(expired)
    assert "Session Vinted expirée" in msg
    assert "Reconnecte ta session Vinted" in msg
