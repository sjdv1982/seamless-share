# Plan: replay-mode tool for `seamless-share`

This plan specifies **what to build and how to test it**, not how to implement it. The contract source is [Pattern 3 of usage-patterns.md](usage-patterns.md#pattern-3-replay-mode-verification-of-a-crystallized-artifact). Where this plan and that contract disagree, the contract wins; this file refines API surface and tests.

## Status and naming

The tool composes two layers, conceptually separable:

- **Seamless runtime mode** — a mode flag (and the policy changes it controls) inside Seamless's transformation submission and buffer resolution paths. This is *not* a `seamless-share` artifact; it lives in Seamless. `seamless-share` consumes it.
- **`seamless-share replay`** — the harness. Sets up the local materialization, configures endpoints to the crystallized artifact, launches the client script under replay mode, collects structured findings into a report.

Implementers may pick a different command name (`seamless-share verify`, `seamless-share check-artifact`, ...) but must preserve the layering: the harness does not invent gating logic; gating happens in Seamless.

## Surfaces in scope

Two surfaces, both must work and both must produce the same report:

1. **CLI**: `seamless-share replay …`, primary user-facing form.
2. **Python API**: `from seamless_share.replay import replay, ReplayConfig` (final import path is an implementer choice). Returns a typed `ReplayReport` object. Used by tests, by CI, and by the publisher's iteration loop.

The structured report is the contract. The CLI's `text` rendering is a presentation aid; the JSON / Python object is canonical.

## What is explicitly out of scope (v1)

- **Modifying the script under test.** The harness runs the script verbatim; it does not rewrite `.run()` to `.compute()`, does not inject probes, does not swap pins. If a script change is needed to converge, that is the publisher's job between runs.
- **Modifying the crystallized artifact.** Read-only on the `seamless.db` and the companion bufferdir under test. The local-materialization shape (whatever the publisher chose) is read-only during a run; the publisher may edit it *between* runs.
- **Automatically resolving findings.** The harness reports; it never silently adds a checksum to a whitelist, never fetches a missing buffer in the background, never re-runs to converge.
- **Remote-delegation success.** If a backend dispatch happens, it is a finding — even if the dispatched work completes correctly. The replay's self-sufficiency claim is undermined; the publisher decides what to do.
- **Cascade `why-not` recursion.** Each `Unexpected miss` finding carries one diff against one candidate (via [Pattern 2](usage-patterns.md#pattern-2-cache-miss-diagnosis-why-not)'s primitive). The publisher re-invokes `why-not` manually for cascade questions.
- **Concurrency.** Two replays of the same artifact in parallel are out of scope; assumed external coordination.

## CLI ergonomics

```
seamless-share replay <script> [script-args …]
    --artifact <path-to-seamless.db>
    --bufferdir <path>                     # the companion bufferdir
    [--authorization <path>]                # authorization spec (whitelist, etc.)
    [--driver-cache {bypass,enabled}]       # default: bypass
    [--report <path>]                       # default: stdout
    [--report-format {json,text}]           # default: json when --report is a file, text on stdout
    [--config <path>]                       # isolated seamless-config; default: synthesized
    [--inherit-config]                      # opt-in; for diagnostic purposes only
    [--allow-remote]                        # opt-in; replay treats remote dispatch as a finding even with this set, but does not refuse to dispatch
    [--fail-on {none,any,unauthorized-only}] # default: none (always exit 0 if the tool ran)
    [--timeout <seconds>]                   # wall-clock cap; default: none
    [--verbose | -v] [--quiet | -q]
```

### Conventions

- The script is invoked as a child process (or in-process; implementer choice) with `[script-args …]` forwarded verbatim. Replay must not parse the script's args.
- `--artifact` and `--bufferdir` are both required positional-equivalent flags (not positional, because forgetting one is a common failure shape worth catching at parse time with a clear message).
- `--authorization` points at whatever shape the implementer chose for the authorization spec (a JSON file with a checksum list, a directory of authorization fragments, ...). The spec format is an implementer choice; tests pin behavior, not file shape. Absent `--authorization`, the run uses the maximally-restrictive default: only the local bufferdir is authorized, no fingertips, driver caching bypassed.
- `--driver-cache bypass` (default) — drivers re-execute even when their `result_checksum` is cached. `--driver-cache enabled` — drivers may short-circuit on their cached `result_checksum`; their sub-transformations are not submitted. The choice must be visible in the report (count of drivers executed vs short-circuited).
- `--config` overrides Seamless's normal endpoint resolution. Default: the harness *synthesizes* a config that resolves the database endpoint to `--artifact`, the bufferdir endpoint to `--bufferdir`, and *no remote endpoints*. `--inherit-config` opts the user back into their normal config; this is for debugging only and the report flags it prominently.
- `--allow-remote` does **not** disable the `Remote delegation observed` finding. It only suppresses any preemptive refusal the implementer chose to add. Whether the implementer adds such a refusal is their choice; the test asserts the *finding* is emitted regardless.
- `--fail-on` controls exit-code mapping only. `none` (default) — exit `0` whenever the tool ran to completion, regardless of findings. `any` — exit `5` if any finding is present. `unauthorized-only` — exit `5` if any finding of `Unauthorized materialization`, `Unauthorized fingertip`, or `Authorized materialization with unsatisfied dependency` is present. CI integrations choose; the default does not penalize having findings, because Pattern 3 explicitly frames findings as the iteration signal.
- Exit codes:
  - `0` — tool ran to completion and produced a report. **Findings present is still `0` unless `--fail-on` says otherwise.**
  - `2` — usage error (bad argv, missing required flag, script path not found).
  - `3` — setup error (artifact unreadable, authorization spec malformed, config synthesis failed).
  - `4` — script crashed or returned non-zero through a non-Seamless code path. Report is still produced and lists the script error; the publisher decides whether that is a finding or a script bug.
  - `5` — findings present and `--fail-on` matched. Report produced normally.
  - `6` — timeout.
  - `1` — reserved for unexpected internal failure (crash in the harness itself).

### Report file conventions

- Default report destination: stdout, with `--report-format text`.
- When `--report <path>` is given, default report format becomes `json` and a deterministic byte-ordered JSON is written (see [Determinism](#determinism-and-machine-readability) below).
- A run that produces no report file because of a setup or timeout error must still emit a structured error object on stderr in the same schema as the report (with `phase: "setup_error" | "timeout"` and an empty findings list). CI can rely on the schema.

## Authorization model (API-level)

The authorization spec is the publisher's declaration of what materialization is allowed during replay. The plan does not pin the file format — that is an implementer choice — but the *model* below is binding.

An authorization spec consists of:

1. **Bufferdir authorizations** — the companion bufferdir at `--bufferdir` is authorized by being passed. Any checksum whose buffer is present in it may be materialized.
2. **Fingertip authorizations** — a (possibly empty) set of checksums that are permitted to be re-derived by re-executing their producer. Each fingertip authorization implies the producer's `tf_checksum` must be cached in the artifact; if it is not, the authorization is *incoherent* (see findings).
3. **Driver-cache knob** — the `--driver-cache` flag (or its equivalent in the spec). Affects which drivers may serve from their cached `result_checksum`.

Any buffer materialization during replay must trace to **one** of these authorizations. Anything else is a finding.

The implementer is free to support additional authorization mechanisms in future versions — the contract is that each must be **explicit, auditable, and verifiable**, not the specific list above.

## The findings model

A replay run accumulates findings into the report; the run does not abort on the first one (unless `--timeout` fires). The finding kinds below are normative — same names, same data fields — across CLI and Python API. Adding finding kinds requires a schema version bump.

| Finding kind (stable tag)            | Triggered by | Required fields |
|---|---|---|
| `unexpected_miss`                    | non-driver `tf_checksum` lookup misses | `tf_checksum`, `script_position` (best-effort), `driver_context` (parent driver `tf_checksum` chain, possibly empty), `diff` (a [Pattern 2](usage-patterns.md#pattern-2-cache-miss-diagnosis-why-not) `why-not`-shaped object) |
| `unauthorized_materialization`       | buffer fetch could not be served from any authorized source | `checksum`, `requested_by` (the `tf_checksum` that asked, if known), `script_position`, `available_authorizations` (a brief summary of which authorizations were considered and why each rejected it) |
| `unauthorized_fingertip`             | a consumer would have triggered input fingertipping on a checksum not authorized for fingertipping | `consumer_tf_checksum`, `missing_input_checksum`, `producer_tf_checksum`, `script_position` |
| `authorized_materialization_unsatisfied_dependency` | an authorized materialization cannot complete because a transitive dependency is itself not authorized or unavailable | `authorized_target` (checksum), `unsatisfied_dependency` (checksum), `chain` (the transitive trail), `script_position` |
| `remote_delegation_observed`         | execution dispatched to a remote backend (jobserver, daskserver, remote hashserver) during replay | `backend` (named tag), `dispatched_work` (what was sent), `script_position` |
| `unexpected_heavy_compute`           | a non-driver transformation ran (or a driver ran for an unexpectedly long time) | `tf_checksum`, `was_driver` (bool), `observed_cost_ms`, `correlated_miss` (the `unexpected_miss` finding id if applicable, else null) |
| `irreproducible_only_hit`            | the only cache entry for the `tf_checksum` is in `IrreproducibleTransformation` | `tf_checksum`, `row_count`, `result_checksums`, `script_position` |
| `authorization_incoherent`           | startup or run-time discovery: an authorization names a checksum whose producer is not in the cache, or a self-contradictory knob configuration | `authorization` (what the authorization said), `reason` (short tag) |

A finding is `{ "kind": "<tag>", "id": "<stable hash of fields>", ...required fields..., "context": { ... extras } }`. The `id` is computed from the kind plus the required fields so that two replays produce stable, comparable finding ids; `context` is extras (timing, internal annotations) that do *not* contribute to `id`.

### Finding-to-finding cross-references

`unexpected_heavy_compute` may reference an `unexpected_miss` by id (a non-driver that missed will usually have both). This is the only cross-reference required in v1. Other kinds are independent.

## Report schema

```jsonc
{
  "tool": "replay",
  "version": "<semver>",                 // schema version
  "artifact": {
    "seamless_db": "<path>",
    "seamless_db_checksum": "<hex>",     // pre-run checksum
    "bufferdir": "<path>",
    "bufferdir_checksum": "<hex>"        // a deterministic digest of the bufferdir's contents; e.g., sorted manifest hash
  },
  "authorization_summary": { ... },      // shape mirrors the chosen authorization model
  "config": {
    "synthesized": <bool>,
    "endpoints_resolved": { ... },       // what config the harness actually used; informational
    "driver_cache": "bypass" | "enabled"
  },
  "outcome": {
    "phase": "setup_error" | "running" | "completed" | "script_error" | "timeout",
    "wall_ms": <int>,
    "script_exit_code": <int>|null
  },
  "counts": {
    "drivers_executed": <int>,
    "drivers_short_circuited": <int>,    // 0 when --driver-cache bypass
    "transformations_submitted": <int>,
    "cache_hits": <int>,
    "buffers_materialized_from_bufferdir": <int>,
    "buffers_materialized_via_authorized_fingertip": <int>,
    "findings_by_kind": { "<tag>": <int>, ... }
  },
  "findings": [ ... ],                   // sorted deterministically; see below
  "post_run_assertions": {
    "seamless_db_unchanged": <bool>,
    "bufferdir_unchanged": <bool>
  }
}
```

A schema validator (`seamless-share/replay/schema.json`) is part of the deliverable.

### Determinism and machine-readability

The report's `findings` array is sorted by a tuple of stable keys: `(script_position or "", kind, tf_checksum or "", finding.id)`. Per-finding internal lists (e.g., `chain`, `result_checksums`) are sorted by their own stable order (lexicographic on hex).

This guarantees: two replays of the same artifact + script + authorization + config produce a byte-identical `findings` array. CI diffs can distinguish "real change" from "concurrency noise". Tests assert byte-equality across re-runs (modulo timing fields, which live under `context` and `wall_ms` and are excluded from byte-equality checks).

## Python API shape

```python
from seamless_share.replay import replay, ReplayConfig, AuthorizationSpec

report = replay(
    script="path/to/client.py",
    script_args=["--input", "foo"],
    artifact="path/to/crystallized/seamless.db",
    bufferdir="path/to/crystallized/buffers/",
    authorization=AuthorizationSpec.from_file("auth.json"),
    driver_cache="bypass",
    config=ReplayConfig.synthesized(),    # or .from_file(path) or .inherit()
    timeout=None,
    allow_remote=False,
)
# report is a typed dataclass mirroring the JSON schema
assert all(f.id for f in report.findings)
```

Final naming is an implementer choice; tests pin behavior, not symbol names.

## Behavioral contracts (must hold)

These are the API-level acceptance criteria, mirroring Pattern 3's "Must hold".

1. **Read-only on the artifact.** Before-and-after checksums of the `seamless.db` and the bufferdir match exactly. The tool has no code path that writes to either. This is a structural property, enforced by a static check and a runtime assertion.
2. **Findings accumulate, not abort.** A run does not abort on the first finding. A clean run produces zero findings; a run with multiple independent problems produces multiple findings.
3. **Driver default is bypass.** Without `--driver-cache enabled`, drivers identified by the Seamless definition-time marker re-execute even when their `result_checksum` is cached. Non-drivers do not.
4. **Every materialization traces to an authorization.** Every byte materialized during the run is recorded as having traced to a specific authorization (bufferdir presence, authorized fingertip, ...). Anything else produces an `unauthorized_materialization` finding. The trace is auditable in the report (per-checksum, not just per-count).
5. **Each `unexpected_miss` carries a `why-not`-shaped diff.** The diff field of an `unexpected_miss` finding is structurally identical to a [Pattern 2](usage-patterns.md#pattern-2-cache-miss-diagnosis-why-not) `why-not` result (lookup_state, candidate, diff). The harness invokes the `why-not` primitive; it does not invent its own diff logic.
6. **Isolated config by default.** Without `--inherit-config`, the run uses a synthesized config that does *not* resolve to the publisher's normal remote endpoints. The report's `config.endpoints_resolved` reflects what was actually used; tests can assert no remote endpoints appear in this field.
7. **Remote delegation is always a finding.** Whether `--allow-remote` is set or not, any backend dispatch produces a `remote_delegation_observed` finding. `--allow-remote` only governs whether the harness preemptively refuses, not whether it reports.
8. **Determinism.** Two runs with the same script, artifact, authorization, config, and `--driver-cache` flag produce byte-identical reports modulo timing fields.
9. **`Irreproducible-only hit` is surfaced, not refused.** A `tf_checksum` reachable only via `IrreproducibleTransformation` produces a finding when the script touches it; the run continues so the publisher can see all such cases in one pass.
10. **Heavy-driver mismatches surface as ordinary findings.** A driver that locally re-executes and needs bulk-input materialization that is not authorized produces `unauthorized_materialization` / `unauthorized_fingertip` / `authorized_materialization_unsatisfied_dependency` findings — with the driver's `tf_checksum` in `driver_context`. No new finding kind for "heavy driver"; existing kinds compose.

## Must reject (API-level)

- `replay` invoked without `--artifact` or without `--bufferdir` — exit `2`.
- `replay` invoked with a `--script` path that does not exist or is not readable — exit `2`.
- `replay` invoked with an authorization spec that fails to parse — exit `3`.
- `--driver-cache` with a value other than `bypass` or `enabled` — exit `2`.
- Any flag combination that would silently write to the artifact — must not be expressible at the CLI or in the Python API.

## Test plan

Three tiers, mirroring the why-not plan's structure:

1. **Schema/contract tests** — assert the report shape and the cross-cutting invariants.
2. **Scenario tests** — concrete replay situations from Pattern 3's failure modes.
3. **Property/regression tests** — read-only, determinism, exit-code matrix, isolation.

### Test fixtures

Each fixture is a triple of `(crystallized seamless.db, companion bufferdir, client script)`, optionally with an `authorization spec`. Fixtures are generated by `tests/fixtures/replay/*.py` scripts that materialize a fresh triple into a temp directory.

Required fixtures (minimum set; numbering used in scenarios below):

- `R1` — trivial happy path: a script that computes one cached transformation; bufferdir contains exactly what is needed.
- `R2` — `R1` with the script edited so the transformation's `tf_checksum` no longer matches what is cached (parameter added). Used for `unexpected_miss` testing.
- `R3` — a script using a driver that returns a deep checksum and produces three sub-transformations. All sub-transformations are cached; bufferdir contains their outputs.
- `R4` — `R3` with one sub-transformation's `tf_checksum` perturbed to simulate cache drift.
- `R5` — nested drivers, depth 3, all leaves cached.
- `R6` — a script that requires a buffer not in the bufferdir and not authorized for fingertipping.
- `R7` — `R6` with a fingertip authorization for that checksum; the producer is cached.
- `R8` — `R7` but the producer's own input is itself absent and unauthorized — triggers `authorized_materialization_unsatisfied_dependency`.
- `R9` — a script that materializes via remote hashserver in a synthesized config; replay should report `remote_delegation_observed` but only if the config inadvertently includes a remote — i.e., this fixture is a regression test for the isolated-config default. (See P-tests.)
- `R10` — an `IrreproducibleTransformation` row reachable from the script.
- `R11` — a heavy driver that needs to materialize a bulk input present in the bufferdir; happy path under `--driver-cache bypass`.
- `R12` — `R11` but bulk input is *not* in the bufferdir; expected to produce `unauthorized_materialization` with driver context.
- `R13` — a script that triggers multiple independent issues (e.g., one unauthorized fingertip + one unexpected miss in unrelated branches). Used to assert findings accumulate.
- `R14` — empty findings, but with intentionally inflated wall-clock cost (a sleep or busy-loop in a transformation that *did* cache). Used to test that `unexpected_heavy_compute` is calibrated to *compute that ran*, not to wall-clock alone — i.e., a cached hit that took long for unrelated reasons must not produce this finding.
- `R15` — a script that races (parallel sub-transformations); used for determinism testing.
- `R16` — an authorization spec that names a checksum whose producer is not in the artifact. Used for `authorization_incoherent`.
- `R17` — the same client script and artifact, but run twice with `--driver-cache bypass` vs `--driver-cache enabled`. Used for the driver-knob assertions.

### Cross-cutting schema/contract tests

Numbered against Pattern 3's [Must test](usage-patterns.md#must-test-2) list 39–54; this plan refines.

- **C1 (≈ Pattern 3 test 39).** Clean replay: `R1` produces zero findings; report's `outcome.phase == "completed"`; `script_exit_code == 0`; `counts.cache_hits >= 1`; post-run assertions both true.
- **C2 (≈ 40).** Unexpected miss with diff: `R2` produces exactly one `unexpected_miss` finding. The finding's `diff` is a `why-not`-shaped object, `diff.lookup_state.state == "NOT_PRESENT"`, `diff.candidate != null`, `diff.identity_relevant == true`. Asserts the diff comes from Pattern 2's primitive (the schemas match exactly).
- **C3 (≈ 41).** Driver may miss; sub-transformations may not: under default `--driver-cache bypass`, `R3` produces zero findings (the driver executes; all subs hit). `R4` produces one `unexpected_miss` attributed to the sub-transformation with `driver_context` listing `R3`'s driver `tf_checksum`.
- **C4 (≈ 42).** Nested drivers: `R5` produces zero findings; each driver level executes; `counts.drivers_executed == 3`; `counts.cache_hits == <number of leaves>`.
- **C5 (≈ 43).** Unauthorized fingertip surfaced: `R6` (no authorization) produces one `unauthorized_fingertip` or one `unauthorized_materialization` finding (the kind depends on whether the consumer was going to fingertip or just materialize — the test accepts either, but asserts *exactly one* of these is present and that no silent re-execution happened — `counts.transformations_submitted` is unchanged from the no-fingertip baseline).
- **C6 (≈ 44).** Authorized fingertip succeeds: `R7` produces zero findings of the authorization-failure kinds. `counts.buffers_materialized_via_authorized_fingertip >= 1`.
- **C7 (≈ 45).** Authorized materialization with unsatisfied dependency: `R8` produces one `authorized_materialization_unsatisfied_dependency` finding; its `chain` lists the transitive trail; the run does not recursively cascade into more re-executions (`counts.transformations_submitted` is bounded — implementation-defined cap, but tested as ≤ N for fixture-known N).
- **C8 (≈ 46).** Unauthorized materialization surfaced: a fixture where a script needs a byte for which no authorization applies produces an `unauthorized_materialization` finding naming the checksum and `script_position`; no remote fetch is attempted.
- **C9 (≈ 47).** Iterative convergence: start from `R6` with empty authorization; resolve the finding by adding the buffer to the bufferdir; re-run; new run has zero findings. Test asserts both runs ran cleanly and the report shows the change.
- **C10 (≈ 48).** Irreproducible-only hit: `R10` produces one `irreproducible_only_hit` finding with `row_count >= 1` and a non-empty `result_checksums`.
- **C11 (≈ 49).** Read-only assertion: before/after checksums of `seamless.db` and bufferdir are byte-identical for every test in this suite. `post_run_assertions.seamless_db_unchanged == true` and `bufferdir_unchanged == true`.
- **C12 (≈ 50).** Driver cache bypass by default: `R3` under default `--driver-cache bypass`: the driver re-executes even though its `result_checksum` is cached. `counts.drivers_executed >= 1`, `counts.drivers_short_circuited == 0`.
- **C13 (≈ 51).** Driver caching knob: `R3` under `--driver-cache enabled`: the driver short-circuits on its cached entry; sub-transformations are *not* submitted. `counts.drivers_short_circuited >= 1`, `counts.transformations_submitted < default-mode count`. Assert the knob's effect is visible in the report.
- **C14 (≈ 52).** Remote delegation observed: a fixture where a remote backend dispatch is forced (by injecting a remote endpoint into the synthesized config, only for this test) produces a `remote_delegation_observed` finding. Test does not assert whether work completed; it asserts visibility.
- **C15 (≈ 53).** Execution-time visibility: report includes wall-clock and per-transformation timing under `context`. Test asserts presence of timing data, not a fixed budget.
- **C16 (≈ 54).** Multiple findings in one run: `R13` produces ≥ 2 findings of different kinds in one run; tool does not abort at the first.

### Scenario tests

These bind specific Pattern 3 nuances to assertions on the report.

- **S1 (heavy driver, happy path).** `R11` produces zero findings under `--driver-cache bypass`. `counts.buffers_materialized_from_bufferdir` includes the bulk input.
- **S2 (heavy driver, missing bulk input).** `R12` produces an `unauthorized_materialization` finding whose `requested_by` is the driver's `tf_checksum` (or its child as appropriate) and whose `driver_context` lists the driver. No new finding kind required.
- **S3 (heavy driver with driver caching).** `R11` under `--driver-cache enabled` short-circuits the driver. `counts.drivers_short_circuited >= 1`. No materialization of the bulk input occurs (`counts.buffers_materialized_from_bufferdir` does *not* include it). This is the knob's intended trade-off.
- **S4 (authorization incoherent at startup).** `R16` produces an `authorization_incoherent` finding. The publisher's chosen implementation may also refuse to start; if it does, the report still contains the finding and exits via the `phase: "setup_error"` path with exit `3`. Test accepts either shape (runtime or startup) but asserts the finding is present in the report.
- **S5 (heavy compute without miss).** `R14` (cached hit that wall-clock-took-long) produces zero `unexpected_heavy_compute` findings. Wall-clock alone must not trigger the finding; *actual recomputation* must.
- **S6 (driver determinism mask).** A fixture (call it `R18`) where a driver's body depends on an environment variable, so its sub-transformations differ across runs. Run twice with the env var changed between runs. Second run produces `unexpected_miss` findings on the sub-transformations whose `tf_checksum`s drifted. Test asserts the findings reference the driver's `tf_checksum` in `driver_context` — providing the context Pattern 3 names as the diagnostic surface for driver non-determinism. The harness is not expected to diagnose the cause; the test asserts the *context is reachable* in the report.
- **S7 (config isolation).** With default `--config` (synthesized), assert `config.endpoints_resolved` contains only `--artifact` and `--bufferdir` — no remote endpoints. Run the same fixture with `--inherit-config` under a manipulated user config that includes a remote; assert the report's `config.endpoints_resolved` now lists the remote and the report flags this prominently (e.g., a `config_inherited` warning at the top level).
- **S8 (allow-remote does not suppress finding).** Force a remote dispatch (as in C14) with `--allow-remote`; assert the `remote_delegation_observed` finding is still present.
- **S9 (script crash).** A script that raises a Python exception during a non-Seamless code path produces `outcome.phase == "script_error"`, `outcome.script_exit_code != 0`, exit `4`. Findings collected up to the crash are still in the report.
- **S10 (timeout).** A script wrapped to hang past `--timeout`; report is produced with `outcome.phase == "timeout"`, exit `6`. Read-only assertions still hold.

### Property and regression tests

- **P1 (read-only invariance).** Hash `seamless.db` and every byte-file in the bufferdir before each test; assert unchanged after. Run across a randomized sample of 20+ scenarios.
- **P2 (determinism).** Run each scenario twice; assert report `findings` arrays are byte-identical and `counts` are equal. Timing fields are excluded from the comparison.
- **P3 (no-write-path static check).** Grep / AST check: assert no symbol importable from `seamless_share.replay` calls a known write API on `seamless.db` / hashserver pointed at the artifact paths. Wired into CI.
- **P4 (exit-code matrix).** Parametrize across `{0, 2, 3, 4, 5, 6}` and `--fail-on` variants; for each, construct the minimal invocation that should produce it; assert the exit code.
- **P5 (CLI ↔ Python API equivalence).** For each scenario fixture, run the CLI with `--report-format json` and the Python API; assert structured outputs are equal modulo timing.
- **P6 (schema validity).** Validate every report against `seamless-share/replay/schema.json`. Schema is public contract; bumps require version bump.
- **P7 (`--driver-cache` knob is purely about drivers).** Diff the two reports from `R17` (`bypass` vs `enabled`). Differences must be confined to: `counts.drivers_executed`, `counts.drivers_short_circuited`, `counts.transformations_submitted`, and the set of findings (which may shrink under `enabled` because uncached sub-transformations stop being visible). Non-driver-related fields are unchanged.
- **P8 (`--allow-remote` is purely about preemptive refusal).** Compare runs with and without `--allow-remote` on a fixture that *doesn't* trigger remote dispatch. Reports are byte-identical modulo timing.
- **P9 (finding `id` stability).** Compute findings' `id`s across two runs; identical findings produce identical ids. A change in `required fields` produces a different `id`. Tests pin a few example ids to a regression baseline.
- **P10 (`why-not` schema compatibility).** The `diff` field inside `unexpected_miss` findings validates against the `why-not` schema. If the `why-not` schema is bumped in a breaking way, this test fails — forcing a coordinated bump.
- **P11 (large run resilience).** A synthesized fixture with 500+ transformations and 100+ findings produces a complete, sorted report without truncation or memory blowup. (Catches accidental pagination, log spam, or O(n²) sorting.)
- **P12 (concurrency caveat).** Pattern 3 declares concurrency out of scope. A test asserts the tool *documents* this (the `--help` output contains the caveat). No correctness assertion under concurrent invocation.

### Tests that must NOT exist

The plan is also a list of things implementers should resist adding:

- Tests that assert a fixed wall-clock budget. Pattern 3 explicitly says execution time is *observational, not a gating threshold*. A test that fails when replay takes > N seconds will be brittle and will be deleted.
- Tests that assert the harness fixes findings automatically (silently adds to a whitelist, fetches a missing buffer, etc.).
- Tests that assert remote dispatch is silently allowed under `--allow-remote`. It is *always* a finding.
- Tests that assert any modification to the artifact under test.
- Tests that depend on the iteration order of parallel sub-transformations (the report's determinism contract makes order stable, but assertions should target the stable shape, not the underlying scheduling).
- Tests that assert cascade diagnosis of `unexpected_miss` findings beyond one `why-not` step.

## Failure shapes the test suite must cover

Beyond the matrix above, these failure shapes must each have a dedicated test:

1. **Artifact path missing or unreadable** — exit `3`; report on stderr in the standard schema with `phase: "setup_error"` and a tag distinguishing missing vs unreadable.
2. **Bufferdir path missing** — exit `3` with a distinct tag.
3. **Authorization spec malformed** — exit `3`; report identifies the malformation.
4. **Authorization spec coherent at file level but semantically incoherent** (`R16`) — `authorization_incoherent` finding (see S4).
5. **Script crashes mid-replay** — `R`-fixture variant: `phase: "script_error"`, exit `4`, partial findings preserved.
6. **Timeout fires** — exit `6`, `phase: "timeout"`, partial findings preserved.
7. **Driver re-execution succeeds but its inputs require unauthorized materialization** (`R12`) — `unauthorized_materialization` finding with driver context (already covered by S2; called out here as a deliberate failure shape).
8. **The artifact contains a `Transformation` row whose `result_checksum` cannot be materialized and whose producer is in the artifact but its inputs are unauthorized for fingertipping** — should produce `authorized_materialization_unsatisfied_dependency` (`R8` / C7).
9. **A driver runs to a `result_checksum` that is *not* the one previously cached** — surfaces as the driver's output disagreement; the test fixture establishes whether this is treated as `unexpected_miss` on the driver or as something else (implementer decides; the test pins their choice and asserts it is visible in the report).

## Composition with prior patterns

- **Reads Pattern 1's output.** The harness consumes a crystallized artifact as-is. No assumption that Pattern 1's tooling exists yet — the fixture generators are hand-rolled. When Pattern 1 lands, an integration test runs Pattern 1 to produce the artifact, then replay against it (`I1`).
- **Uses Pattern 2's primitive.** Each `unexpected_miss` finding's `diff` is produced by calling the `why-not` Python API directly. The harness must not duplicate diff logic; if `why-not` is not yet implemented, the finding's `diff` field is `null` and the test suite is gated until `why-not` exists. Acceptance criterion (5) reflects this gate.

### Integration tests (when Pattern 1 and Pattern 2 are also available)

- **I1.** Run Pattern 1 (`crystallize`) on a synthetic project; run replay on its output; expect zero findings.
- **I2.** Run Pattern 1, then deliberately edit the client script to introduce one cache miss; run replay; expect exactly one `unexpected_miss` whose `diff` is a usable `why-not` output that, when fed back through `why-not` independently, produces the same diff.

## Documentation deliverables alongside the code

- `seamless-share/replay/README.md` — user-facing quickstart, links back to [Pattern 3](usage-patterns.md#pattern-3-replay-mode-verification-of-a-crystallized-artifact).
- `seamless-share/replay/schema.json` — JSON Schema for the report.
- `seamless-share/replay/CHANGELOG.md` — semver, schema version bumps, finding-kind additions.
- `seamless-share/replay/findings.md` — the canonical list of finding kinds and their required fields. Drives the schema and is referenced by tests.
- Man-page-style `--help` text; `--help` output is itself a test fixture (regenerable, byte-stable).

## Acceptance criteria for v1

The tool is ready for v1 cut when:

1. All tests above pass, with Pattern 2's `why-not` available so `unexpected_miss` findings have populated diff bodies.
2. The schema validator (P6) is wired into CI.
3. The static no-write-path check (P3) is wired into CI.
4. A real-world smoke test exists: a small Seamless project's crystallized artifact runs through replay with both `--driver-cache bypass` and `--driver-cache enabled`, and the publisher confirms findings (or absence of findings) match expectations.
5. The schema and CLI are reviewed against [Pattern 3](usage-patterns.md#pattern-3-replay-mode-verification-of-a-crystallized-artifact) line-by-line; any deviation is either fixed or recorded as an intentional refinement in this file.
6. The replay-mode runtime hooks in Seamless itself are merged and documented in the relevant contract under [seamless/docs/agent/contracts/](seamless/docs/agent/contracts/). Without the Seamless-side mode, the harness cannot enforce the cache discipline that Pattern 3 requires.
