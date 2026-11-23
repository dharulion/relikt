# Relikt — demoscene magazine extractor (agent brief)

## Goal
Recover article text (and, where possible, music and graphics) from binary records inside old DOS demoscene magazine executables and write the results to `out/<edition>/` as UTF-8 text.

## Repository layout
- `data/<edition>/` — read-only source binaries supplied by the user. Never modify or commit these.
- `tools/` — shared Python modules plus per-edition extractors in `tools/<edition>/`.
- `out/<edition>/` — generated articles, manifests, music and graphics. Never commit.
- `tmp/<edition>/` — temporary/diagnostic files. Never commit.
- `examples/<edition>/` — checked-in content listings used to verify a recovery run (no copyrighted works).
- `relikt.py` — CLI/orchestrator that selects the edition and runs its extractors.

## Typical workflow
1. Place the magazine executable in `data/<edition>/`.
2. `python relikt.py -e <edition> verify`
3. `python relikt.py -e <edition> full`
4. `python relikt.py -e <edition> status`

`python relikt.py list` shows discovered editions; `--all` processes all of them.

## Requirements
Python 3.10+ (standard library only). External DOS/emulation tools are optional and only needed for manual analysis, not for running the extractors.

## Constraints
- Do not modify anything under `data/`.
- Do not commit generated content from `out/` or `tmp/`.
- Write all extracted text as UTF-8 so Polish characters are preserved.
- Extractor modules under `tools/<edition>/` are imported and executed by `relikt.py` (including during `verify`); treat them as code and review untrusted additions before running them.
- Run the smoke tests with `python -m unittest discover -s tests`.
