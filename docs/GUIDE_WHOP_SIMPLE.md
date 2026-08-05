# Guide simple — Whop + Discord Resello

Lis ça calmement, une case à la fois.  
Tu n’as **pas** besoin de tout comprendre : suis juste les étapes.

---

## Ce qui est DÉJÀ bon

| Chose | État |
|--------|------|
| Bot sur Railway | OK (`/health` → ok) |
| Webhook Whop | OK (test → `200`) |
| Prix Starter | **14,99 € / mois** |
| Prix Pro | **19,99 € / mois** |
| Prix Pro+ | **24,99 € / mois** |

Tu n’as **plus** à modifier les prix des anciens plans (Whop les bloque si déjà vendus — c’est normal).

---

## Pourquoi tu n’as pas eu le rôle

Le bot donne le rôle **seulement** s’il reçoit ton **ID Discord** dans le webhook.

Si Discord n’est **pas connecté** à ton compte Whop → paiement OK, **rôle = non**.

---

## Test qui marche (gratuit)

### Étape 1 — Lier Discord à Whop (OBLIGATOIRE)

1. Connecte-toi sur Whop avec le **compte qui achète** (pas forcément le compte vendeur)
2. Ouvre : https://whop.com/@me/settings/connected-accounts/
3. Clique **Connect** à côté de Discord
4. Autorise Whop
5. Vérifie que Discord apparaît bien comme connecté

### Étape 2 — Quitter l’abo test s’il est déjà pris

1. Whop → ton abonnement gratuit / test
2. **Leave** / **Cancel** / **Terminate**
3. Attends 10 secondes

### Étape 3 — Rejoindre

1. Ouvre le bon lien :
   - Starter : https://whop.com/checkout/plan_qugfCEyM1Fj6M
   - Pro : https://whop.com/checkout/plan_4wqm3XCgWhxdZ
   - Pro+ : https://whop.com/checkout/plan_fd6h0aOtaZESw
2. Si c’est gratuit (ou code promo) → confirme
3. Va sur Discord → serveur Resello
4. Tu dois avoir le rôle **Starter** / **Pro** / **Pro+**

### Étape 4 — Remettre payant

Dès que le test est OK, remets les prix payants (ou masque le plan gratuit).  
Ne laisse pas Starter/Pro/Pro+ en gratuit en public.

---

## Si toujours pas de rôle

Envoie à l’assistant (ou fais-le toi) :

1. Ton **ID Discord**  
   Discord → Paramètres → Avancé → Mode développeur ON  
   → clic droit sur ton pseudo → **Copier l’ID utilisateur**
2. Le plan voulu : Starter / Pro / Pro+

→ on te met le rôle **à la main**, puis on regarde les logs Railway.

---

## Liens utiles

| Quoi | Lien |
|------|------|
| Santé du bot | https://bot-vented-production.up.railway.app/health |
| Webhook (dans Whop) | `https://bot-vented-production.up.railway.app/webhooks/whop` |
| Discord lié | https://whop.com/@me/settings/connected-accounts/ |

---

## Railway — variables checkout (si pas encore fait)

Noms **exactement** comme ça :

```
SUBSCRIPTIONS_CHECKOUT_STARTER=https://whop.com/checkout/plan_qugfCEyM1Fj6M
SUBSCRIPTIONS_CHECKOUT_PRO=https://whop.com/checkout/plan_4wqm3XCgWhxdZ
SUBSCRIPTIONS_CHECKOUT_PROPLUS=https://whop.com/checkout/plan_fd6h0aOtaZESw
```

Aussi nécessaires (déjà en principe) :

- `DISCORD_BOT_TOKEN`
- `DISCORD_GUILD_ID`
- `WHOP_WEBHOOK_SECRET`
- `WHOP_PRODUCT_STARTER` / `_PRO` / `_PROPLUS`
- `DISCORD_ROLE_SUB_STARTER` / `_PRO` / `_PROPLUS`

---

## Ce qu’il ne faut PAS faire

- Modifier le prix d’un **ancien** plan « déjà des membres » → Whop refuse, c’est normal
- Tester avec le **compte vendeur** Whop (souvent bloqué)
- Rejoindre **sans** Discord lié
- Laisser les abos en **gratuit** après le test

---

## Résumé en 3 lignes

1. **Lier Discord** sur Whop  
2. **Rejoindre** l’abo (gratuit pour test)  
3. **Vérifier le rôle** sur Discord → puis remettre payant  

Si étape 3 échoue → envoie ton ID Discord + le plan.
