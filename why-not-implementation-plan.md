# Implementation Plan: `why-not` and `transformation-diff`

This is a handoff-ready implementation plan derived from `why-not-plan.md` and
the Pattern 2 contract in `usage-patterns.md`. It is written for an implementer
who has not read the source plan yet.

The contract source of truth is still:

- `seamless-share/why-not-plan.md`
- `seamless-share/usage-patterns.md`, Pattern 2: cache-miss diagnosis
- `seamless/docs/agent/contracts/identity-and-caching.md`

If this implementation plan conflicts with those contracts, the contracts win,
except for the candidate-selection refinement called out below: v1 should use a
near-match threshold instead of accepting a candidate merely because it shares
one plain-key value.

## Current Repository State

`seamless-share` is currently a stub repository with documentation only:

- `README.md`
- `usage-patterns.md`
- `why-not-plan.md`
- `LICENSE.txt`

There is no Python package, CLI entry point, test suite, or `pyproject.toml` in
`seamless-share` yet. Build the package structure from scratch inside this repo.
The adjacent workspace contains reusable APIs and models:

- `seamless-database/database_models.py` defines SQLite models:
  `Transformation`, `RevTransformation`, `MetaData`,
  `IrreproducibleTransformation`, and helpers such as `db_init`.
- `seamless-remote/seamless_remote/database_client.py` has read-only client
  methods for remote database services:
  `get_transformation_result`, `get_rev_transformations`,
  `get_execution_record`, and `get_irreproducible_records`.
- `seamless-core/seamless/checksum/calculate_checksum.py` exposes
  `calculate_dict_checksum`, compatible with plain-cell dict checksums.
- `seamless-transformer/seamless_transformer/pretransformation.py` defines
  `NON_CHECKSUM_ITEMS`, which is the current implementation-level list of
  dunder/non-checksum transformation keys.

Do not assume this workspace root is a single git repository. `seamless-share`
is its own git repository.

## V1 Deliverable

Implement two read-only tools as separate layers:

1. `seamless-share transformation-diff`
   - Low-level primitive.
   - Compares exactly two transformation references.
   - Has no candidate-selection logic.
   - Reports complete key-level differences with `plain` vs `dunder`
     classification.

2. `seamless-share why-not`
   - User-facing diagnostic.
   - Resolves one expected transformation reference.
   - Always reports lookup state for the queried endpoint set.
   - When lookup state is `NOT_PRESENT`, selects or accepts a candidate and
     calls the `transformation-diff` core to produce the diff.

Both tools must expose:

- CLI surface.
- Python API surface:
  `from seamless_share.why_not import why_not, transformation_diff, Reference, EndpointSpec`

The CLI should use the same core as the Python API. JSON CLI output must be the
serialized form of the Python dataclass result.

## Non-Negotiable Constraints

- Read-only by construction. No code path may write to a database, bufferdir, or
  hashserver.
- `why-not` must call `transformation-diff` for diff computation. It must not
  contain independent diff logic.
- Lookup state and candidate diff are independent fields. Do not collapse them
  into one result enum.
- Diffs are complete key-level lists. Do not pick a primary cause, do not hide
  entries, and do not merge opposite-side key entries.
- `identity_relevant` is `true` if and only if at least one diff entry is
  classified `plain`.
- `--deep` is presentation only. It must never relax identity semantics and must
  not write missing content into any cache.
- No v1 cascade explanation. Upstream result checksum changes are reported as
  one differing input pin.
- Do not consult `MetaData` for identity diffing. Metadata may be useful for
  execution diagnostics elsewhere, but not here.

## Candidate-Selection Refinement

The source plan says the heuristic should refuse only when no transformation in
the union of endpoints shares at least one plain-key value with the input. That
is too loose for v1: a single matching plain-key value can select a wildly
unrelated transformation.

Use a near-match threshold instead:

- Compute `plain_delta_count` between input and candidate over plain keys only.
- Count one delta for each plain key present on only one side.
- Count one delta for each plain key present on both sides whose normalized
  value differs.
- A candidate is eligible only if `plain_delta_count <= max_plain_delta`.
- Set `max_plain_delta = 3` by default.
- Keep this default in one named constant, e.g. `DEFAULT_MAX_PLAIN_DELTA = 3`.
- It is acceptable to expose `--max-plain-delta <N>` later, but do not add that
  flag in v1 unless tests pin it.

Refusal rule:

- If no transformation in the endpoint union satisfies the near-match threshold,
  `why-not` returns `lookup_state.state == NOT_PRESENT`, `candidate == null`,
  and `diff == null`.
- It must not invent a candidate to diff against.
- Emit a stable warning tag, e.g. `candidate_not_near_enough`.
- The old "at least one shared plain-key value" condition may be used only as a
  cheap prefilter, never as the final eligibility test.

This refinement affects tests C15/P8-style candidate behavior: build fixtures
where a candidate shares one plain-key value but has more than three plain
differences, and assert refusal.

## Proposed Package Layout

Create this structure in `seamless-share`:

```text
seamless-share/
  pyproject.toml
  seamless_share/
    __init__.py
    cli.py
    why_not/
      __init__.py
      api.py
      models.py
      references.py
      endpoints.py
      local_database.py
      remote_database.py
      lookup.py
      diff.py
      selection.py
      deep.py
      render.py
      errors.py
      schema.json
      README.md
      CHANGELOG.md
  tests/
    fixtures/
    test_cli.py
    test_contract_schema.py
    test_diff.py
    test_lookup.py
    test_selection.py
    test_deep.py
    test_readonly.py
    test_scenarios.py
```

Use `argparse` for the CLI unless the repo establishes a different convention
before implementation starts. Keep dependencies minimal:

- Required: `seamless-core`, `seamless-database`, `seamless-remote`, `peewee`
  only if imported directly for local DB access, and `jsonschema` for tests or
  runtime schema validation if desired.
- Optional for tests: `pytest`.

Suggested `pyproject.toml` entry point:

```toml
[project.scripts]
seamless-share = "seamless_share.cli:main"
```

## Public Data Model

Implement typed dataclasses in `seamless_share/why_not/models.py`. Keep field
names identical to the JSON schema so serialization is boring and stable.

Core enums:

- `ReferenceForm`: `tf_checksum`, `dict_path`, `definition_path`
- `LookupStateName`: `NOT_PRESENT`, `IRREPRODUCIBLE`,
  `PRESENT_RESULT_UNAVAILABLE`, `PRESENT_AS_HIT`
- `DiffSide`: `key_only_in_input`, `key_only_in_candidate`, `value_differs`
  for `why-not`; use A/B internally and normalize labels at the API boundary.
- `Classification`: `plain`, `dunder`
- `DeepKind`: `text_diff`, `json_diff`, `checksum_fallback`

Dataclasses:

- `Reference`
  - User-facing constructor helpers:
    `from_tf_checksum`, `from_dict_path`, `from_definition_path`, `from_str`.
  - Store original value, reference form, resolved `tf_checksum`, and resolved
    transformation dict when available.
- `EndpointSpec`
  - User-facing constructor helpers:
    `from_str`, `from_path`.
  - Store raw spec, endpoint kind, resolved path or URL, and display name.
- `DiffEntry`
- `DeepDiff`
- `TransformationDiffResult`
- `LookupState`
- `EndpointLookupState`
- `CandidateInfo`
- `WhyNotResult`

Serialization rules:

- JSON output must be deterministic:
  - sort object keys with `json.dumps(..., sort_keys=True)`
  - deterministic list ordering
  - no timing field unless `--verbose`
- Warning values should be stable tags, not prose. Start with:
  - `dunder_only_diff`
  - `empty_haystack`
  - `empty_diff_check_implicit_closure`
  - `deep_checksum_fallback`
  - `candidate_not_found`
  - `candidate_not_near_enough`

## CLI Contract

Implement:

```text
seamless-share transformation-diff <ref-A> <ref-B>
  [--endpoint <spec> ...]
  [--config <path>]
  [--deep]
  [--deep-best-effort]
  [--format {json,text}]
  [--quiet|-q]
  [--verbose|-v]
```

Default format: `json`.

Implement:

```text
seamless-share why-not <ref>
  [--endpoint <spec> ...]
  [--config <path>]
  [--candidate <ref>]
  [--deep]
  [--deep-best-effort]
  [--format {json,text}]
  [--explain-selection]
  [--quiet|-q]
  [--verbose|-v]
```

Default format: `text`.

Exit codes:

- `0`: command ran and produced output. Diff entries, misses, and hits are
  normal findings, not errors.
- `2`: usage error, bad argv, missing required endpoint/ref, unknown endpoint,
  definition path cannot load.
- `3`: endpoint error, unreachable endpoint, corrupt database, malformed schema,
  permission denied.
- `4`: `--deep` requested and at least one buffer was unavailable. Output is
  still produced. Downgrade to `0` when `--deep-best-effort` is passed.
- `1`: unexpected internal failure only.

## Reference Resolution

Implement in `references.py`.

Accepted reference forms:

1. A 64-hex `tf_checksum`.
2. A path to a JSON file containing an unwrapped transformation dict. The file
   may include dunder keys. Compute the plain transformation checksum from the
   dict after excluding dunder/non-checksum keys.
3. A path to a transformation definition that tooling can evaluate to a
   transformation dict. For v1, define the smallest supportable subset:
   - Prefer JSON dict paths first.
   - Add Python definition support only if there is a clear existing API to
     construct without executing the transformation.
   - If no safe evaluator exists, document `definition_path` as accepted only
     for explicitly supported file types and return exit `2` for unsupported
     paths.

Checksum computation:

- The transformation checksum is over the unwrapped transformation dict's plain
  keys only.
- Use the same canonical JSON/checksum logic as Seamless. Start with
  `seamless.checksum.calculate_checksum.calculate_dict_checksum`.
- Exclude dunder/non-checksum keys according to the contract and current
  implementation. Importing `NON_CHECKSUM_ITEMS` from
  `seamless_transformer.pretransformation` is acceptable, but wrap it in one
  local function so future contract changes are localized.

Important: a `tf_checksum` reference can only be diffed if the transformation
dict can be retrieved from an endpoint or supplied inline. If the endpoint only
stores `tf_checksum -> result_checksum`, add the missing read API described
below before claiming checksum references are fully supported.

## Endpoint Resolution

Implement in `endpoints.py`.

Support these endpoint specs:

1. Local SQLite database path:
   - Plain path ending in `.db` or existing file path.
   - Open read-only with SQLite URI mode where possible:
     `file:/path/to/seamless.db?mode=ro`
   - Never call `db_init(..., create_tables=True)` on a diagnostic endpoint.
2. Remote database URL:
   - Use `DatabaseClient(readonly=True)` from `seamless_remote.database_client`.
   - Never instantiate a non-readonly client.
3. Named cluster from `seamless-config`:
   - Resolve through the same configuration path that existing Seamless tools
     use.
   - Pass `mode="ro"` or equivalent read-only configuration.
   - Honor `--config <path>` if supported by `seamless-config`; otherwise fail
     with exit `2` and a clear message.

Expose one common read-only protocol:

```python
class DatabaseEndpoint:
    spec: EndpointSpec
    def get_transformation_result(tf_checksum: str) -> str | None: ...
    def get_transformation_dict(tf_checksum: str) -> dict | None: ...
    def iter_transformation_dicts() -> Iterable[TransformationRecord]: ...
    def get_irreproducible_records(tf_checksum: str) -> list[dict]: ...
    def buffer_available(checksum: str) -> bool: ...
    def get_buffer(checksum: str) -> bytes | None: ...
```

If existing database services do not expose transformation dict retrieval,
implement local SQLite support first and add a read-only remote API extension
before marking remote checksum refs complete. Do not fake this by re-executing
or materializing transformations.

## Required Database Read API Gap

Pattern 2 requires diffing transformation dicts, but the existing normal cache
model shown in `seamless-database/database_models.py` stores only:

- `Transformation.checksum`
- `Transformation.result`

It does not visibly store the transformation dict. Execution records in
`MetaData` may contain `tf_checksum` and `result_checksum`, but the contract says
metadata is not identity input and must not be used as the transformation dict.

Before full v1, confirm where transformation dicts are persisted. If they are
not persisted, implement one of these read-only-compatible prerequisites:

1. Preferred: add a canonical `TransformationDict` table or buffer reference in
   the producer path, then expose a read-only `GET transformation_dict` endpoint.
2. Acceptable v1 staging: support full diffs only when references include inline
   dict files, and have checksum-only references fail with exit `3` or a stable
   endpoint capability error when the dict cannot be retrieved.

Do not silently degrade `transformation-diff` into comparing only
`tf_checksum -> result_checksum`; that would violate the contract.

## Lookup State Algorithm

Implement in `lookup.py`.

For each endpoint:

1. Query `IrreproducibleTransformation` rows first.
   - If rows exist: endpoint state is `IRREPRODUCIBLE`.
   - Details: row count and sorted unique result checksums.
2. Query `Transformation` by `tf_checksum`.
   - If absent: endpoint state is `NOT_PRESENT`.
   - If present: inspect `result_checksum`.
3. Determine result materializability.
   - If buffer is present or can be re-derived under read-only policy:
     `PRESENT_AS_HIT`.
   - If not materializable and not re-derivable:
     `PRESENT_RESULT_UNAVAILABLE`.
   - Details include `result_checksum` and a short reason tag such as
     `not_in_bufferdir`, `scratch_no_producer_in_scope`, or `evicted`.

Union view across endpoints:

1. If any endpoint is `IRREPRODUCIBLE`, union state is `IRREPRODUCIBLE`.
2. Else if any endpoint is `PRESENT_AS_HIT`, union state is `PRESENT_AS_HIT`.
3. Else if any endpoint is `PRESENT_RESULT_UNAVAILABLE`, union state is
   `PRESENT_RESULT_UNAVAILABLE`.
4. Else union state is `NOT_PRESENT`.

`NOT_PRESENT` means absent from all endpoints. Always preserve
`per_endpoint` in argv order.

Endpoint error behavior:

- If an endpoint is unreachable, corrupt, or permission denied, return exit `3`.
- Include the endpoint spec in the error.
- Preserve partial per-endpoint provenance if it was already collected before
  the failure.

## Diff Algorithm

Implement the primitive in `diff.py`.

Inputs:

- Two resolved transformation dicts.
- Labels for sides. Internally use A/B. For `why-not`, map A to `input` and B to
  `candidate`.
- Optional deep diff fetcher.

Steps:

1. Normalize both dicts:
   - Ensure keys are strings.
   - Preserve dunder keys in the dict for diffing.
   - Do not include `MetaData`.
2. Compute sorted key union using lexical ordering for deterministic output.
3. For each key:
   - If only in A: `key_only_in_A`.
   - If only in B: `key_only_in_B`.
   - If in both and normalized value differs: `value_differs`.
   - If equal: no entry.
4. Classification:
   - `dunder` if the key starts and ends with double underscores or is listed in
     the non-checksum/dunder key set.
   - Otherwise `plain`.
   - Keep the classification function central and tested against
     `identity-and-caching.md`.
5. Values:
   - Default depth reports checksums or absent values.
   - For a pin tuple `(celltype, subcelltype, checksum)`, report the checksum.
   - For dunder values that are direct dicts/strings rather than checksum refs,
     either compute a presentation checksum or serialize a stable JSON value
     under a documented field. Prefer matching the contract shape by reporting a
     checksum-like identity where possible.
6. `identity_relevant = any(entry.classification == "plain")`.
7. Warnings:
   - If entries are non-empty and all are dunder, emit `dunder_only_diff`.
   - If entries are empty in `why-not` candidate mode, emit
     `empty_diff_check_implicit_closure`.

Side naming:

- `transformation-diff` JSON uses `key_only_in_A`, `key_only_in_B`, and
  `value_differs` unless the final schema chooses the contract names exactly.
- `why-not` JSON must use `key_only_in_input`, `key_only_in_candidate`, and
  `value_differs` as in `why-not-plan.md`.

## Deep Diff Algorithm

Implement in `deep.py`.

Only run deep diff for entries where a checksum can be found for both sides.

1. Fetch buffers read-only from the endpoint set or configured bufferdir.
2. If either buffer is unavailable:
   - Attach `{"kind": "checksum_fallback", "body": null,
     "fallback_reason": "<tag>"}`.
   - Add warning `deep_checksum_fallback`.
   - Mark the command for exit `4` unless `--deep-best-effort` is set.
3. If both buffers decode as UTF-8 and the key is textual, emit unified diff:
   - `kind = "text_diff"`
   - stable context size, e.g. 3 lines
4. If both buffers parse as JSON, emit:
   - `kind = "json_diff"`
   - stable structural body
5. Otherwise emit checksum fallback with reason `binary_or_opaque`.

Never fingertip, recompute, upload, or cache missing buffers in the deep path.

## Candidate Selection Heuristic

Implement in `selection.py`. This belongs only to `why-not`.

Inputs:

- Resolved input transformation dict.
- All candidate transformation dicts from the union of endpoints.
- Optional `explain_selection`.

Eligibility and refusal:

- Compute `plain_delta_count` for every candidate as defined in
  "Candidate-Selection Refinement".
- A candidate is eligible only when `plain_delta_count <= 3` by default.
- If the endpoint set has zero `Transformation` rows, return
  `candidate = null`, `diff = null`, warning `empty_haystack`.
- If no transformation meets the threshold, return `candidate = null`,
  `diff = null`, warning `candidate_not_near_enough`.
- If no candidate shares any plain-key value at all, this is also a refusal; the
  emitted warning may be `candidate_not_found` or `candidate_not_near_enough`,
  but document the distinction and keep tests stable.

Scoring eligible candidates:

1. Split each dict into plain keys and dunder keys.
2. Count exact key/value matches on plain keys.
3. Count exact key overlap on plain keys.
4. Count `plain_delta_count`; lower is better.
5. Count exact key/value matches on dunder keys as a tie-breaker only.
6. Compute a deterministic score in `[0, 1]`. Suggested:
   - `delta_score = max(0, (max_plain_delta - plain_delta_count) / max_plain_delta)`
   - `plain_match_score = matching_plain_key_values / max(len(input_plain_keys), 1)`
   - `plain_key_overlap_score = overlapping_plain_keys / max(len(input_plain_keys), 1)`
   - `dunder_tiebreak = matching_dunder_key_values / max(len(input_dunder_keys), 1)`
   - final sort tuple:
     `(delta_score, plain_match_score, plain_key_overlap_score,
       dunder_tiebreak, candidate_tf_checksum)`
   - expose a documented float such as `delta_score` or a weighted combination;
     use the full tuple for deterministic ordering.
7. Sort candidates by the score tuple descending, with `candidate_tf_checksum`
   ascending as the final tie-breaker.
8. Return the top candidate and, when `--explain-selection` is set, the top 3
   runners-up with score components, including `plain_delta_count`.

`--candidate` behavior:

- Skip heuristic entirely.
- Resolve the supplied candidate reference.
- Set `candidate.tf_checksum` to the supplied candidate.
- Set `selection_score` to `null` or a documented sentinel because no heuristic
  ran.
- `--explain-selection` is a no-op when `--candidate` is supplied.

## Python API

Implement in `api.py` and export from `seamless_share/why_not/__init__.py`.

```python
def transformation_diff(
    ref_a: Reference | str,
    ref_b: Reference | str,
    *,
    endpoints: list[EndpointSpec | str] | None = None,
    deep: bool = False,
    deep_best_effort: bool = False,
    verbose: bool = False,
) -> TransformationDiffResult:
    ...

def why_not(
    ref: Reference | str,
    *,
    endpoints: list[EndpointSpec | str] | None = None,
    candidate: Reference | str | None = None,
    deep: bool = False,
    deep_best_effort: bool = False,
    explain_selection: bool = False,
    verbose: bool = False,
) -> WhyNotResult:
    ...
```

API errors:

- Raise typed exceptions from `errors.py`, e.g. `UsageError`, `EndpointError`,
  `DeepBufferUnavailable`.
- CLI translates exceptions to the documented exit codes.
- Do not call `sys.exit` outside `cli.py`.

## `why-not` Control Flow

Implement this exact shape:

1. Parse refs and endpoints.
2. Resolve the input reference enough to know `tf_checksum` and, if possible,
   the transformation dict.
3. Compute lookup state across the endpoint union.
4. Construct the top-level `WhyNotResult` with lookup state populated.
5. If lookup state is not `NOT_PRESENT`:
   - Do not run candidate selection.
   - Do not populate `diff`.
   - Render state-specific hints in text format.
6. If lookup state is `NOT_PRESENT`:
   - If `--candidate` supplied, resolve it and call the diff primitive.
   - Else run the deterministic near-match heuristic.
   - If heuristic refuses, leave `candidate` and `diff` null.
   - If a candidate exists, call `transformation_diff_core`.
7. Add warnings from candidate selection and diff.
8. Add timing only when verbose.
9. Serialize deterministically.

## Text Rendering

Implement in `render.py`. JSON is the contract; text is for humans.

For `why-not` text:

- Always show:
  - `tf_checksum`
  - lookup state
  - endpoint count/spec summary
- For `PRESENT_AS_HIT`:
  - Omit candidate/diff sections.
  - Say clearly: `this is a cache hit on the queried endpoint set - your miss is elsewhere`
- For `IRREPRODUCIBLE`:
  - Show row count and result checksum set.
  - Explain that candidate diff is not run by default.
- For `PRESENT_RESULT_UNAVAILABLE`:
  - Show result checksum and reason.
  - Include this exact required hint:
    `this is not an identity miss - searching for a code change will not explain it`
- For `NOT_PRESENT` with candidate:
  - Show candidate checksum and selection score if present.
  - In verbose or `--explain-selection` output, show `plain_delta_count`.
  - Show all diff entries in stable order.
- For `NOT_PRESENT` with no candidate:
  - Say that no near-enough candidate was found.
  - If available, mention the configured max plain delta.
- For dunder-only diff:
  - Prepend a banner saying the candidate does not explain the miss.
- Respect `--quiet` by suppressing optional explanatory lines only.
- Respect `--verbose` by adding per-endpoint provenance and timing.

For `transformation-diff` text:

- Show both refs, `identity_relevant`, warnings, and all entries.
- No lookup state and no candidate wording.

## JSON Schema

Keep `seamless_share/why_not/schema.json` as part of the public contract.

Minimum schemas:

- `WhyNotResult`
- `TransformationDiffResult`
- shared `DiffEntry`
- shared `DeepDiff`
- shared `LookupState`

CI/test suite must validate every JSON CLI output against this schema.

Use schema version `0.1.0` for the first implementation. Bump on breaking
output changes.

## Test Strategy

Use `pytest`. Keep tests layered so the primitive can be trusted independently
from endpoint and CLI work.

### Phase 1: Pure Unit Tests

Implement before any real DB fixture.

- Identical dict diff: no entries, `identity_relevant == false`.
- Plain-only diff: one plain entry, `identity_relevant == true`.
- Dunder-only diff: one dunder entry, `identity_relevant == false`,
  warning `dunder_only_diff`.
- Mixed diff: both classifications, `identity_relevant == true`.
- Key-only side tests in both directions.
- Entry order is deterministic.
- JSON serialization is byte-identical across repeated calls.
- `why-not` refuses heuristic selection when there is no near-enough candidate.
- `why-not` refuses a candidate that shares one plain-key value but exceeds
  `DEFAULT_MAX_PLAIN_DELTA`.
- `--candidate` bypasses heuristic and may diff a candidate outside the
  near-match threshold because the user explicitly asked for it.
- CLI usage errors produce exit `2`.

### Phase 2: Local SQLite Endpoint Tests

Build minimal generated fixture DBs under `tests/fixtures/`.

Fixtures must be created in temp directories. The fixture builder should return:

- path to `seamless.db`
- path to bufferdir if applicable
- named checksums and dicts needed by tests

Required fixtures from the source plan:

- F1: direct function with two parameters, cached.
- F2: F1 plus third parameter `c`, cached.
- F3: F1 with parameter `b` renamed to `x`, cached.
- F4: F1 with whitespace/comment-only edit, cached.
- F5: F1 with different value for `b`, cached.
- F6: chain `X -> Y -> Z`, two snapshots.
- F7: equivalent direct/delayed pair.
- F8: module/closure inclusion variant.
- F9: pair differing only in `__env__`.
- F10: pair differing only in `__compilation__`.
- F11: `seamless-run` invocations differing in `--metavar`.
- F12: `seamless-run` invocations differing in command string.
- F13: `seamless-run` compressed/uncompressed pin-name case.
- F14: transformation row exists but result buffer is absent.
- F15: irreproducible rows for a `tf_checksum`.
- F16: empty DB.
- F17: two endpoints, one missing and one hit.
- F18: implicit closure case where dict diff is empty.

Do not hand-author opaque checksum strings unless the test is specifically for
bad or random checksums. Prefer generating transformation dicts and checksums in
fixture builders.

### Phase 3: Contract Tests

Port the C1-C18 checks from `why-not-plan.md` directly, with the candidate
selection refinement applied to C15.

Must include:

- Lookup state tests for all four states.
- Endpoint union test, including reversed endpoint order.
- Deep diff text output on `code`.
- Deep missing-buffer fallback with exit `4` and `--deep-best-effort` exit `0`.
- Read-only before/after byte hashing of DB and bufferdir.
- Heuristic determinism, byte-identical JSON.
- Heuristic refusal when no candidate is near enough, including a case with one
  shared plain-key value but more than three plain deltas.
- No cascade on F6.
- Dunder-only warning tag.
- Empty haystack warning.

### Phase 4: Scenario Tests

Port S1-S20 from `why-not-plan.md`.

The scenario suite should assert structured output shape:

- exact entry count where the plan requires exactness
- side values
- key paths
- classification
- `identity_relevant`
- warning tags
- lookup state

Do not assert a primary difference. There is no primary difference in v1.

### Phase 5: Property and Regression Tests

Implement:

- P1 read-only invariance across a randomized sample of scenarios.
- P2 idempotence on repeated `why-not` calls.
- P3 static no-write-path test.
  - AST scan importable modules under `seamless_share.why_not`.
  - Fail on calls/imports to known write APIs:
    `set_transformation_result`, `set_execution_record`, `set_bucket_probe`,
    `undo_transformation_result`, Peewee `create`, `save`, `delete`, and HTTP
    `put`/`post` in endpoint code.
  - Allow fixture builders to write; production package only is scanned.
- P4 exit-code matrix.
- P5 CLI JSON equals Python API output, modulo timing.
- P6 schema validity.
- P7 `--explain-selection` is additive only.
- P8 `--candidate` override.
- P9 warning tags are documented and enumerable.
- P10 large diff with 200+ pins, no truncation.
- P11 candidate near-match threshold:
  - candidates with `plain_delta_count <= 3` may be selected
  - candidates with `plain_delta_count > 3` are refused unless explicitly
    supplied through `--candidate`

## Implementation Phases

### Phase A: Package Skeleton and Models

1. Add `pyproject.toml`.
2. Add package directories and exports.
3. Add dataclasses/enums and deterministic JSON serialization.
4. Add CLI parser with subcommands and stub implementations returning
   well-formed errors.
5. Add initial JSON schema.
6. Add tests for parser errors and serialization.

Exit criteria:

- `python -m pytest tests/test_contract_schema.py tests/test_cli.py` passes for
  skeleton behavior.
- `seamless-share --help`, `seamless-share why-not --help`, and
  `seamless-share transformation-diff --help` are stable.

### Phase B: Pure Diff Core

1. Implement reference resolution for JSON dict paths and inline `Reference`
   objects.
2. Implement checksum computation for dict references.
3. Implement plain/dunder classification.
4. Implement `transformation_diff_core`.
5. Implement JSON and basic text renderers.
6. Add C1-C5-style tests.

Exit criteria:

- Pure diff tests pass.
- No database endpoint is needed for dict-vs-dict tests.
- Dunder-only warning and text banner are present.

### Phase C: Local Endpoint Reads and Lookup State

1. Implement local SQLite read-only endpoint adapter.
2. Implement endpoint lookup states.
3. Implement endpoint union semantics.
4. Implement result materializability check for local bufferdir.
5. Add F14-F17 lookup fixtures.
6. Add lookup tests C6-C9 and C16.

Exit criteria:

- All four lookup states can be produced with local fixtures.
- Before/after DB and bufferdir hashes are identical after every command.

### Phase D: Transformation Dict Retrieval

1. Confirm where transformation dicts are stored in real Seamless artifacts.
2. If storage exists, implement read-only retrieval.
3. If storage does not exist, add the required producer/storage/read endpoint in
   the appropriate repo before claiming checksum refs are supported.
4. Add tests proving a checksum reference can resolve to the exact dict used to
   compute it.

Exit criteria:

- `transformation-diff <tf_checksum> <dict.json> --endpoint local.db` can
  retrieve the checksum-side dict without executing or writing.
- Remote support has either a tested read API or a documented capability error.

### Phase E: Candidate Selection and `why-not`

1. Implement deterministic candidate enumeration.
2. Implement `plain_delta_count` and `DEFAULT_MAX_PLAIN_DELTA = 3`.
3. Implement scoring and near-match refusal.
4. Implement explicit `--candidate`.
5. Wire `why-not` to call the diff primitive.
6. Add C10, C14, refined C15, P7, P8, and P11 tests.

Exit criteria:

- Repeated `why-not` JSON is byte-identical for fixed fixtures.
- Empty haystack and no-near-match cases refuse to invent candidates.
- A single shared plain-key value is not enough when the plain delta is greater
  than three.

### Phase F: Deep Diff

1. Implement read-only buffer fetching.
2. Implement text, JSON, and fallback deep entries.
3. Implement exit code `4` and `--deep-best-effort`.
4. Add C11, C12, and failure-shape tests.

Exit criteria:

- Missing buffers never crash the run.
- Missing buffers never trigger fingertip/recompute/write paths.

### Phase G: Full Scenario Suite and Docs

1. Build remaining fixtures F1-F13 and F18.
2. Port S1-S20.
3. Add docs:
   - `seamless_share/why_not/README.md`
   - `seamless_share/why_not/CHANGELOG.md`
   - schema docs
4. Add help-output snapshot tests.
5. Add manual smoke-test instructions for a real local `seamless.db`.

Exit criteria:

- All contract, scenario, property, schema, and CLI tests pass.
- Manual smoke test is documented and has been run at least once.

## Failure Shapes to Cover

Add dedicated tests for:

1. One endpoint unreachable in a multi-endpoint run:
   - exit `3`
   - error identifies endpoint
   - partial provenance preserved if available
2. Malformed local database schema:
   - exit `3`
   - distinct error tag from unreachable
3. `--deep` buffer requires fingertipping/no producer in scope:
   - checksum fallback
   - no re-execution
   - no writes
4. Definition path fails to load:
   - exit `2`
   - message names failing path
   - no partial candidate selection
5. Two references resolve to the same `tf_checksum`:
   - `transformation-diff` succeeds with empty entries
   - not treated as an error

## Definition of Done for V1

V1 is ready only when all of these are true:

1. Both CLI subcommands work.
2. Python API returns typed dataclasses matching the JSON schema.
3. JSON output validates against `schema.json`.
4. Text output includes the required state-specific hints.
5. `why-not` always reports lookup state.
6. Candidate diff is populated only when lookup state is `NOT_PRESENT` and a
   candidate exists or was supplied.
7. Automatic candidate selection refuses candidates whose plain delta exceeds
   the configured threshold; explicit `--candidate` still diffs the requested
   candidate.
8. `why-not` calls the transformation-diff primitive for all diffs.
9. Endpoint union semantics are tested.
10. Deterministic JSON is tested.
11. Read-only behavior is tested dynamically and statically.
12. All C, S, and P tests from `why-not-plan.md` are implemented or explicitly
    documented as blocked by a missing prerequisite such as transformation dict
    persistence.
13. The implementation has been reviewed line-by-line against Pattern 2 and the
    candidate-selection refinement above.

## Known Risks and Decisions for the Implementer

- The largest dependency risk is transformation dict retrieval. The visible
  database schema stores transformation result mappings but not the dict itself.
  Resolve this first during Phase D; do not paper over it.
- The candidate-selection threshold is deliberately conservative. If real-world
  fixtures show that `max_plain_delta = 2` is better than `3`, change the single
  constant and update tests; do not return to the one-shared-key rule.
- `definition_path` support can easily become accidental execution. Keep v1
  support narrow and read-only. JSON dict paths are enough to validate most of
  the primitive before definition loading exists.
- Dunder classification must stay aligned with the identity contract, not just
  current implementation names. Centralize the key classifier and test it.
- Remote endpoint support is useful, but local read-only correctness is the
  foundation. Ship local support first if needed.
- Do not add tests or UI that imply a single primary cause. The whole point of
  Pattern 2 is to surface all identity-level differences honestly.
