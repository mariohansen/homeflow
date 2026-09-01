# HomeFlow backend

FastAPI gateway that normalises heterogeneous household devices into one
canonical API. See the repository root `README.md` for the product overview and
`../CLAUDE.md` for the architectural and security constraints.

```bash
uv sync --extra dev
uv run ruff check .
uv run pyright
uv run pytest
uv run python -m homeflow          # serves on 127.0.0.1:8000 in demo mode
```
