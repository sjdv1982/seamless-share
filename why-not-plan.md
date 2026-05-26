# Plan: `why-not` tool for `seamless-share`

This plan specifies **what to build and how to test it**, not how to implement it. The contract source is [Pattern 2 of usage-patterns.md](usage-patterns.md#pattern-2-cache-miss-diagnosis-why-not). Where this plan and that contract disagree, the contract wins; this file refines API surface and tests.

## Status and naming

Two tools are in scope, implemented as separable units:

- `seamless-share transformation-diff` — low-level primitive. Compares two transformation references. No candidate selection. No I/O beyond the database endpoint(s) supplied. Read-only.
- `seamless-share why-not` — user-facing tool. Wraps `transformation-diff`. Adds candidate-selection heuristic and human-readable output. Read-only.

Implementers may pick different command names but must preserve the layering: `why-not` calls `transformation-diff`; `transformation-diff` has no candidate-selection logic.

## Surfaces in scope

Each tool has two surfaces; both must work and both must produce the same underlying findings.

1. **CLI**: argv-driven, primary user-facing form.
2. **Python API**: `from seamless_share.why_not import why_not, transformation_diff` (final import path is an implementer choice; this plan uses these names). Returns structured Python objects. Used by tests and by other agents.

Both surfaces wrap the same core. Whatever the core returns, the CLI emits as JSON (default) or formats as human-readable text (with `--format text`); the Python API returns it as typed objects.

## What is explicitly out of scope (v1)

- **Cascade explanation.** `why-not` does not recursively diagnose upstream transformations. A single upstream `result_checksum` change shows as one differing input pin; the user re-invokes `why-not` on that upstream `tf_checksum` manually if they want to go further.
- **Metadata divergence diagnosis.** `MetaData` rows are not consulted by these tools. They are evidence about execution, not cache identity.
- **Buffer-content normalization or fuzzy matching.** Identity is checksum equality. The `--deep` flag presents content for humans; it does not relax identity.
- **Any write path.** Neither tool has a code path that opens any database or bufferdir endpoint for writing. This is a structural property, not a runtime guard.

## CLI ergonomics

### Common conventions (both subcommands)

- Subcommands under `seamless-share`: `seamless-share why-not …`, `seamless-share transformation-diff …`.
- `--endpoint <spec>` — repeatable. Each occurrence names one database endpoint. Order matters only for tie-breaking in per-endpoint provenance display; the lookup state is computed under the documented union rules. Endpoint spec syntax mirrors what [seamless-config](../seamless-config/) accepts for a database endpoint (path to a local `seamless.db`, URL of a remote db service, named cluster from `clusters.yaml`).
- `--config <path>` — optional override for resolving named endpoints; defaults to the user's normal `seamless-config` resolution.
- `--format {json,text}` — default `json` for `transformation-diff` (machine-readable primitive), default `text` for `why-not` (human-facing). `--format json` on `why-not` must produce the same structured output the Python API returns.
- `--deep` — opt-in deep diff (see [Default depth and the `--deep` flag](usage-patterns.md#default-depth-and-the---deep-flag)).
- `--quiet` / `-q` — suppress informational lines from text format; structured output unaffected.
- `--verbose` / `-v` — include per-endpoint provenance and timing in text format.
- Exit codes (both tools):
  - `0` — tool ran successfully and produced output. **This is the normal exit code regardless of whether a diff was found or whether the lookup state indicates a miss.** The tool ran; the user reads the output. Findings are not errors.
  - `2` — usage error (bad argv, missing required argument, unknown endpoint).
  - `3` — endpoint error (endpoint unreachable, database file corrupt, permission denied).
  - `4` — `--deep` requested but one or more buffers were unavailable. Tool still produced output; this exit code is informational. May be downgraded to `0` with `--deep-best-effort`.
  - Reserve `1` for unexpected internal failure (crash, unhandled exception). Implementations must not use `1` for routine "diff has entries" or "lookup state is `NOT_PRESENT`".

### `transformation-diff` CLI

```
seamless-share transformation-diff <ref-A> <ref-B> [--endpoint <spec> …] [--deep] [--format {json,text}]
```

- Exactly two positional references. Fewer or more is exit code `2`.
- A reference is one of:
  - A 64-hex `tf_checksum` (resolved against the supplied endpoints).
  - A path to a JSON file containing an unwrapped transformation dict (the dict whose checksum is `tf_checksum`, plus its dunder companions if present; the tool computes the checksum to surface in output).
  - A path to a Seamless transformation definition that the tooling can evaluate to a transformation dict. The exact accepted forms are an implementer choice; document them.
- The two references may be of different forms (e.g., diff a local definition against a cached `tf_checksum`).
- The tool does not require both references to be present in any endpoint. A reference supplied as a `tf_checksum` that is absent from all endpoints is exit `3` only if it is needed to retrieve the dict; if the user also supplied the dict directly, no lookup is required.

### `why-not` CLI

```
seamless-share why-not <ref> [--endpoint <spec> …] [--candidate <ref>] [--deep] [--format {json,text}] [--explain-selection]
```

- One positional reference: the transformation the user expected to hit. Same reference forms as `transformation-diff`.
- `--candidate <ref>` — optional. If supplied, skips the heuristic and diffs against this candidate. Useful when the heuristic chose poorly or when the user already knows what they want to compare against.
- `--explain-selection` — emit the heuristic's score for the chosen candidate and the top runners-up (count is an implementer choice; recommend 3). When `--candidate` is supplied, this flag is a no-op (explicitly: no error, just nothing extra to explain).
- The output is the **composition of axis 1 (lookup state) and axis 2 (candidate diff)**, never one substituting for the other. See [Output schema](#output-schema) below.

### Text-format rendering (human-facing)

The `text` format is meant to be glanced at. Suggested layout for `why-not`:

```
tf_checksum: <hex>
lookup state: NOT_PRESENT          (queried across 2 endpoints: foo.db, bar.db)
candidate:    <hex>                (selection score: 0.72; 2 runners-up; --explain-selection for details)
identity-relevant: yes (3 of 4 entries are plain)

diff:
  [plain]  value_differs  code                <hex_A>  <hex_B>
  [plain]  key_only_in_input    arg.c
  [plain]  value_differs  arg.b               <hex_A>  <hex_B>
  [dunder] value_differs  __env__             <hex_A>  <hex_B>
```

For `PRESENT_AS_HIT` the candidate and diff sections are omitted; the tool says explicitly "this is a cache hit on the queried endpoint set — your miss is elsewhere".

For `IRREPRODUCIBLE` the tool lists row count and `result_checksum` set and notes that no candidate diff is run by default.

For `PRESENT_RESULT_UNAVAILABLE` the tool prints the result identity and the reason the buffer is not materializable / re-derivable, and adds the explicit hint: "this is not an identity miss — searching for a code change will not explain it."

For `--format text` on a dunder-only diff: prepend a banner line saying the candidate does not explain the miss, before the diff entries.

## Output schema

The structured output (CLI `--format json`, Python API return value) is the contract. Schema below is normative.

### `why-not` output (top level)

```jsonc
{
  "tool": "why-not",
  "version": "<semver>",                 // schema version; bump on breaking change
  "input": {
    "tf_checksum": "<hex>",              // resolved from the user's reference
    "reference_form": "tf_checksum" | "dict_path" | "definition_path",
    "endpoint_set": ["<spec>", ...]
  },
  "lookup_state": {
    "state": "NOT_PRESENT" | "IRREPRODUCIBLE" | "PRESENT_RESULT_UNAVAILABLE" | "PRESENT_AS_HIT",
    "per_endpoint": [
      { "endpoint": "<spec>", "state": "<one of the four>", "details": { ... } }
    ],
    "details": { ... }                   // shape varies by state; see below
  },
  "candidate": null | {
    "tf_checksum": "<hex>",
    "selection_score": <float in [0,1]>,
    "explanation": null | { ... },       // populated iff --explain-selection
    "runners_up": [] | [ { "tf_checksum": "<hex>", "selection_score": <float> }, ... ]
  },
  "diff": null | {                       // populated iff lookup_state.state == "NOT_PRESENT" AND candidate != null
    "identity_relevant": <bool>,
    "entries": [
      {
        "side": "key_only_in_input" | "key_only_in_candidate" | "value_differs",
        "key": "<dotted key path>",
        "classification": "plain" | "dunder",
        "value_input": "<hex>" | null,   // null for key_only_in_candidate
        "value_candidate": "<hex>" | null,
        "deep": null | { ... }            // populated iff --deep and content fetchable
      },
      ...
    ]
  },
  "warnings": [ "<string>", ... ],       // e.g., "candidate diff has only dunder entries"
  "timing": { "wall_ms": <int>, ... }    // present iff --verbose
}
```

### `lookup_state.details` shape per state

- `NOT_PRESENT` — `{}`.
- `IRREPRODUCIBLE` — `{ "row_count": <int>, "result_checksums": ["<hex>", ...] }`.
- `PRESENT_RESULT_UNAVAILABLE` — `{ "result_checksum": "<hex>", "reason": "<short tag>" }` where reason is one of e.g. `not_in_bufferdir`, `scratch_no_producer_in_scope`, `evicted` (exact vocabulary is an implementer choice but must be enumerable and documented).
- `PRESENT_AS_HIT` — `{ "result_checksum": "<hex>", "served_by": "<endpoint spec>" }`.

### Per-entry `deep` shape (only when `--deep`)

```jsonc
{
  "kind": "text_diff" | "json_diff" | "checksum_fallback",
  "body": "<presentation-appropriate>",   // unified diff text, structured json diff, or null for fallback
  "fallback_reason": null | "<short tag>" // populated only when kind == "checksum_fallback"
}
```

### `transformation-diff` output (top level)

```jsonc
{
  "tool": "transformation-diff",
  "version": "<semver>",
  "input_A": { "tf_checksum": "<hex>", "reference_form": "...", "source_endpoint": "<spec>"|null },
  "input_B": { ... },
  "identity_relevant": <bool>,
  "entries": [ ... ],     // same shape as why-not's diff.entries
  "warnings": [ ... ],
  "timing": { ... }
}
```

`transformation-diff` does not include lookup state or candidate selection. It is purely the diff between two dicts.

## Python API shape

```python
from seamless_share.why_not import why_not, transformation_diff, Reference, EndpointSpec

result = why_not(
    Reference.from_tf_checksum("..."),
    endpoints=[EndpointSpec.from_str("clusters://lab"), EndpointSpec.from_path("local.db")],
    candidate=None,
    deep=False,
    explain_selection=False,
)
# result is a typed dataclass mirroring the JSON schema above
assert result.lookup_state.state in {"NOT_PRESENT", "IRREPRODUCIBLE", "PRESENT_RESULT_UNAVAILABLE", "PRESENT_AS_HIT"}
```

Final naming is an implementer choice; tests pin behavior, not symbol names.

## Behavioral contracts (must hold)

These mirror Pattern 2's "Must hold" and are repeated here as the API-level acceptance criteria.

1. **Read-only.** Across all code paths, no `Transformation`, `RevTransformation`, `MetaData`, or `IrreproducibleTransformation` row is created, modified, or deleted; no buffer is written to any bufferdir or hashserver. The tools must not contain a write code path.
2. **Lookup state is always populated.** Every `why-not` result names exactly one of the four states for the union view, plus a per-endpoint breakdown.
3. **Lookup state and candidate diff are independent fields.** The diff field is populated iff `lookup_state.state == NOT_PRESENT` and a candidate was selected (or supplied via `--candidate`). The tool must never collapse "I have a diff" into "I have a miss" or vice versa.
4. **Diff is complete.** Every key-level difference between the input dict and the candidate dict appears in `entries`. No entry is collapsed, prioritized, marked "primary", or hidden. Two entries with the same key but different sides are not merged.
5. **Classification is correct.** Each entry's `classification` follows from [identity-and-caching.md](seamless/docs/agent/contracts/identity-and-caching.md). `identity_relevant` is `true` iff at least one entry is `plain`.
6. **Deep best-effort.** With `--deep`, an unavailable buffer falls back to `checksum_fallback` for that entry; the run does not fail. Exit code is `4` (informational) unless `--deep-best-effort` was passed, in which case `0`.
7. **Endpoint union semantics.** With multiple endpoints, the union rules from Pattern 2 apply: `NOT_PRESENT` only if absent from all; `IRREPRODUCIBLE` if any endpoint reports it; otherwise the most-resolvable state across endpoints wins. Per-endpoint provenance is always preserved in `lookup_state.per_endpoint`.
8. **Heuristic determinism.** Given the same input reference and the same database state, `why-not` selects the same candidate, returns the same diff, and emits byte-identical JSON output. The selection heuristic does not depend on iteration order of any unordered collection.
9. **Heuristic refusal.** If no transformation in the union of endpoints shares at least one plain-key value with the input, `why-not` returns `lookup_state.state == NOT_PRESENT` with `candidate == null` and `diff == null`. It does not invent a candidate to diff against.

## Must reject (API-level)

- `transformation-diff` invoked with anything other than exactly two references — exit `2`.
- `why-not` or `transformation-diff` invoked with no endpoint and no inline dict that obviates lookup — exit `2` with a message naming the missing piece.
- `why-not` invoked against an endpoint set whose `Transformation` row count is zero — exit code `0`, but `lookup_state.state == NOT_PRESENT`, `candidate == null`, and a warning `empty_haystack` is emitted. (No haystack to search; not a usage error.)
- Any flag combination that would write to a database or bufferdir endpoint — these must not be expressible at the CLI or in the Python API.

## Test plan

The test plan has three tiers:

1. **Schema/contract tests** — assert the structured output shape and the cross-cutting invariants. Independent of fixtures.
2. **Scenario tests** — concrete user actions from the [Common scenarios](usage-patterns.md#common-scenarios-and-what-the-diff-shows) list. Each scenario is a fixture (a small `seamless.db` + companion bufferdir) plus an assertion about diff entries.
3. **Property/regression tests** — read-only assertions, idempotence, determinism, exit-code matrix.

Implementers may split into more files; the assertions below must all exist somewhere in the suite.

### Test fixtures (build once, reuse)

A small generated `seamless.db` (and bufferdir) per scenario. Each fixture is a Python script under `tests/fixtures/` that, when run, materializes a fresh DB+bufferdir into a temp directory and registers it. Fixture scripts are themselves tested by a smoke test (`make fixture-X`).

Fixtures required (minimum set; numbering matches scenarios below):

- `F1` — single `@direct` function with two parameters; cached.
- `F2` — `F1`'s function plus a third parameter `c`, called with a value; cached.
- `F3` — `F1` with parameter `b` renamed to `x`; cached.
- `F4` — `F1` with a whitespace/comment-only edit; cached.
- `F5` — `F1` called with a different value for `b`; cached.
- `F6` — chain `X → Y → Z` where editing the upstream `X` changes `Y`'s and `Z`'s input pins; cached at two snapshots.
- `F7` — pair `(@direct, @delayed)` of the same function; both cached.
- `F8` — module/closure inclusion variant: one version with `objects=[helper]`, one with `helper` inlined.
- `F9` — pair differing only in `__env__`.
- `F10` — pair differing only in `__compilation__`.
- `F11` — `seamless-run` invocations differing in `--metavar`.
- `F12` — `seamless-run` invocations differing in command string.
- `F13` — `seamless-run` invocations switching a pin between `file.npy` and `file.npy.zst` (same canonical buffer).
- `F14` — `Transformation` row whose `result_checksum` is recorded but whose buffer is absent (scratch, no producer in scope).
- `F15` — `IrreproducibleTransformation` rows for a given `tf_checksum`.
- `F16` — empty DB (zero `Transformation` rows).
- `F17` — two endpoints, the queried `tf_checksum` is `NOT_PRESENT` in one and `PRESENT_AS_HIT` in the other.
- `F18` — implicit closure case: a function whose body references a module-level mutable that changed, but whose plain keys did not. The diff is expected to be empty; the test asserts the tool surfaces this honestly rather than declaring "nothing wrong".

Fixtures may reuse compiled-in helpers; their *content* is what matters for tests.

### Cross-cutting schema/contract tests

Numbered against Pattern 2's [Must test](usage-patterns.md#must-test-1) list 21–38; this plan refines and adds the test mechanics.

- **C1 (≈ Pattern 2 test 21).** Identical-pair: `transformation-diff F1 F1` ⇒ `entries == []`, `identity_relevant == false`, exit `0`.
- **C2 (≈ 22).** Plain-only: feed two dicts differing in one plain key ⇒ exactly one entry, `classification == plain`, `identity_relevant == true`.
- **C3 (≈ 23).** Dunder-only on `F9`: exactly one entry on `__env__`, `classification == dunder`, `identity_relevant == false`. In `why-not text` output, the banner "candidate does not explain the miss" appears before the entry. The structured output includes the warning string (stable tag, e.g. `dunder_only_diff`).
- **C4 (≈ 24).** Mixed plain+dunder: both classifications present in entries; `identity_relevant == true`; warning string is *not* emitted.
- **C5 (≈ 25).** Key-only-in-side: `transformation-diff F1 F2` ⇒ entries include a `key_only_in_B` (the new pin `c` only in `F2`) and a `value_differs` on `code`. Asserts exact set of entries (no merge, no collapse).
- **C6 (≈ 26).** Lookup `NOT_PRESENT`: query a synthesized random `tf_checksum` against `F1`'s DB ⇒ `lookup_state.state == NOT_PRESENT`; with no candidate forced, `candidate == null`; with `--candidate <F1's tf_checksum>`, diff is populated.
- **C7 (≈ 27).** Lookup `IRREPRODUCIBLE`: query against `F15` ⇒ `lookup_state.state == IRREPRODUCIBLE`, details include `row_count` and `result_checksums`. By default, `candidate == null` and `diff == null` even when a similarly-shaped transformation exists in the cache.
- **C8 (≈ 28).** Lookup `PRESENT_RESULT_UNAVAILABLE`: query against `F14` ⇒ `state == PRESENT_RESULT_UNAVAILABLE`, details include `result_checksum` and a `reason` tag. No diff is run; the human-format output explicitly steers the user away from looking for a code change.
- **C9 (≈ 29).** Lookup `PRESENT_AS_HIT`: query a `tf_checksum` actually cached ⇒ `state == PRESENT_AS_HIT`; structured output makes this unambiguous; human-format output explicitly says "this is a cache hit".
- **C10 (≈ 30).** Canonical multi-difference: query `F2`'s `tf_checksum` against `F1`'s DB ⇒ `lookup_state.state == NOT_PRESENT`, `candidate == F1`, diff has at least two entries: one `key_only_in_input` for the new pin on `c`, one `value_differs` on `code`. Both classified `plain`. `identity_relevant == true`. No entry is marked "primary". Exact entry count is `2` (no spurious entries).
- **C11 (≈ 31).** `--deep` on `code`: entry's `deep.kind == "text_diff"`; body is a unified diff that contains the changed line(s).
- **C12 (≈ 32).** `--deep` with missing buffer: tooling synthesizes a fixture where the candidate's `code` buffer is evicted; entry falls back to `kind == "checksum_fallback"` with a `fallback_reason`; run does not crash. Exit code is `4` without `--deep-best-effort` and `0` with it.
- **C13 (≈ 33).** Read-only assertion: hash the DB file and every byte-file in the bufferdir before and after every test. They must be byte-identical post-run.
- **C14 (≈ 34).** Heuristic determinism: run `why-not` twice on the same input + DB ⇒ byte-identical JSON output (including ordering of `entries`, `runners_up`, `per_endpoint`).
- **C15 (≈ 35).** Heuristic refusal: build a DB where no row shares any plain-key value with the input; `why-not` returns `candidate == null`, `diff == null`, `lookup_state.state == NOT_PRESENT`. The text format says so plainly.
- **C16 (≈ 36).** Endpoint union: feed `F17`'s two endpoints; lookup-state union is `PRESENT_AS_HIT` (the more-resolvable state wins per the rules); `per_endpoint` lists both, one `NOT_PRESENT`, one `PRESENT_AS_HIT`. Reversing the order of `--endpoint` flags produces the same union state (commutativity), with `per_endpoint` order following argv.
- **C17 (≈ 37).** Dunder-only warning: ensure the structured `warnings` array contains the dunder-only tag; assert against the stable tag string, not the prose.
- **C18 (≈ 38).** No cascade: on `F6`, query the downstream `Z`'s new-snapshot `tf_checksum` against the old-snapshot DB ⇒ diff is a single-step diff on one differing input pin; the tool does **not** recurse to diagnose `Y` or `X`.

### Scenario tests

These bind the [Common scenarios](usage-patterns.md#common-scenarios-and-what-the-diff-shows) to assertions on the structured output. Each test is "fixture + invocation + expected entry shape (sides, classifications, count)". Not all are checking *cache hit/miss* — some assert the *converse* (e.g., decorator swap is a hit).

- **S1 (add a parameter).** Fixture `F1` → `F2`. Identical to C10. The canonical multi-difference case.
- **S2 (remove a parameter).** Inverse of S1: query `F1`'s `tf_checksum` against `F2`'s DB with `--candidate F2` ⇒ one `key_only_in_candidate` for the dropped pin + one `value_differs` on `code`.
- **S3 (rename a parameter).** Fixture `F3` against `F1` ⇒ three entries: `key_only_in_input` (new name), `key_only_in_candidate` (old name), `value_differs` on `code`. Assert exactly three, not one.
- **S4 (default-value-only edit).** Fixture adds a default in the signature, call site unchanged ⇒ one `value_differs` on `code`, no key set change. Assert no entries on input pins.
- **S5 (whitespace / comment edit).** Fixture `F4` against `F1` ⇒ one `value_differs` on `code`. With `--deep`, the text-diff body should be small (test asserts the diff has fewer than N hunks, where N is liberal — the point is the assertion can distinguish "code differs" from "code differs only cosmetically").
- **S6 (algorithmic change of same shape).** Same as S5 structurally — one `value_differs` on `code`. Test asserts the tool produces *the same shape* of output as S5, not that it can tell them apart.
- **S7 (different value on same parameter).** Fixture `F5` against `F1` ⇒ one `value_differs` on the input pin `b`; `code` unchanged.
- **S8 (upstream `result_checksum` change).** Fixture `F6` two snapshots. Querying the downstream against the wrong-snapshot DB ⇒ one differing input pin, classification `plain`. Tool does not recurse upstream.
- **S9 (normalized-to-same-buffer).** Fixture passes two argument values that normalize to the same checksum ⇒ `lookup_state.state == PRESENT_AS_HIT`, `entries == []`. The test explicitly checks the tool does not invent a diff.
- **S10 (module/closure inclusion change).** Fixture `F8` ⇒ at least one entry on the modules-related key; `code` may or may not also differ, but the test only asserts *the presence* of the modules entry.
- **S11 (implicit-closure change).** Fixture `F18` ⇒ `entries == []`. Test asserts: (a) structured output has empty entries; (b) the human-format output includes a hint mentioning implicit closure (assert against a stable hint tag in the JSON `warnings` array, e.g. `empty_diff_check_implicit_closure`, rather than English prose).
- **S12 (celltype change).** Pin changes from `text` to `bytes`. Test asserts an entry on the celltype-bearing key; classification is whatever the contracts say (test reads from the contract, doesn't hardcode).
- **S13 (`__env__` change).** Fixture `F9` ⇒ one `dunder` entry on `__env__`; `identity_relevant == false`; warning `dunder_only_diff`.
- **S14 (`__compilation__` change).** Fixture `F10` ⇒ same shape as S13 but on `__compilation__`.
- **S15 (decorator swap `@direct` ↔ `@delayed`).** Fixture `F7` ⇒ `lookup_state.state == PRESENT_AS_HIT`, `entries == []`.
- **S16 (`seamless-run` command string change).** Fixture `F12` ⇒ one entry on the command-bearing plain key.
- **S17 (`seamless-run` `--metavar` change).** Fixture `F11` ⇒ one entry on the metavar-bearing dunder key; warning `dunder_only_diff`.
- **S18 (`seamless-run` compression suffix on pin name).** Fixture `F13` ⇒ at least one entry reflecting the pin-name difference, despite identical canonical buffer checksums. Test asserts the diff is *non-empty* — the surprising case the doc calls out.
- **S19 (`seamless-run` argument typing change).** Pin's celltype-equivalent changes; entry on the affected pin. Same caveat as S12 about classification.
- **S20 (empty haystack).** Fixture `F16`, an empty DB ⇒ exit `0`, `lookup_state.state == NOT_PRESENT`, `warnings` contains `empty_haystack`, `candidate == null`.

### Property and regression tests

- **P1 (read-only invariance).** Hash every file under the DB and bufferdir before each test in the suite; assert unchanged after. Run on a randomized sample of 20+ scenarios.
- **P2 (idempotence on re-invoke).** Invoke `why-not` twice in succession; assert byte-identical JSON output (already partially in C14, but here run across all scenario tests as a property).
- **P3 (no-write-path static check).** Grep / AST check: assert that no symbol importable from `seamless_share.why_not` calls a known write API on `seamless.db` / hashserver. This is enforced as a unit test, not just code review.
- **P4 (exit-code matrix).** Parametrize across the documented exit codes (0, 2, 3, 4). For each, construct the minimal CLI invocation that should produce it, assert the exit code.
- **P5 (CLI ↔ Python API equivalence).** For each scenario fixture, run the CLI with `--format json` and the Python API; assert the structured outputs are equal (modulo `timing`, which is non-deterministic).
- **P6 (schema validity).** Validate every structured output against a JSON Schema kept in `seamless-share/why_not/schema.json`. The schema is part of the tool's public contract.
- **P7 (`--explain-selection` is purely additive).** Same invocation with and without `--explain-selection` differs only by populated `candidate.explanation` and `candidate.runners_up`; all other fields identical. The flag is not allowed to change candidate selection.
- **P8 (`--candidate` overrides heuristic).** With `--candidate <X>`, the result's `candidate.tf_checksum == X`, the diff is computed against `X`, and `candidate.selection_score` is `null` (or a documented sentinel) since no heuristic ran.
- **P9 (warning tags are stable).** The set of warning tags emitted by the tool is enumerable. Test asserts the tool emits one of the documented tags; new tags require a doc update.
- **P10 (large-diff resilience).** Synthesize a transformation with 200+ input pins, half differing; tool produces all 100+ entries in the output without truncation. (Catches accidental pagination or truncation.)

### Tests that must NOT exist

The plan is also a list of things implementers should resist adding:

- Tests that assert a *primary* difference. There is no primary difference.
- Tests that assert cascade behavior in v1.
- Tests that assert metadata divergence is part of the diff.
- Tests that assert `--deep` "explains" the miss beyond producing content. Identity is checksum equality; `--deep` is presentation.
- Tests asserting that a dunder-only diff is treated as a near-miss "almost the same" — it is treated as *the candidate was wrong*.

## Failure shapes the test suite must cover

Beyond the matrix above, these failure shapes must each have a dedicated test:

1. Endpoint unreachable mid-run (one endpoint of two) — exit `3`, error message identifies which endpoint, partial per-endpoint provenance retained where possible.
2. Endpoint reachable but malformed (e.g., wrong schema version) — exit `3`, error tag distinguishes from "unreachable".
3. `--deep` with a buffer that requires fingertipping but no producer is in scope — falls back per C12, does not silently re-execute, does not write any record.
4. User supplies a path to a transformation definition that fails to load — exit `2` with a message identifying the failing path; do not partially proceed.
5. Two references resolving to the same `tf_checksum` — `transformation-diff` returns empty entries (not an error); `why-not` is non-applicable here (single reference).

## Documentation deliverables alongside the code

- `seamless-share/why_not/README.md` — user-facing quickstart, links back to [Pattern 2](usage-patterns.md#pattern-2-cache-miss-diagnosis-why-not).
- `seamless-share/why_not/schema.json` — JSON Schema for the structured output.
- `seamless-share/why_not/CHANGELOG.md` — semver, schema version bumps, warning-tag additions.
- Man-page-style `--help` text for both subcommands; the `--help` output is also a test fixture (regenerating it from argparse output should be byte-stable).

## Acceptance criteria for v1

The tool is ready for v1 cut when:

1. All tests above pass.
2. The schema validator (P6) is wired into CI.
3. The static no-write-path check (P3) is wired into CI.
4. Running `why-not` on a real local `seamless.db` produced by a small Seamless project (a separate manual smoke test the implementer documents) gives a sensible answer for at least one deliberately-introduced cache miss of each scenario category that appears in the project.
5. The schema and CLI are reviewed against [Pattern 2](usage-patterns.md#pattern-2-cache-miss-diagnosis-why-not) line-by-line; any deviation is either fixed or recorded as an intentional refinement in this file.
