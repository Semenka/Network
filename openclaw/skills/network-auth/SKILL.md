---
name: network-auth
description: One-time OAuth setup for Google (Gmail + People API), X.com, and LinkedIn (OIDC). All scopes are read-only; tokens stay on this machine.
---

# Network Auth

Use this when the user wants to connect a new contact source or rotate credentials.

Pre-flight:

```bash
network-chief auth-status      # who is already authorized
```

Authorize each provider (each opens a browser tab; if you're on a headless host, copy the printed URL into a local browser):

```bash
network-chief auth-google      # Gmail + Google People API (contacts.readonly + gmail.readonly)
network-chief auth-x           # X.com OAuth 2.0 PKCE (tweet.read users.read follows.read offline.access)
network-chief auth-linkedin    # LinkedIn OIDC (openid profile email) — owner identity only
```

Revoke / rotate:

```bash
network-chief auth-revoke --provider google
network-chief auth-revoke --provider x
network-chief auth-revoke --provider linkedin
```

Safety:

- Scopes are read-only. We never send mail, post tweets, or message anyone from this machine.
- Tokens land in `data/network.db` (sqlite, gitignored). Keep filesystem permissions tight.
- LinkedIn cannot return your 1st-degree connections via API. Use `network-chief sync-linkedin --guided-export` to walk through the official data download — it watches `exports/` and ingests the CSV automatically when the archive lands.
- If a provider returns 429, run `auth-status` for the reset time before retrying.
