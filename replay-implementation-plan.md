# Handoff Implementation Plan: `seamless-share replay`

This is a self-contained implementation plan for the replay-mode verifier described in `seamless-share/replay-plan.md`. The work has two layers:

1. Seamless runtime replay mode: enforcement and instrumentation inside Seamless execution, cache lookup, buffer resolution, and remote dispatch paths.
2. `seamless-share replay`: a read-only harness, CLI, Python API, report schema, renderers, fixtures, and tests.

The harness must not invent cache-discipline policy. It configures replay mode, runs the user script, and serializes findings emitted by the runtime hooks.

## Repository Orientation

Relevant existing code:

- `seamless-share/pyproject.toml`: package metadata and `seamless-share = seamless_share.cli:main` console script.
- `seamless-share/seamless_share/cli.py`: existing argparse CLI with `transformation-diff` and `why-not` subcommands.
- `seamless-share/seamless_share/why_not/`: existing Pattern 2 implementation. Reuse `why_not()` for unexpected-miss diffs and reuse deterministic serialization style from `why_not.models.to_json`.
- `seamless-share/tests/`: existing pytest suite for CLI/API/schema/read-only behavior.
- `seamless-transformer/seamless_transformer/transformation_cache.py`: central non-Dask transformation cache lookup, local execution, worker dispatch, remote jobserver dispatch, and database writes.
- `seamless-dask/seamless_dask/transformation_mixin.py`: Dask submission path, driver detection, database-cache checks, dependency/input fingertipping, and local fallback.
- `seamless-dask/seamless_dask/client.py`: worker-side Dask tasks, fat/fat-finger checksum materialization, database writes, hashserver writes, and remote-client checks.
- `seamless-dask/seamless_dask/types.py`: `TransformationSubmission` and input metadata passed to Dask.
- `seamless-remote/seamless_remote/{database_remote.py,buffer_remote.py,jobserver_remote.py}`: remote database/hashserver/jobserver facades used by runtime paths.
- `seamless-share/usage-patterns.md`: contract source for Pattern 3. If this plan and Pattern 3 disagree, Pattern 3 wins.

## V1 Deliverables

Add these files:

- `seamless-share/seamless_share/replay/__init__.py`
- `seamless-share/seamless_share/replay/api.py`
- `seamless-share/seamless_share/replay/models.py`
- `seamless-share/seamless_share/replay/auth.py`
- `seamless-share/seamless_share/replay/config.py`
- `seamless-share/seamless_share/replay/report.py`
- `seamless-share/seamless_share/replay/render.py`
- `seamless-share/seamless_share/replay/schema.json`
- `seamless-share/seamless_share/replay/README.md`
- `seamless-share/seamless_share/replay/CHANGELOG.md`
- `seamless-share/seamless_share/replay/findings.md`
- `seamless-share/tests/replay/` with fixtures, scenario tests, schema tests, determinism tests, exit-code tests, and CLI/API equivalence tests.
- Runtime hook module(s), preferably in `seamless-transformer`, for replay state and events. Suggested names: `seamless_transformer/replay_runtime.py` and, if needed, lightweight adapters in `seamless_dask/replay_runtime.py`.
- Contract documentation under `seamless/docs/agent/contracts/`, e.g. `seamless/docs/agent/contracts/replay-mode.md`.

Update these files:

- `seamless-share/seamless_share/cli.py`: add the `replay` subcommand.
- `seamless-share/pyproject.toml`: include replay package data and any test extras needed for subprocess/schema tests.
- `seamless-transformer/seamless_transformer/transformation_cache.py`: consult replay mode during cache lookup/execution/remote dispatch/write attempts.
- `seamless-dask/seamless_dask/transformation_mixin.py`: consult replay mode for driver cache bypass, non-driver cache misses, Dask submissions, and fingertip authorization.
- `seamless-dask/seamless_dask/client.py`: consult replay mode in worker-side cache lookup, materialization, fingertipping, and write paths.

## Public Contract

### CLI

Implement:

```text
seamless-share replay <script> [script-args ...]
    --artifact <path-to-seamless.db>
    --bufferdir <path>
    [--authorization <path>]
    [--driver-cache {bypass,enabled}]
    [--report <path>]
    [--report-format {json,text}]
    [--config <path>]
    [--inherit-config]
    [--allow-remote]
    [--fail-on {none,any,unauthorized-only}]
    [--timeout <seconds>]
    [--verbose | -v] [--quiet | -q]
```

Argparse detail: add `replay_parser.add_argument("script")` and `replay_parser.add_argument("script_args", nargs=argparse.REMAINDER)`. Do not parse or transform script args.

Exit codes:

- `0`: tool ran and produced a report, unless `--fail-on` remaps findings.
- `2`: usage error, including missing `--artifact`, missing `--bufferdir`, unreadable script, or invalid enum flag.
- `3`: setup error, including unreadable artifact/bufferdir, malformed authorization, or config synthesis failure.
- `4`: script crashed or exited non-zero through a non-Seamless code path.
- `5`: findings matched `--fail-on`.
- `6`: timeout.
- `1`: unexpected harness failure.

Defaults:

- `--driver-cache bypass`
- `--fail-on none`
- stdout report uses `text` unless `--report-format json` is explicit.
- file report uses `json` unless `--report-format text` is explicit.
- no `--authorization` means only the supplied bufferdir is authorized; no fingertips; driver-cache remains controlled by `--driver-cache`.
- no `--inherit-config` means synthesize an isolated config that resolves only the supplied artifact and bufferdir.

### Python API

Expose from `seamless_share.replay`:

```python
from seamless_share.replay import AuthorizationSpec, ReplayConfig, ReplayReport, replay

report = replay(
    script="path/to/client.py",
    script_args=["--input", "foo"],
    artifact="path/to/seamless.db",
    bufferdir="path/to/bufferdir",
    authorization=AuthorizationSpec.from_file("auth.json"),
    driver_cache="bypass",
    config=ReplayConfig.synthesized(),
    timeout=None,
    allow_remote=False,
)
```

Return a typed `ReplayReport` dataclass that mirrors JSON exactly after conversion through `to_plain()` / `to_json()`.

## Report Model

Create dataclasses in `replay/models.py`:

- `ArtifactInfo`
- `AuthorizationSummary`
- `ReplayConfigInfo`
- `Outcome`
- `ReplayCounts`
- `PostRunAssertions`
- `Finding`
- `ReplayReport`

Use `SCHEMA_VERSION = "0.1.0"` for V1.

Required top-level JSON:

```json
{
  "tool": "replay",
  "version": "0.1.0",
  "artifact": {
    "seamless_db": "...",
    "seamless_db_checksum": "...",
    "bufferdir": "...",
    "bufferdir_checksum": "..."
  },
  "authorization_summary": {},
  "config": {
    "synthesized": true,
    "endpoints_resolved": {},
    "driver_cache": "bypass"
  },
  "outcome": {
    "phase": "completed",
    "wall_ms": 0,
    "script_exit_code": 0
  },
  "counts": {
    "drivers_executed": 0,
    "drivers_short_circuited": 0,
    "transformations_submitted": 0,
    "cache_hits": 0,
    "buffers_materialized_from_bufferdir": 0,
    "buffers_materialized_via_authorized_fingertip": 0,
    "findings_by_kind": {}
  },
  "findings": [],
  "post_run_assertions": {
    "seamless_db_unchanged": true,
    "bufferdir_unchanged": true
  }
}
```

Serialization requirements:

- Use `json.dumps(..., sort_keys=True, separators=(",", ":"))`.
- Omit `None` values from dataclass serialization, matching `why_not.models.to_plain`.
- Sort findings by `(script_position or "", kind, tf_checksum or "", id)`.
- Sort per-finding lists such as `chain` and `result_checksums` lexicographically unless their semantic order is a dependency trail. For dependency trails, keep trail order and make it deterministic at construction.
- Put timing and volatile annotations under `context`; they must not contribute to finding IDs.

Finding IDs:

- Stable hash over finding `kind` plus required fields only.
- Use canonical JSON bytes and SHA-256; store a short or full hex digest, but keep the choice stable and documented.
- Changes to `context` must not change the ID.

Finding kinds and required fields:

- `unexpected_miss`: `tf_checksum`, `script_position`, `driver_context`, `diff`
- `unauthorized_materialization`: `checksum`, `requested_by`, `script_position`, `available_authorizations`
- `unauthorized_fingertip`: `consumer_tf_checksum`, `missing_input_checksum`, `producer_tf_checksum`, `script_position`
- `authorized_materialization_unsatisfied_dependency`: `authorized_target`, `unsatisfied_dependency`, `chain`, `script_position`
- `remote_delegation_observed`: `backend`, `dispatched_work`, `script_position`
- `unexpected_heavy_compute`: `tf_checksum`, `was_driver`, `observed_cost_ms`, `correlated_miss`
- `irreproducible_only_hit`: `tf_checksum`, `row_count`, `result_checksums`, `script_position`
- `authorization_incoherent`: `authorization`, `reason`

## Authorization

Implement `AuthorizationSpec` in `replay/auth.py`.

V1 file format should be JSON for predictable tests:

```json
{
  "fingertips": ["<checksum>"],
  "driver_cache": "bypass"
}
```

Rules:

- The supplied `--bufferdir` is always an authorization source.
- Any checksum whose bytes are present in the bufferdir may be materialized from that bufferdir.
- Fingertip authorizations are explicit checksum strings.
- A fingertip authorization is incoherent if the artifact cannot identify a cached producer transformation for the authorized checksum.
- Driver cache is ultimately controlled by CLI/API `driver_cache`. If the auth file also contains `driver_cache`, reject contradictory values or emit `authorization_incoherent` with reason `conflicting_driver_cache` and continue only if the behavior is unambiguous.

Use a small `AuthorizationDecision` helper with:

- `allowed: bool`
- `source: "bufferdir" | "fingertip" | None`
- `reason: str`
- `considered: list[dict]`

Every materialization attempt must produce either a positive trace in the report or an authorization finding.

## Isolated Config

Implement `ReplayConfig` in `replay/config.py`.

Required modes:

- `ReplayConfig.synthesized()`: no remote endpoints; database reads resolve to `artifact`; buffer reads resolve to `bufferdir`; writes are disabled.
- `ReplayConfig.from_file(path)`: explicit isolated config file supplied with `--config`.
- `ReplayConfig.inherit()`: opt into normal environment/config. The report must mark this prominently with a top-level warning such as `config_inherited`.

The runtime needs enough environment to run a child script under replay mode. Prefer subprocess execution for V1 because it isolates global Seamless state and lets timeout handling be robust.

Suggested child environment:

- `SEAMLESS_REPLAY_MODE=1`
- `SEAMLESS_REPLAY_ARTIFACT=<abs path>`
- `SEAMLESS_REPLAY_BUFFERDIR=<abs path>`
- `SEAMLESS_REPLAY_AUTH=<serialized temp JSON path>`
- `SEAMLESS_REPLAY_DRIVER_CACHE=bypass|enabled`
- `SEAMLESS_REPLAY_ALLOW_REMOTE=0|1`
- `SEAMLESS_REPLAY_REPORT_EVENTS=<temp event JSONL path>`
- `SEAMLESS_REPLAY_CONFIG_MODE=synthesized|file|inherit`
- `SEAMLESS_REPLAY_CONFIG=<path>` when applicable

The runtime hook module should read these lazily and no-op when `SEAMLESS_REPLAY_MODE` is absent.

## Runtime Hook Design

Add a low-level runtime event API, preferably in `seamless-transformer/seamless_transformer/replay_runtime.py`:

```python
def active() -> bool: ...
def config() -> ReplayRuntimeConfig: ...
def emit(event: ReplayEvent) -> None: ...
def cache_lookup(tf_checksum, *, is_driver, transformation_dict, tf_dunder, script_position): ...
def materialization_request(checksum, *, requested_by, script_position, intent): ...
def fingertip_request(consumer_tf_checksum, missing_input_checksum, producer_tf_checksum, script_position): ...
def remote_dispatch(backend, dispatched_work, script_position): ...
def transformation_started(tf_checksum, *, is_driver): ...
def transformation_finished(tf_checksum, *, is_driver, observed_cost_ms, cache_hit): ...
```

Write events as deterministic JSON Lines to `SEAMLESS_REPLAY_REPORT_EVENTS`. Flush after each event so a crash or timeout still preserves partial findings.

Do not import `seamless_share` from runtime packages. Keep the runtime dependency direction clean:

- Runtime packages emit neutral events.
- `seamless-share` parses events and builds the canonical report/finding dataclasses.

### Runtime Enforcement Points

In `seamless-transformer/seamless_transformer/transformation_cache.py`:

1. At the start of `TransformationCache.run()`, compute `is_driver` from `tf_dunder` / `transformation_dict["__meta__"]["driver"]`.
2. Before in-memory or remote cache hit return, emit cache-hit event and increment later via harness aggregation.
3. On database miss for non-driver transformations, emit `unexpected_miss` event with the transformation dict path/payload needed for the harness to call `why_not`.
4. If `is_driver` and driver cache is `bypass`, skip cached `result_checksum` return and execute the driver locally.
5. If `is_driver` and driver cache is `enabled`, allow short-circuit and emit `driver_short_circuited`.
6. Before remote database/hashserver/jobserver operations, emit `remote_delegation_observed`. If the implementation elects to preemptively refuse remote dispatch when `allow_remote` is false, still emit the finding first.
7. Disable writes to the artifact-backed database and bufferdir. Any call path to `database_remote.set_transformation_result()`, `set_execution_record()`, hashserver writes, or buffer writes must be blocked or redirected to scratch storage under replay.

In `seamless-dask/seamless_dask/transformation_mixin.py`:

1. Apply the same driver cache policy in `_compute_with_dask()`, `_compute_with_dask_async()`, and `_ensure_dask_futures()`.
2. For non-driver submissions, a missing cache entry is `unexpected_miss`.
3. For `allow_input_fingertip`, emit/authorize `unauthorized_fingertip` before fat-finger futures are created.
4. Preserve `driver_context`: when a driver submits children, include a stack of parent driver `tf_checksum`s in emitted events. Use a `contextvars.ContextVar` in `replay_runtime.py` so nested execution works across async code.

In `seamless-dask/seamless_dask/client.py`:

1. Guard `_fat_checksum_task` and `_fat_finger_checksum_task` materializations.
2. Emit `unauthorized_materialization` when bytes are requested but unavailable from authorized sources.
3. Emit `authorized_materialization_unsatisfied_dependency` when fingertipping is authorized but a transitive input/dependency is not available or not authorized.
4. Block or redirect worker-side `database_remote.set_*` and `buffer_remote.write_buffer()` during replay.
5. Emit remote findings when Dask scheduling or remote clients are used as remote backends.

In `seamless-remote`, avoid broad changes if runtime hook checks in callers are sufficient. Add tests that prove remote facades are not written to when replay mode is active.

## Harness Flow

Implement `replay()` in `seamless-share/seamless_share/replay/api.py`:

1. Validate inputs:
   - script exists and is readable.
   - artifact exists and opens read-only as SQLite.
   - bufferdir exists and is a directory.
   - driver cache is `bypass` or `enabled`.
   - authorization file parses.
2. Compute pre-run digests:
   - `seamless_db_checksum`: SHA-256 of file bytes.
   - `bufferdir_checksum`: deterministic manifest hash: sorted relative file paths, file modes if useful, sizes, and SHA-256 of file bytes.
3. Build authorization summary and detect startup incoherence.
4. Build isolated or inherited config summary.
5. Create temp event JSONL path and serialized runtime config/auth file.
6. Run the script as a subprocess:
   - executable: `sys.executable`
   - argv: `[sys.executable, script, *script_args]`
   - cwd: default to current working directory unless API adds `cwd` later.
   - env: parent env plus replay variables for synthesized/file/inherited config.
   - timeout: subprocess timeout; on timeout, terminate, then kill if needed.
7. Parse event JSONL into counters and findings.
8. For each `unexpected_miss` event, call `seamless_share.why_not.why_not()` with:
   - the emitted transformation dict or temp JSON reference for the missed transformation.
   - endpoint set containing the artifact.
   - candidate omitted unless the event can supply a better candidate.
   - `deep=False` initially unless a later test requires deep diffs.
9. Query the artifact for irreproducible-only hits when the runtime emits a relevant lookup event or when a lookup misses the normal table.
10. Compute post-run digests and fill `post_run_assertions`.
11. Set `outcome.phase`:
   - `completed` if script exit code is `0`.
   - `script_error` if script exit code is non-zero.
   - `timeout` on timeout.
   - `setup_error` for setup failures.
12. Sort findings and counts deterministically.
13. Return `ReplayReport`.

Setup and timeout errors must still create a schema-shaped report object. If a report file cannot be written because setup failed before path handling, emit the JSON object on stderr from the CLI.

## CLI Rendering

In `replay/render.py`, implement:

- `render_replay_text(report, quiet=False, verbose=False) -> str`
- `to_json(report) -> str` or reuse a shared serializer from `replay.models`.

Text rendering is presentation only. Keep it stable enough for tests, but make JSON/Python objects canonical.

Minimum text contents:

- artifact paths
- outcome phase and script exit code
- counts summary
- findings grouped by kind, with IDs and key checksums
- read-only assertion result
- inherited-config warning if present

## JSON Schema

Create `seamless-share/seamless_share/replay/schema.json`.

Schema must validate:

- top-level report shape
- enum values for `outcome.phase`, `driver_cache`, and finding kinds
- required fields per finding kind using `oneOf`
- required count fields
- `post_run_assertions`

Update `seamless-share/pyproject.toml` package data:

```toml
seamless_share = [
  "why_not/schema.json",
  "why_not/README.md",
  "why_not/CHANGELOG.md",
  "replay/schema.json",
  "replay/README.md",
  "replay/CHANGELOG.md",
  "replay/findings.md",
]
```

## Test Implementation Plan

Build tests in layers. Mark tests that require full runtime hooks as expected failures only during the first scaffolding commit; remove xfails before V1.

### Unit Tests

Add `seamless-share/tests/replay/test_models.py`:

- deterministic JSON serialization
- finding ID stability
- finding sort order
- volatile `context` does not affect ID

Add `seamless-share/tests/replay/test_auth.py`:

- empty auth means bufferdir-only
- malformed auth raises setup error
- fingertip auth summary
- incoherent fingertip auth emits `authorization_incoherent`
- conflicting driver cache is rejected or reported consistently

Add `seamless-share/tests/replay/test_digests.py`:

- database file digest changes when bytes change
- bufferdir manifest digest is sorted and stable
- nested bufferdir layouts hash deterministically

Add `seamless-share/tests/replay/test_schema.py`:

- every generated report validates against `replay/schema.json`
- each finding kind validates with required fields
- missing required fields fail validation

### CLI/API Tests

Add `seamless-share/tests/replay/test_cli.py`:

- missing `--artifact` exits `2`
- missing `--bufferdir` exits `2`
- unreadable/missing script exits `2`
- malformed auth exits `3` and emits schema-shaped setup report
- invalid `--driver-cache` exits `2`
- `--report` defaults to JSON
- stdout defaults to text
- `--fail-on none|any|unauthorized-only` maps exit codes correctly

Add `seamless-share/tests/replay/test_api_cli_equivalence.py`:

- for each fixture scenario, compare CLI JSON report and Python API report modulo timing/context fields.

### Runtime Scenario Fixtures

Place fixture builders under `seamless-share/tests/replay/fixtures/`.

Minimum builders:

- `R1`: trivial happy path, cached transformation, result buffer present.
- `R2`: R1 with changed transformation dict, causing `unexpected_miss`.
- `R3`: driver with three cached sub-transformations.
- `R4`: R3 with one perturbed sub-transformation.
- `R5`: nested drivers depth 3.
- `R6`: missing buffer, no fingertip authorization.
- `R7`: R6 with fingertip authorization and cached producer.
- `R8`: R7 with producer input absent/unauthorized.
- `R10`: irreproducible-only row.
- `R11`: heavy driver needing bulk input present in bufferdir.
- `R12`: R11 with bulk input absent.
- `R13`: multiple independent issues.
- `R14`: cached hit with inflated wall-clock but no recomputation.
- `R15`: parallel sub-transformations for determinism.
- `R16`: authorization names checksum whose producer is absent.
- `R17`: same artifact under `driver_cache=bypass` and `enabled`.
- `R18`: driver body depends on env var so sub-transformations drift across runs.

The existing `tests/test_api_cli.py` has helper patterns for creating a minimal SQLite `transformation` table and adjacent bufferdir. Reuse that style for unit-level fixtures, then add full Seamless fixtures as runtime hooks mature.

### Contract Tests

Implement the C/S/P cases from `replay-plan.md` as pytest modules:

- `test_contract_clean_and_miss.py`: C1, C2
- `test_contract_drivers.py`: C3, C4, C12, C13, S1, S2, S3, S6
- `test_contract_authorization.py`: C5, C6, C7, C8, C9, S4
- `test_contract_remote_config.py`: C14, S7, S8, P8
- `test_contract_failures.py`: S9, S10, failure-shape cases 1-9
- `test_contract_properties.py`: C11, P1, P2, P3, P4, P5, P6, P7, P9, P10, P11, P12

Do not add tests that assert fixed wall-clock budgets, automatic remediation, silent remote success, artifact mutation, scheduler iteration order, or recursive cascade diagnosis beyond one `why-not` step.

## Static No-Write Check

Add a CI test, e.g. `seamless-share/tests/replay/test_no_write_static.py`, that scans replay modules for forbidden calls against artifact paths:

- `set_transformation_result`
- `set_execution_record`
- `undo_transformation_result`
- `write_buffer`
- direct SQLite write modes for the artifact
- direct `open(..., "w")` / `Path.write_*` to artifact or bufferdir paths

This test is not a substitute for runtime read-only assertions. It catches accidental harness write paths.

## Implementation Sequence

### Phase 1: Report/API/CLI Skeleton

1. Create replay package and dataclasses.
2. Add deterministic serialization and schema.
3. Add CLI parser and route in `seamless_share.cli`.
4. Implement input validation, digest calculation, setup-error reports, and report writing.
5. Add unit, schema, and CLI tests.

Acceptance for Phase 1: the replay command can run a no-op Python script, produce a valid empty report, handle setup errors, and pass model/schema/CLI tests.

### Phase 2: Runtime Event Plumbing

1. Add `seamless_transformer.replay_runtime` with environment parsing and JSONL event emission.
2. Add no-op-safe imports in `transformation_cache.py`, `transformation_mixin.py`, and `client.py`.
3. Emit cache lookup, cache hit, transformation started/finished, remote dispatch, materialization, fingertip, and write-block events.
4. Add tests with monkeypatched runtime paths to prove events are emitted and replay mode is inert when env vars are absent.

Acceptance for Phase 2: harness parses runtime events into counts/findings without enforcing all policy yet.

### Phase 3: Cache Discipline and Authorization Enforcement

1. Implement driver cache bypass/enabled behavior in non-Dask and Dask paths.
2. Implement materialization authorization checks.
3. Implement fingertip authorization checks.
4. Implement remote delegation findings and optional preemptive refusal.
5. Block or redirect all writes during replay.
6. Add read-only before/after assertions around full script runs.

Acceptance for Phase 3: C1-C8, C11-C14, S1-S4, S7-S8 pass.

### Phase 4: `why-not` Integration and Irreproducible Handling

1. On `unexpected_miss`, feed the emitted transformation dict into `why_not()`.
2. Validate `diff` against the existing why-not schema.
3. Detect irreproducible-only hits using existing local SQLite endpoint logic or a replay-local helper.
4. Add `irreproducible_only_hit` findings with row counts and sorted result checksums.

Acceptance for Phase 4: C2, C10, P10, I2-style diff equivalence pass.

### Phase 5: Determinism, Failure Modes, and Scale

1. Normalize sorting for findings, counts, and authorization summaries.
2. Preserve partial findings on script crash and timeout.
3. Complete exit-code matrix.
4. Add large-run fixture and determinism tests.
5. Add `--help` caveat test documenting no parallel replays of the same artifact.

Acceptance for Phase 5: P1-P12 and all failure-shape tests pass.

### Phase 6: Documentation and Contract Review

1. Write `replay/README.md` quickstart.
2. Write `replay/findings.md` as canonical finding-kind reference.
3. Write `replay/CHANGELOG.md` with schema version policy.
4. Add `seamless/docs/agent/contracts/replay-mode.md`.
5. Review against Pattern 3 in `usage-patterns.md` line by line and record any intentional refinements.

Acceptance for Phase 6: docs are packaged, help output is stable, and CI runs schema plus static no-write checks.

## Open Implementation Decisions to Resolve Locally

No user questions are required, but implementers must make and document these choices:

- Whether runtime remote dispatch is preemptively refused when `allow_remote=False` or merely observed and reported. The finding must be emitted either way.
- Exact JSON auth file shape beyond `fingertips` and optional `driver_cache`.
- Whether finding IDs use full SHA-256 or a shortened prefix.
- How to represent `script_position` when Python stack information is unavailable. Use `null` or a stable best-effort string, but keep sorting deterministic.
- Whether subprocess mode is the only V1 execution mode. Prefer subprocess for V1; in-process can be added later if needed.

## V1 Definition of Done

- `seamless-share replay` CLI and `seamless_share.replay.replay()` Python API are implemented.
- Reports validate against `seamless_share/replay/schema.json`.
- Runtime replay mode enforces read-only artifact behavior and emits all required findings.
- Driver cache bypass is default and `enabled` short-circuits only drivers.
- Every materialization is traced to bufferdir authorization or explicit fingertip authorization, or becomes a finding.
- `unexpected_miss.diff` is produced by the existing `why_not()` primitive.
- Remote dispatch is always visible as `remote_delegation_observed`.
- Script crashes and timeouts preserve partial reports.
- CLI/Python API outputs match modulo timing.
- Read-only assertions and static no-write checks are in CI.
- Documentation and contract files are packaged and tested.
