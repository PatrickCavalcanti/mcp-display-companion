# Contributing

Thanks for helping. Small, focused changes are the easiest to review.

## Running the tests

```bash
python -m pip install pytest
python -m pytest -q
```

The tests need no network and no Windows APIs: everything is faked
(see the Development section of the README).

## Where the panel texts live

Every text on the phone screen is in `mcp_display_companion/strings.py`, in two
dictionaries: `"en"` and `"pt-BR"`. When you add or change a text, change both
languages in the same commit.

`tests/test_strings.py` fails if the two languages have different keys, if a
placeholder (`{name}`) differs between them, or if a key is unused.

## Commit messages

Look at the history with `git log --oneline -14`. The convention:

- A prefix, then a scope in parentheses, then a short subject:
  `feat(view): ...`, `fix(runtime): ...`, `docs: ...`, `refactor(home): ...`.
- Prefixes seen in the last 14 commits: `feat`, `fix`, `docs`, `refactor`.
- Most subjects are in English. A few older subjects are in Portuguese.
- Put the reason in the body when the diff alone does not show it.

## Pull requests

- Keep a PR to one change. Explain what changed and why.
- Add or update a test for the behavior you change.
- Write code, comments, and docs in English.

## Licence

By contributing, you agree that your contribution is released under the MIT
licence in `LICENSE`.
