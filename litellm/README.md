# LiteLLM proxy

All LLM / image / video calls from mhsquare go through this proxy. The backend only knows
`MHSQUARE_LITELLM_URL` and the virtual key `MHSQUARE_LITELLM_API_KEY`.

## 1. Create `config.yaml`

```sh
cp litellm/config.example.yaml litellm/config.yaml   # gitignored
```

Edit the `model:` values if needed. **Never paste keys into this file** — keys are read via
`os.environ/ANTHROPIC_API_KEY`, `os.environ/GEMINI_API_KEY`, which come from `.env`.

## 2. Start the proxy

```sh
docker compose up -d postgres litellm
curl http://localhost:4000/health/liveliness
```

The proxy stores keys and spend in the `litellm` Postgres database (created by
`infra/postgres/init.sql`).

## 3. Create the mhsquare virtual key with a daily budget

```sh
curl -s -X POST http://localhost:4000/key/generate \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"key_alias":"mhsquare","max_budget":5.0,"budget_duration":"1d","metadata":{"app":"mhsquare"}}'
```

Put the returned `key` into `.env` as `MHSQUARE_LITELLM_API_KEY`, then
`docker compose up -d api worker` to reload.

`budget_duration: 1d` makes LiteLLM reset spend daily, so the daily stop is deterministic. To change
the limit: `POST /key/update` with `{"key": "...", "max_budget": 10}`.

## Testing a low budget (phase 6 check)

Set `max_budget` to `0.01`, start a run, and watch it go to `paused_budget`.
