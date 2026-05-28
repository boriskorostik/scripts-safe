# AGENT.md

## Scope
Work only inside `/home/boris/scripts-safe`.

## Purpose
This repo is a curated, GitHub-safe subset of `/home/boris/script`.
It exists to keep only reusable scripts that do not contain known hardcoded secrets.

## Rules
- Do not bulk-copy the whole `/home/boris/script` tree here.
- Add scripts one by one after checking for passwords, tokens, and private data.
- Keep `manifests/` as an index of original files, not as executable source.

## Validation
- Python files should compile.
- Shell scripts should remain executable when appropriate.
- Avoid adding binaries, spreadsheets, archives, and generated folders.
