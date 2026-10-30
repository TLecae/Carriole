## What and why

<!-- What does this change, and for which use? Link the issue if there is one. -->

## Checks

- [ ] `uv run ruff format .` and `uv run ruff check .`
- [ ] `uv run pytest engine api store worker assistant -m "not slow"`
- [ ] Interface changed: `npx tsc --noEmit` and `npx vitest run` in `frontend/`
- [ ] New user-visible text: French text as the key, English translation added (`uv run etl i18n check --lang en`)
- [ ] Modifier changed: catalogue regenerated (`uv run etl modifiers doc`, then with `--lang en --out docs/en/catalogue.md`)
- [ ] `CHANGELOG.md` updated if users will notice the change
