# LiteLLM proxy

All LLM / image / video calls from mhsquare go through this proxy. Business logic only knows the
provider-neutral aliases (`planner-llm`, `code-llm`, `image-gen`, `video-gen`, …).

## Configure from the UI (normal path)

Open **Settings** in mhsquare:

1. **AI providers**: paste the API keys for the providers you use (Anthropic, Gemini, OpenAI,
   OpenRouter).
2. **Models**: pick the LiteLLM model id for each role (`provider/model`). Defaults come from
   [llm-defaults.env](llm-defaults.env).
3. **Daily budget**: set a USD limit. The first save creates the mhsquare virtual key
   (`budget_duration: 1d`). Later saves update its limit.

Everything is written to **`local/llmconfig.env`**, which is gitignored and never committed. Keys
are write-only in the UI: it shows only `configured …abcd`.

## How it is wired

```
UI ──PUT /api/settings/llm──► api ──atomic write──► local/llmconfig.env
                                                        │ (watched every 3 s)
                         litellm/entrypoint.sh ◄────────┘ reloads LiteLLM with the new env
```

- [config.example.yaml](config.example.yaml) maps each alias to `os.environ/MH_MODEL_*`. It contains no
  values. `setup` copies it to `config.yaml` (gitignored). The example is used if that copy is
  missing.
- The entrypoint parses env files without shell evaluation, and restarts the proxy only when
  provider keys or model IDs change. mhsquare's own key line doesn't trigger a restart.
- The API never needs the Docker socket.
- The master key (`LITELLM_MASTER_KEY` in `.env`) is used by the API only to create and update the
  budget key.

## Manual key admin (optional)

The proxy listens on `127.0.0.1:${LITELLM_HOST_PORT}` (default 4000):

```sh
curl -s -X POST localhost:4000/key/update -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" -d '{"key":"sk-...","max_budget":0.01}'
```

Setting `max_budget` to `0.01` is the phase 6 check: the run should go to `paused_budget`.
