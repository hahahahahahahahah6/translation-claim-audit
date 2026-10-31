# translation-claim-audit

An offline static auditor that compares documentation claims about converting
text between languages with provider-resolved operations in Python source.
Version 0.1.0 uses only the Python standard library and never executes the
project being inspected.

## Install and run

```sh
python3 -m pip install .
translation-claim-audit /path/to/project
python3 -m translation_claim_audit /path/to/project --json
translation-claim-audit --version
python3 -m unittest discover
```

The root defaults to the current directory. Use `--verbose` for evidence and
matched phrases, `--json` for structured output, and repeat `--ignore GLOB` to
exclude relative file paths. Quote globs to avoid shell expansion.
`--fail-on MEDIUM` makes findings supported only by tests fail the command.
The default threshold is HIGH. Usage and input errors exit 1; findings meeting
the threshold exit 2; otherwise the command exits 0. A MEDIUM finding remains
visible with status FAIL even when the default threshold gives exit 0.

## Scope and matching

Only root README.md and Markdown files below any directory named docs are
inspected. Fences of backticks or tildes with up to three leading spaces are
excluded. Inline code counts. Blank lines separate paragraphs. Matching folds
case, expands negative contractions, replaces hyphens with spaces and collapses
whitespace. It uses exact word boundaries with longest phrases first and
alphabetical tie breaking. Negative spans are removed before positive matching.
The fixed vocabulary contains 50 positive and 26 negative phrases.

Python files are parsed with ast. Symlinked files and directories are excluded.
Files below fixtures, docs and examples cannot supply evidence. Files below
tests, or named test_*.py or *_test.py, supply test evidence only. Invalid Python
supplies no evidence. Imports and bound objects are resolved within each module;
local definitions and assignments can shadow imported names.

Recognized provider operations include the transformers task pipeline with a
literal task containing the relevant token, five specified model constructors
or model loaders, and bound methods on objects created by googletrans,
deep_translator or deepl. Constructing an API client alone is insufficient.
Detection, locale formatting, dictionary lookup, transliteration, character
mapping, stored strings, shell commands and unrelated speech or audio APIs
supply no evidence. Dynamic imports, runtime values and cross-module object
flows are outside this static check. This is a source fingerprint audit, not a
runtime correctness guarantee.

The finding identifiers and severity rules are:

```
documented-translation-claim-false: HIGH without production evidence;
MEDIUM if evidence exists only in tests.
documented-no-translation-claim-false: HIGH with production evidence.
```

There is at most one finding per family. A positive claim with no source still
fails; a negative claim with no source passes. A project without matching
claims passes. JSON includes status, findings, the two evidence flags and
matched positive and negative phrases.

## Development

```sh
python3 -m unittest discover
python3 -m build
```

MIT licensed. No runtime dependencies.
