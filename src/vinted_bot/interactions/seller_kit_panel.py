"""Panneaux RESSELLO SELLER KIT (#emballages-expedition)."""

from __future__ import annotations

from typing import Any

EMBED_COLOR = 0x5865F2
PANEL_MARKER = "RESSELLO SELLER KIT"


def _item(title: str, detail: str, url: str, price: str = "") -> str:
    price_bit = f"\n💰 {price}" if price else ""
    return f"**• {title}**\n{detail}{price_bit}\n🔗 {url}"


def build_seller_kit_payloads() -> list[dict[str, Any]]:
    """Retourne une liste de payloads Discord (1 message = 1 partie)."""
    parts: list[tuple[str, str]] = [
        (
            "📦 RESSELLO SELLER KIT — PARTIE 1/5\nEmballage & Expédition",
            (
                "Les indispensables pour emballer et expédier tes ventes Vinted efficacement.\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "### 📮 Pochettes d'expédition\n\n"
                + _item(
                    "Lot 100 pochettes opaques 24×32 cm",
                    "Format principal : t-shirts, chemises, pantalons, vêtements légers",
                    "https://www.amazon.fr/s?k=pochettes+expedition+24x32",
                )
                + "\n\n"
                + _item(
                    "Lot 100 pochettes opaques 30×40 cm",
                    "Sweats, pulls, vestes",
                    "https://www.amazon.fr/s?k=pochettes+expedition+30x40",
                )
                + "\n\n"
                + _item(
                    "Lot 100 grandes pochettes 40×50 cm",
                    "Manteaux, grosses pièces",
                    "https://www.amazon.fr/s?k=pochettes+expedition+40x50",
                )
                + "\n\n"
                + _item(
                    "Lot assortiment plusieurs tailles",
                    "Idéal pour commencer avec plusieurs formats",
                    "https://www.amazon.fr/s?k=lot+pochettes+expedition+plusieurs+tailles",
                )
                + "\n\n### 📦 Cartons\n\n"
                + _item(
                    "Cartons chaussures",
                    "Sneakers, bottes, chaussures premium",
                    "https://www.amazon.fr/s?k=cartons+chaussures+expedition",
                )
                + "\n\n"
                + _item(
                    "Lot cartons petits formats",
                    "Accessoires, objets, petits colis",
                    "https://www.amazon.fr/s?k=cartons+expedition+petit+format",
                )
                + "\n\n"
                + _item(
                    "Lots cartons économiques",
                    "Pour vendeurs avec beaucoup d'envois",
                    "https://www.amazon.fr/s?k=lot+cartons+expedition",
                )
                + "\n\n### 🛡️ Protection & fermeture\n\n"
                + _item(
                    "Papier bulle (rouleau)",
                    "Protection chaussures et objets fragiles",
                    "https://www.amazon.fr/s?k=papier+bulle+rouleau",
                )
                + "\n\n"
                + _item(
                    "Scotch emballage (lot)",
                    "Indispensable pour fermer les colis",
                    "https://www.amazon.fr/s?k=scotch+emballage+lot+6",
                )
                + "\n\n"
                + _item(
                    "Dévidoir scotch professionnel",
                    "Gain de temps pour emballer rapidement",
                    "https://www.amazon.fr/s?k=devidoir+scotch+emballage",
                )
                + "\n\n### 🏷️ Organisation expédition\n\n"
                + _item(
                    "Étiquettes autocollantes colis",
                    "Organisation des commandes",
                    "https://www.amazon.fr/s?k=etiquettes+autocollantes+colis",
                )
                + "\n\n"
                + _item(
                    "Sacs zip transparents",
                    "Ranger accessoires et petites pièces",
                    "https://www.amazon.fr/s?k=sacs+zip+plastique",
                )
                + "\n\n### 💡 Astuce économie — cartons gratuits\n"
                "Supermarchés · Magasins de chaussures · Pharmacies · "
                "Librairies · Magasins électroménager\n\n"
                "**Plateformes :**\n"
                "• [Geev](https://www.geev.com/)\n"
                "• [Leboncoin](https://www.leboncoin.fr/)"
            ),
        ),
        (
            "🖨️ RESSELLO SELLER KIT — PARTIE 2/5\nImpression & Organisation des commandes",
            (
                "Les outils essentiels pour gagner du temps et gérer ses ventes efficacement.\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "### 🖨️ Imprimantes étiquettes\n\n"
                + _item(
                    "Imprimante thermique étiquette 4×6",
                    "Imprime les bordereaux d'expédition sans encre — idéal vendeurs réguliers",
                    "https://www.amazon.fr/s?k=imprimante+thermique+4x6",
                    "environ 50–80 €",
                )
                + "\n\n"
                + _item(
                    "Imprimante thermique Bluetooth",
                    "Impression rapide depuis le téléphone",
                    "https://www.amazon.fr/s?k=imprimante+thermique+bluetooth+etiquette",
                )
                + "\n\n"
                + _item(
                    "Rouleaux étiquettes thermiques 4×6 (500/1000)",
                    "Consommables — moins cher en gros volume",
                    "https://www.amazon.fr/s?k=etiquettes+thermiques+4x6+500",
                )
                + "\n\n"
                + _item(
                    "Étiquettes autocollantes A4",
                    "Alternative économique avec imprimante classique",
                    "https://www.amazon.fr/s?k=etiquettes+autocollantes+a4",
                )
                + "\n\n### 📋 Organisation des commandes\n\n"
                + _item(
                    "Étiqueteuse portable",
                    "Identifier les stocks, bacs et vêtements",
                    "https://www.amazon.fr/s?k=etiqueteuse+portable",
                )
                + "\n\n"
                + _item(
                    "Scanner code-barres",
                    "Gestion rapide d'un gros stock",
                    "https://www.amazon.fr/s?k=scanner+code+barre",
                )
                + "\n\n"
                + _item(
                    "Étiquettes de rangement",
                    "Classer par catégories, tailles ou références",
                    "https://www.amazon.fr/s?k=etiquettes+rangement",
                )
            ),
        ),
        (
            "✨ RESSELLO SELLER KIT — PARTIE 3/5\nNettoyage & Remise en état",
            (
                "Les indispensables pour remettre vêtements et chaussures en meilleur état avant la vente.\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "### 👔 Entretien vêtements\n\n"
                + _item(
                    "Défroisseur vapeur portable",
                    "Enlève les plis avant photos et envoi — rendu plus pro",
                    "https://www.amazon.fr/s?k=defroisseur+vapeur+portable",
                    "environ 25–50 €",
                )
                + "\n\n"
                + _item(
                    "Rouleau anti-peluches",
                    "Retire poussières, poils et résidus avant photo",
                    "https://www.amazon.fr/s?k=rouleau+anti+peluche",
                    "environ 5–10 €",
                )
                + "\n\n"
                + _item(
                    "Rasoir anti-bouloches électrique",
                    "Redonne un aspect neuf aux pulls, sweats, lainages",
                    "https://www.amazon.fr/s?k=rasoir+anti+bouloche+electrique",
                    "environ 10–25 €",
                )
                + "\n\n"
                + _item(
                    "Brosse anti-bouloches textile",
                    "Alternative manuelle",
                    "https://www.amazon.fr/s?k=brosse+anti+bouloche+vetement",
                )
                + "\n\n"
                + _item(
                    "Détachant textile",
                    "Retirer les petites taches avant mise en vente",
                    "https://www.amazon.fr/s?k=detachant+textile+vetement",
                )
                + "\n\n"
                + _item(
                    "Spray textile désodorisant",
                    "Meilleure odeur avant expédition",
                    "https://www.amazon.fr/s?k=spray+textile+desodorisant",
                )
                + "\n\n### 👟 Nettoyage sneakers\n\n"
                + _item(
                    "Kit nettoyage sneakers",
                    "Indispensable pour Nike, Adidas, Jordan, New Balance…",
                    "https://www.amazon.fr/s?k=kit+nettoyage+sneakers",
                    "environ 10–25 €",
                )
                + "\n\n"
                + _item(
                    "Brosse nettoyage chaussures",
                    "Semelles, tissus et cuir",
                    "https://www.amazon.fr/s?k=brosse+nettoyage+chaussure",
                )
                + "\n\n"
                + _item(
                    "Gomme daim / nubuck",
                    "Traces sur chaussures en daim",
                    "https://www.amazon.fr/s?k=gomme+daim+chaussure",
                )
                + "\n\n"
                + _item(
                    "Nettoyant cuir",
                    "Chaussures et accessoires en cuir",
                    "https://www.amazon.fr/s?k=nettoyant+cuir+chaussure",
                )
                + "\n\n"
                + _item(
                    "Imperméabilisant chaussures",
                    "Protection après nettoyage",
                    "https://www.amazon.fr/s?k=impermeabilisant+chaussure",
                )
                + "\n\n"
                + _item(
                    "Chiffons microfibres",
                    "Nettoyage sans abîmer les matières",
                    "https://www.amazon.fr/s?k=chiffons+microfibres",
                )
            ),
        ),
        (
            "🏪 RESSELLO SELLER KIT — PARTIE 5/5\nStockage, Organisation & Expérience Client",
            (
                "Les outils pour gérer un stock propre, gagner du temps et améliorer l'expérience acheteur.\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "### 🗂️ Stockage & organisation stock\n\n"
                + _item(
                    "Portant vêtements avec roulettes",
                    "Ranger comme une mini boutique — idéal gros stock",
                    "https://www.amazon.fr/s?k=portant+vetement+roulettes",
                    "environ 30–80 €",
                )
                + "\n\n"
                + _item(
                    "Sacs sous vide vêtements",
                    "Gagner de la place pour un gros volume",
                    "https://www.amazon.fr/s?k=sacs+sous+vide+vetement",
                )
                + "\n\n"
                + _item(
                    "Étagère de stockage",
                    "Organiser plusieurs centaines d'articles",
                    "https://www.amazon.fr/s?k=etagere+stockage+garage",
                )
                + "\n\n"
                + _item(
                    "Étiquettes de rangement",
                    "Identifier rapidement chaque catégorie",
                    "https://www.amazon.fr/s?k=etiquettes+rangement",
                )
                + "\n\n### 🎁 Expérience client & colis premium\n\n"
                + _item(
                    "Sachets parfumés pour colis",
                    "Petite attention à la réception",
                    "https://www.amazon.fr/s?k=sachet+parfume+armoire",
                )
                + "\n\n"
                + _item(
                    "Spray textile désodorisant",
                    "Odeur propre avant envoi",
                    "https://www.amazon.fr/s?k=spray+textile+vetement",
                )
                + "\n\n"
                + _item(
                    "Kit couture",
                    "Réparer boutons et petits défauts",
                    "https://www.amazon.fr/s?k=kit+couture",
                )
                + "\n\n"
                + _item(
                    "Colle textile",
                    "Réparer rapidement certains défauts",
                    "https://www.amazon.fr/s?k=colle+textile",
                )
                + "\n\n"
                + _item(
                    "Boutons de remplacement",
                    "Chemises, vestes, manteaux",
                    "https://www.amazon.fr/s?k=boutons+couture",
                )
                + "\n\n"
                + _item(
                    "Patchs vêtements",
                    "Personnaliser ou réparer certaines pièces",
                    "https://www.amazon.fr/s?k=patch+vetement",
                )
                + "\n\n### ⚡ Setup power seller\n\n"
                + _item(
                    "Deuxième téléphone reconditionné",
                    "Séparer compte perso et activité Vinted",
                    "https://www.backmarket.fr/fr-fr",
                )
                + "\n\n"
                + _item(
                    "Scanner code-barres",
                    "Gérer un gros inventaire rapidement",
                    "https://www.amazon.fr/s?k=scanner+code+barre",
                )
            ),
        ),
    ]

    payloads: list[dict[str, Any]] = []
    for title, description in parts:
        # Discord embed description max 4096
        desc = description[:4096]
        title_line, _, subtitle = title.partition("\n")
        payloads.append(
            {
                "embeds": [
                    {
                        "title": title_line[:256],
                        "description": (
                            (f"**{subtitle}**\n\n" if subtitle else "") + desc
                        )[:4096],
                        "color": EMBED_COLOR,
                        "footer": {"text": "Resello · Seller Kit"},
                    }
                ]
            }
        )
    return payloads
