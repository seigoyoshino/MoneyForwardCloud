# Household dashboard (viewer)

Streamlit app that renders a household budget dashboard.
This repository contains **application code only** — no personal or financial data.
The app reads an encrypted data bundle at runtime from a separate private source,
and decrypts it with a key supplied via Streamlit secrets.

## Required secrets

```toml
ENCRYPTION_KEY = "..."   # Fernet key
GITHUB_TOKEN   = "..."   # read access to the private data repository
DATA_REPO      = "owner/repo"
DATA_PATH      = "cloud_data/bundle.enc"   # optional
DATA_BRANCH    = "main"                    # optional
FULL_USERS     = "a@example.com"           # comma separated
SETTLE_USERS   = "b@example.com"           # comma separated
```
