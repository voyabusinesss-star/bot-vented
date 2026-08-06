# Railway : multi-services (même image / même repo)

Architecture pour éviter les OOM Chromium (scrape + detector + fiches séparés de l’API Discord).

## Plan free (actuel) — 3 services

| Service        | `APP_ROLE` | Rôle                                         | HTTP public |
|----------------|------------|----------------------------------------------|-------------|
| `bot-vented`   | `api`      | Discord + Whop + migrations                  | oui `/health` |
| `bot-scrape`   | `scrape`   | **Salons publics + alertes filtres privés**  | non |
| `bot-detector` | `niches`   | Détecteur puis fiches **à tour de rôle** (1 Chromium) | non |

Le scrape privé n’est **pas** un 4ᵉ service : il tourne déjà dans `bot-scrape` (même boucle Playwright que les salons publics + worker filtres DM).

Sur `niches`, detector et fiches s’alternent (jamais 2 navigateurs en même temps) pour éviter les `Target crashed`.

`bot-fiches` dédié nécessite un upgrade de plan Railway (limite de resources free).

## Plan payant — 4 services

| Service        | `APP_ROLE`  | Rôle                                      | HTTP public |
|----------------|-------------|-------------------------------------------|-------------|
| `bot-vented`   | `api`       | Discord + Whop + migrations               | oui `/health` |
| `bot-scrape`   | `scrape`    | Salons publics + filtres privés           | non |
| `bot-detector` | `detector`  | Détecteur de niches                       | non |
| `bot-fiches`   | `fiches`    | Fiches produit niches                     | non |

Tous partagent le **même Postgres** (`DATABASE_URL` → service Postgres).

## Variables minimales par service

### Commun (tous)
- `DATABASE_URL` = `${{Postgres.DATABASE_URL}}` (ou `postgresql+psycopg://…`)
- `DISCORD_BOT_TOKEN`
- salons Discord nécessaires au rôle

### `bot-vented` (api)
```
APP_ROLE=api
```
+ toutes les vars Whop / panels / salons déjà présentes.

### `bot-scrape`
```
APP_ROLE=scrape
SCRAPE_PARALLEL_WORKERS=4
SCRAPE_POLL_SECONDS_MIN=0.3
SCRAPE_POLL_SECONDS_MAX=0.8
REQUEST_DELAY_SECONDS=0.5
DISCORD_POST_DELAY_SECONDS=0
```
+ tous les `DISCORD_CHANNEL_*` marques / sneakers / ALL.

### `bot-detector` (plan free = `niches`)
```
APP_ROLE=niches
NICHES_DETECTOR_WINDOW_SECONDS=2100   # ~35 min detector d'abord
NICHES_DETECTOR_CYCLE_PAUSE_SECONDS=180
FICHES_DEVELOP_SECONDS=900            # deep-dive ~15 min (1 fiche/h max)
DISCORD_CHANNEL_NICHES=…
DISCORD_CHANNEL_NICHES_DEMO=…
DISCORD_CHANNEL_NICHES_VINTED=…
DISCORD_CHANNEL_FICHES_PRODUIT=…
```

Cadence code : **≤10 détections Discord / heure**, **1 fiche / heure**, jamais deux fois la même niche en fiche. Scrape public+privé = `bot-scrape` uniquement (inchangé).

### `bot-fiches` (plan payant seulement)
```
APP_ROLE=fiches
DISCORD_CHANNEL_FICHES_PRODUIT=…
```

Sur plan free, ne pas créer `bot-fiches` : utiliser `APP_ROLE=niches` sur `bot-detector`.

## Création CLI (exemple)

```bash
railway link -p <project> -e production

railway add --service bot-scrape --repo voyabusinesss-star/bot-vented --branch main \
  --variables "APP_ROLE=scrape"

railway add --service bot-detector --repo voyabusinesss-star/bot-vented --branch main \
  --variables "APP_ROLE=detector"

railway add --service bot-fiches --repo voyabusinesss-star/bot-vented --branch main \
  --variables "APP_ROLE=fiches"
```

Puis copier / référencer les variables depuis `bot-vented` (token Discord, channels, DB).

Sur **bot-vented** uniquement : `APP_ROLE=api` (plus de scrape/detector/fiches dans le même container).
