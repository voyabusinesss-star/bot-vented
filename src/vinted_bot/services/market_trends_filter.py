"""Filtres anti-bruit : ne garder que les concepts commerciaux exploitables."""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

# Adjectifs / descriptifs / états / parties génériques / couleurs seules / tailles
GENERIC_LEXICON: frozenset[str] = frozenset(
    {
        # vides
        "avec", "pour", "dans", "sans", "plus", "tres", "comme", "tout", "tous",
        "toute", "cette", "celui", "elle", "elles", "nous", "vous", "leur",
        "leurs", "des", "les", "une", "du", "au", "aux", "et", "ou", "la", "le",
        "un", "en", "sur", "sous", "par", "pas", "est", "are", "the", "and",
        "for", "from", "with", "de", "a", "à", "d", "l", "y", "ce", "ces",
        "mon", "ma", "mes", "ton", "ta", "tes", "son", "sa", "ses",
        # adjectifs courants
        "grand", "grande", "grands", "grandes", "petit", "petite", "petits",
        "petites", "long", "longue", "longs", "longues", "court", "courte",
        "courts", "courtes", "large", "larges", "fin", "fine", "fins", "fines",
        "epais", "epaisse", "épais", "épaisse", "leger", "legere", "léger",
        "légère", "lourd", "lourde", "etroit", "étroit", "ample", "slim",
        "regular", "oversize", "oversized", "cropped", "tight", "loose",
        "beau", "belle", "joli", "jolie", "super", "cool", "nice", "good",
        "best", "new", "old", "like", "rare", "unique", "original", "vrai",
        "vraie", "parfait", "parfaite", "excellent", "excellente",
        # caractéristiques génériques
        "manche", "manches", "col", "cols", "fermeture", "poche", "poches",
        "bouton", "boutons", "zip", "zipper", "capuche", "capuchon", "ceinture",
        "lacet", "lacets", "semelle", "doublure", "couture", "coutures",
        "motif", "motifs", "rayure", "rayures", "logo", "print", "imprime",
        "imprimé", "broderie", "patch", "patchs", "detail", "détail",
        "manches_longues", "manches_courtes", "col_rond", "col_v",
        # états
        "neuf", "neuve", "neufs", "neuves", "occasion", "etat", "état",
        "bon", "bonne", "tres_bon", "très_bon", "satisfaisant", "correct",
        "abime", "abimé", "abîmé", "use", "usé", "porte", "porté", "vintage",
        "etiquette", "étiquette", "boite", "boîte",
        # couleurs seules
        "noir", "noire", "black", "blanc", "blanche", "white", "rouge", "red",
        "bleu", "blue", "vert", "green", "jaune", "yellow", "orange", "rose",
        "pink", "violet", "purple", "marron", "brown", "beige", "gris", "grey",
        "gray", "dore", "doré", "argente", "argenté", "kaki", "navy", "cream",
        "cream", "ecru", "écru", "bordeaux", "turquoise", "multicolore",
        # tailles / mesures
        "taille", "size", "xs", "s", "m", "l", "xl", "xxl", "xxxl", "t36",
        "t37", "t38", "t39", "t40", "t41", "t42", "t43", "t44", "t45", "t46",
        "36", "37", "38", "39", "40", "41", "42", "43", "44", "45", "46", "47",
        "48", "cm", "mm", "ans", "mois", "eu", "fr", "us", "uk",
        # catégories génériques trop larges seules
        "vetement", "vêtement", "habit", "habits", "article", "articles",
        "mode", "fashion", "style", "look", "piece", "pièce", "objet",
        "accessoires", "accessoire", "chaussure", "chaussures", "baskets",
        "sneakers", "sneaker", "shoe", "shoes", "sapatilhas", "veste",
        "jacket", "manteau", "pantalon", "pants", "jean", "jeans", "short",
        "shorts", "jupe", "robe", "shirt", "tshirt", "tee", "sweat",
        "sweatshirt", "hoodie", "pull", "polo", "chemise", "casquette",
        "bonnet", "echarpe", "écharpe", "sac", "bag",
        # genre / public
        "homme", "femme", "hommes", "femmes", "garcon", "garçon", "fille",
        "enfant", "enfants", "kids", "baby", "bebe", "bébé", "mixte",
        "unisexe", "men", "women", "boys", "girls",
        # marketplace
        "vinted", "vente", "achat", "prix", "euro", "eur", "frais", "envoi",
        "livraison", "annonce", "annonces", "lot", "set", "pack", "bundle",
        "promo", "soldes", "reduction", "réduction", "offre", "urgent",
        "custom", "handmade", "diy",
        # autres bruits fréquents
        "grand", "petite", "manche", "couleur", "matiere", "matière",
        "qualite", "qualité", "authentique", "authenticity", "serie", "série",
        "edition", "édition", "collection", "limited", "special", "spécial",
        "hiver", "ete", "été", "printemps", "automne", "summer", "winter",
    }
)

# Unigrams autorisés même seuls (valeur commerciale connue)
COMMERCIAL_UNIGRAM_ALLOW: frozenset[str] = frozenset(
    {
        "pokemon", "pokémon", "disney", "sanrio", "marvel", "lego", "funko",
        "amiibo", "jellycat", "goretex", "vibram", "polartec", "cachemire",
        "cashmere", "selvedge", "deadstock", "gorpcore", "y2k", "archive",
        "airpods", "supreme", "stussy", "carhartt", "moncler", "hermes",
        "prada", "gucci", "chanel", "nike", "adidas", "salomon", "asics",
        "hoka", "stone", "island",  # stone alone weak — handled via phrases
        "nuptse", "detroit", "samba", "gazelle", "dunk", "yeezy", "jordan",
        "shox", "kayano", "nendoroid", "manga", "naruto", "pikachu",
        "dracaufeu", "kuromi", "stitch", "leica", "polaroid", "vinyle",
        "vinyl", "playstation", "xbox", "nintendo", "gameboy",
    }
)

PRODUCT_REF_RE = re.compile(
    r"^(?=.*\d)[a-z0-9][a-z0-9\-]{2,18}$|"  # refs alphanumériques
    r"^(xt|acs|af|am|nb|aj)[\-_]?[0-9]{1,4}[a-z]?$",
    re.IGNORECASE,
)


def normalize_token(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("-", " ").replace("_", " ")
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def is_generic_token(token: str | None) -> bool:
    t = normalize_token(token).replace(" ", "_")
    if not t:
        return True
    if t.isdigit():
        return True
    if len(t) <= 2:
        return True
    if t in GENERIC_LEXICON:
        return True
    # tailles type "t42"
    if re.fullmatch(r"t?\d{2}", t):
        return True
    return False


def is_commercial_unigram(token: str | None) -> bool:
    t = normalize_token(token).replace(" ", "")
    if not t:
        return False
    if t in COMMERCIAL_UNIGRAM_ALLOW:
        return True
    if PRODUCT_REF_RE.match(t):
        return True
    return False


def is_commercially_relevant_phrase(phrase: str | None) -> bool:
    """True si la phrase est un concept commercial (pas un mot générique)."""
    norm = normalize_token(phrase)
    if not norm:
        return False
    parts = [p for p in norm.split() if p]
    if not parts:
        return False

    # Phrase entièrement générique
    if all(is_generic_token(p) for p in parts):
        return False

    joined = "_".join(parts)
    if joined in GENERIC_LEXICON:
        return False

    # Unigram : seulement allowlist / refs produit
    if len(parts) == 1:
        return is_commercial_unigram(parts[0])

    # Bigram/trigram : rejeter si un seul mot non-générique trop faible
    # Ex. "grand logo" → logo générique + grand générique
    non_generic = [p for p in parts if not is_generic_token(p)]
    if not non_generic:
        return False

    # "manches longues", "bon etat", "taille m"
    if len(non_generic) == 1 and is_generic_token(non_generic[0]):
        return False

    # Au moins un signal commercial OU 2+ mots non génériques
    if any(is_commercial_unigram(p) for p in non_generic):
        return True
    if len(non_generic) >= 2:
        return True

    # Un seul mot non générique dans un bigram avec générique → souvent bruit
    # ("veste detroit" ok car detroit commercial? detroit in allow via models)
    # Autoriser si le mot non générique a une forme "produit" (majuscule pattern déjà perdu)
    # Exiger len >= 5 pour le signal seul
    signal = non_generic[0]
    if len(signal) >= 5 and signal not in GENERIC_LEXICON:
        # encore trop permissif pour "lampe" etc. — demander bigram complet non générique partiel
        # "detroit jacket" : detroit ok (5+), jacket generic → True (modèle connu via models de préférence)
        return True
    return False


@lru_cache(maxsize=1)
def commercial_alias_index() -> tuple[tuple[str, str, str], ...]:
    """(normalized_alias, entity_id, display) trié par longueur desc."""
    from pathlib import Path

    import yaml

    from vinted_bot.services.market_entities import load_keyword_defs, load_model_defs

    entries: list[tuple[str, str, str]] = []
    trends_path = Path(__file__).resolve().parents[3] / "config" / "market_trends.yaml"
    raw = yaml.safe_load(trends_path.read_text(encoding="utf-8")) or {}
    for row in raw.get("topics") or []:
        if not isinstance(row, dict) or not row.get("slug"):
            continue
        if str(row.get("kind") or "") == "color":
            continue
        display = str(row.get("display_name") or row["slug"])
        slug = str(row["slug"]).strip().lower()
        for alias in row.get("aliases") or []:
            norm = normalize_token(str(alias))
            if norm:
                entries.append((norm, f"topic:{slug}", display))
    for model in load_model_defs():
        for alias in model.aliases:
            norm = normalize_token(alias)
            if norm:
                entries.append((norm, f"model:{model.slug}", model.display_name))
    for kw in load_keyword_defs():
        if kw.kind == "color":
            continue
        for alias in kw.aliases:
            norm = normalize_token(alias)
            if norm and is_commercially_relevant_phrase(norm):
                entries.append((norm, f"keyword:{kw.slug}", kw.display_name))
    entries.sort(key=lambda x: len(x[0]), reverse=True)
    return tuple(entries)


def extract_commercial_phrases(title: str | None) -> list[tuple[str, str, str]]:
    """Retourne [(entity_type, key, display), ...] concepts commerciaux du titre.

    Priorité : aliases connus (modèles, licences, keywords) puis bigrams/trigrams filtrés.
    """
    norm = normalize_token(title)
    if not norm:
        return []
    hay = f" {norm} "
    found: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    matched_spans: list[str] = []
    for alias, entity_id, display in commercial_alias_index():
        if f" {alias} " not in hay:
            continue
        etype, key = entity_id.split(":", 1)
        uid = f"{etype}:{key}"
        if uid in seen:
            continue
        found.append((etype, key, display))
        seen.add(uid)
        matched_spans.append(alias)

    parts = [p for p in norm.split() if p]
    # trigrams puis bigrams — jamais unigrams libres
    for n in (3, 2):
        for i in range(0, max(0, len(parts) - n + 1)):
            chunk_parts = parts[i : i + n]
            phrase = " ".join(chunk_parts)
            if not is_commercially_relevant_phrase(phrase):
                continue
            # Déjà couvert par un alias connu
            if any(phrase == span or phrase in span or span in phrase for span in matched_spans):
                continue
            key = phrase.replace(" ", "_")
            uid = f"phrase:{key}"
            if uid in seen:
                continue
            found.append(("phrase", key, phrase.title()))
            seen.add(uid)

    return found[:10]
