# Local development

All values here are synthetic. Never paste a real household value into this
file.

## Requirements

- Python 3.13 (3.12 works)
- [uv](https://docs.astral.sh/uv/)
- Optional: Docker, for the phase 2 PostgreSQL work

## First run

```bash
cd backend
uv sync --extra dev
```

Create the untracked configuration:

```bash
cp .env.example .env          # from the repository root
python scripts/generate_client_token.py   # paste into HOMEFLOW_DEV_CLIENT_TOKEN
```

`.env` is gitignored. It must never be committed, and it must never contain a
value copied from a production deployment.

## Start the gateway

```bash
cd backend
uv run python -m homeflow
```

It binds to `127.0.0.1:8000` and starts in demo mode with a synthetic household.
Interactive API documentation is available at `/docs` in development only.

## Try it

```bash
TOKEN="the value of HOMEFLOW_DEV_CLIENT_TOKEN"

curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/v1/devices

POOL=$(curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/v1/devices \
  | python -c "import json,sys;print(next(d['id'] for d in json.load(sys.stdin) if d['kind']=='POOL'))")

curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"action":"SET_HEATER","parameters":{"on":true}}' \
  http://127.0.0.1:8000/v1/devices/$POOL/commands
```

The demo pool then warms towards its target on every simulation tick, and the
change is pushed to any connected WebSocket client.

Requests without a credential return `401` with a problem document. That is the
expected behaviour: being on the machine is not authorisation.

## Quality gates

```bash
cd backend
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
```

Hardware-marked tests never run in CI and are opt-in locally:

```bash
uv run pytest -m "requires_bestway"
```

## Optional pre-commit hooks

```bash
uvx pre-commit install
```

This adds local formatting, large-file and secret scanning. CI runs the same
checks; the hooks are convenience, not the security boundary.

## Common problems

**Every request returns 401.** `HOMEFLOW_DEV_CLIENT_TOKEN` is unset or the
process was started before `.env` existed. The gateway registers no clients in
that case, by design.

**Startup fails with a configuration error.** That is intentional. Production
refuses demo mode, a development credential, a wildcard `Host` allowlist, or a
missing `HOMEFLOW_ID_SALT`.

**pyright cannot find imports.** The environment lives at `backend/.venv`; run
it through `uv run` or recreate it with `uv sync --extra dev`.
