# Contributing

Thanks for helping improve Fernruf.

## Ground rules

- Do not remove or bypass the fixed AI disclosure, allowlist enforcement, marketing blocks, or policy ceilings.
- Pull requests that weaken abuse protections will be closed.
- Use **fictitious** phone numbers in tests and docs — only numbers reserved for drama/media use:
  - **Germany:** Bundesnetzagentur [notice 148/2021 (PDF)](https://www.bundesnetzagentur.de/DE/Fachthemen/Telekommunikation/Nummerierung/_DL/mittlg148_2021.pdf?__blob=publicationFile) drama numbers — Berlin `(0)30 23125 000–999` (E.164 e.g. `+493023125123`), mobile `(0)171 39200 00–99` (E.164 e.g. `+491713920001`).
  - **United States:** NANP reserved `555-0100` through `555-0199` (E.164 e.g. `+12025550100` for `202-555-0100`).
  - Blocked-prefix fixtures (0900, 118xx, etc.) stay clearly fake service numbers.
- Use `example.com` and documentation IP ranges (`127.0.0.0/8`, `192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`) for network examples.

## Development

```bash
uv sync --group dev
uv run pytest
```

## Commits

Keep changes focused; match existing style in the module you edit.
