# Railway — Discord + Whop + scrape Vinted (24/7)

Déploie le bot sur Railway : webhook Whop (URL HTTPS fixe) **et** scrape Vinted.
Plus besoin de faire tourner le scrape en local.

## Architecture

```text
Paiement Whop → https://<service>.up.railway.app/webhooks/whop
             → discord-interactions (Railway)
             → rôles Discord + Postgres

scrape --loop     → salons marques + filtres privés (si filtres créés)
detector --loop   → détecteur de niches
fiches-produit    → fiches produit
```

Désactiver un worker : `ENABLE_SCRAPE=0` / `ENABLE_DETECTOR=0` / `ENABLE_FICHES=0`.

## Prérequis

- Compte Railway (même abo que Vintify = 2e projet OK)
- Repo Git poussé (ou deploy depuis CLI)
- Variables d’env (voir ci-dessous)

## Créer le service

1. [railway.app](https://railway.app) → **New Project** → **Resello Bot** (à côté de Vintify)
2. **Add service** → Deploy from GitHub (ce repo) **ou** `railway up`
3. **Add Plugin** → **PostgreSQL**
4. Générer un domaine : service → **Settings** → **Networking** → **Generate Domain**
5. Noter l’URL : `https://XXXX.up.railway.app`

Le `Dockerfile` + `railway.toml` du repo sont utilisés automatiquement.

Au démarrage : `alembic upgrade head` puis `vinted-bot discord-interactions`.

Healthcheck : `GET /webhooks/whop/health` → `ok`

## Variables d’environnement (Railway → Variables)

Copier depuis ton `.env` local au minimum :

```env
DATABASE_URL=<lié automatiquement au plugin Postgres Railway>
# postgresql://… est normalisé en postgresql+psycopg:// par le bot

DISCORD_BOT_TOKEN=
DISCORD_GUILD_ID=
DISCORD_APPLICATION_ID=

DISCORD_ROLE_SUB_STARTER=
DISCORD_ROLE_SUB_PRO=
DISCORD_ROLE_SUB_PROPLUS=
DISCORD_ROLE_REGLEMENT_VERIFIED=

WHOP_WEBHOOK_SECRET=
WHOP_PRODUCT_STARTER=
WHOP_PRODUCT_PRO=
WHOP_PRODUCT_PROPLUS=
WHOP_WEBHOOK_HOST=0.0.0.0
# PORT est injecté par Railway — ne pas forcer WHOP_WEBHOOK_PORT en prod
```

### Scrape Vinted (obligatoire si tu ne scrapes plus en local)

Copier depuis ton `.env` local **toutes** les `DISCORD_CHANNEL_*` (marques, sneakers, `DISCORD_CHANNEL_ALL`, `DISCORD_CHANNEL_MES_ALERTES`, etc.) + :

```env
VINTED_BASE_URL=https://www.vinted.fr
SCRAPE_HEADLESS=true
REQUEST_DELAY_SECONDS=1.8
MAX_RETRIES=3
ENABLE_SCRAPE=1
PRIVATE_FILTER_SCRAPE_INTERVAL_SECONDS=20
```

Astuce Railway : **Variables** → Raw Editor → colle le bloc `DISCORD_CHANNEL_*` de ton `.env`.

Optionnel panels : `DISCORD_WEBHOOK_*`, etc.

## Brancher Whop (une seule fois)

1. Whop → **Developer** → **Webhooks** → éditer le webhook
2. **URL du point de terminaison** :
   ```text
   https://XXXX.up.railway.app/webhooks/whop
   ```
   (remplace `XXXX` par ton domaine Railway)
3. Events :
   - `membership_activated`
   - `membership_deactivated`
4. Enregistrer (garder le même secret `ws_…` / `whsec_…` déjà dans `WHOP_WEBHOOK_SECRET`)

Ne plus utiliser d’URL `trycloudflare.com` / ngrok.

## Vérifier

```bash
curl https://XXXX.up.railway.app/webhooks/whop/health
# → ok
```

Puis Whop → **Send test** (`membership_activated`) ou un vrai paiement.

Logs Railway attendus :
- `whop_webhook_handled`
- `whop_roles_synced` / `whop_subscription_activated`

## Local vs Railway

| | Local | Railway |
|--|-------|---------|
| Webhook | cloudflared (URL change) | URL fixe |
| Port | `WHOP_WEBHOOK_PORT=8788` | `PORT` (auto) |
| DB | docker compose | plugin Postgres |
| Scrape | `scrape --loop` | non (hors scope) |

## Dépannage

| Symptôme | Action |
|----------|--------|
| Healthcheck fail | Vérifier que `WHOP_WEBHOOK_SECRET` ou un rôle/produit est set (sinon le serveur webhook ne démarre pas) |
| `bad_signature` | Recopier le secret webhook Whop dans `WHOP_WEBHOOK_SECRET` |
| `unknown_product` | Vérifier `WHOP_PRODUCT_*` = `prod_…` |
| `pending_discord` | Discord non lié au checkout Whop |
| Pas de rôle | Bot : **Gérer les rôles** + rôles sous le rôle du bot |
