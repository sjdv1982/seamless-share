# Seamless-share usage patterns

Defensive specification for implementers. This file frames *why* a `seamless-share` operation succeeds or fails, then enumerates concrete usage patterns. Each pattern carries explicit invariants ("must hold"), rejection conditions ("must reject"), and test obligations ("must test"), so implementers and test authors share one source of truth.

**Status.** This document is the design contract that drives implementation, not API documentation for tools that already work. At the time of writing, the repo is a stub (see [README.md](seamless-share/README.md)) and none of the tools described below exist; as the doc is used to produce implementations, some of the named tools (`fork`, `merge-back`, `transformation-diff`, `why-not`, ...) **will** come into existence and others may be renamed. Implementers may rename, but the contracts attached to each name are binding for whatever tool ends up filling that slot. When a tool exists, this file remains its source of truth for *required* behavior — implementations diverging from the contracts below are defects, not features. Verify the current implementation status by inspecting the repo directly; do not assume from this file that a tool exists or does not.

## Audience and scope

This document is for:

- Implementer agents writing or modifying `seamless-share` code.
- Middleware/planner agents writing concrete implementation + testing plans from these patterns.

It is not a Seamless tutorial. Read the agentic docs linked at the end before this file.

## What `seamless-share` is for

`seamless-share` exchanges Seamless artifacts (transformation cache entries, hashserver buffers, execution records, and the identity structures linking them) between Seamless installations so that:

1. A **recipient gets the same cache hits the publisher does** on the shared transformations and their cascade.
2. The recipient can reason about the **provenance cascade** behind those hits — the chain of transformations whose composition produced each result.

This is the load-bearing reframe: sharing is not primarily about trust. It is about transporting enough of the content-identity graph that future computations on the recipient side resolve to cached results — and, when a recipient needs to re-derive (e.g., to fingertip a scratched intermediate, or to fork a sub-step), they have the graph slice required to do so.

Trust and audit questions exist but are a separate concern (possibly a separate repo). Here we assume publisher and recipient are cooperating on caching and provenance.

## Core mental model

### A share is a closed subgraph, not a payload

The unit of a share is a **closure** over the content-identity graph: a chosen set of "root" transformations, plus the transitive set of identities required to satisfy the chosen policy (rehydrate-only, allow-fingertip, allow-re-execute, audit). The publisher computes this closure; the recipient consumes it.

The implementer must always answer two questions when defining a share:

1. **What is the closure?** Which transformations and which buffers are inside the slice?
2. **What is the policy at the frontier?** For each identity *not* included, what is supposed to happen when the recipient asks for it?

A share that does not make both answerable is incomplete by construction.

### Two frontiers, two failure modes

| Frontier | Owned by | What can go wrong if it leaks out of the slice |
|---|---|---|
| **Input frontier**: buffers a transformation reads | transformation identity | Recipient can verify the result checksum but cannot re-execute or fingertip a scratched output. |
| **Inner frontier**: sub-`tf_checksum`s inside a driver/nested transformation | parent driver identity | Recipient gets one cache hit at the top; modifying any inner step forces a full recompute, defeating the per-step caching that nesting was for. |

Both frontiers must be enumerable from the share artifact. Tests must exercise patterns that cross each.

### Plain vs dunder, restated for sharing

Per [identity-and-caching.md](seamless/docs/agent/contracts/identity-and-caching.md):

- **Plain keys** (`code`, `arg1`, `objects`, modules, ...) are part of `tf_checksum`. For *cache reuse alone*, the recipient needs the identities the plain keys reference; bytes only matter where materialization is required.
- **Dunder keys** (`__env__`, `__compilation__`, `__schema__`, ...) are excluded from `tf_checksum`. For *cache reuse alone*, they are irrelevant. For *re-execution or fingertipping*, the recipient must be able to reconstruct them locally.

Implementer corollary: a share aimed only at cache hits may legitimately omit dunder content. A share that promises fingertipping or re-execution must materialize the dunders the worker will need.

### Scratch interacts with sharing non-trivially

Per [cache-storage-and-limits.md](seamless/docs/agent/contracts/cache-storage-and-limits.md) and [scratch-witness-audit.md](seamless/docs/agent/contracts/scratch-witness-audit.md):

- A scratch result has no stored bytes; the recipient can only obtain its value by re-deriving it.
- Re-derivation requires the producer transformation's identity **and** that producer's inputs to be present in the slice (recursively, until something materialized is reached).
- A share that names a scratch buffer without naming its producer (or the producer's inputs) is a **silent failure surface**: a top-level cache hit, followed by a fingertip failure deep inside the cascade at consumption time.
- Witness artifacts (per `scratch-witness-audit.md`) must not be scratch-only in a share that purports to support audit or cross-environment comparison.

### Execution records are evidence, not identity

Per [execution-records.md](seamless/docs/agent/contracts/execution-records.md):

- Records ride alongside `(tf_checksum, result_checksum)`; they do not change cache identity.
- A `MetaData` body difference for the same `(tf_checksum, result_checksum)` is evidence disagreement about the execution envelope — surface it, do not treat it as a cache conflict.
- A `result_checksum` mismatch for the same `tf_checksum` is a referential-transparency violation; it migrates to `IrreproducibleTransformation` and the migration is one-way. `seamless-share` import paths must respect this migration and never reverse it.
- The optimistic null hypothesis (that scientifically meaningful results are invariant under environment variation) is the framing under which records exist; see [env-null-hypothesis](~/.claude/skills/seamless-adoption/references/env-null-hypothesis.md).

### Provenance cascade

A share frequently spans a chain `... -> X -> Y -> Z` where each step is a transformation whose inputs include the previous step's result. Treat the cascade as a first-class object:

- **Cache reuse** at `Z` requires `Z`'s `tf_checksum` and the input identities `Z` consumes (some of which are `Y`'s `result_checksum`).
- **Re-derivation** at `Z` requires `Y`'s bytes (or `Y`'s re-derivability through the same recursion).
- **Editing** at `Y` and replaying forward requires `Y`'s inputs to be materializable.

Slices that satisfy one of these but not the others are not wrong — they are **narrower-purpose**. The publisher must label which purposes the slice supports; the recipient must verify before relying on a property the slice does not promise.

## Operational primitives

`seamless-share` provides — independently of any specific usage pattern — a small set of operational primitives that the patterns below compose. Implementers must treat these as first-class APIs, not as ad-hoc helpers.

### Local working copy of `seamless.db`

A *local working copy* is a file-on-disk snapshot of (a subset of) a remote `seamless.db`, decoupled from the remote so the user can experiment without disturbing team state.

- **Fork.** Produce a local `seamless.db` from a remote source. The local copy must record a **fork-point token** (e.g., a digest of the remote db at fork time, or the remote's protocol-level sequence) so later operations can detect divergence.
- **Launch with override.** Run computations against the local copy as the database endpoint, while the **bufferdir endpoint remains remote** (the team hashserver). This is implemented on top of [seamless-config](seamless-config/), overriding the resolved database path in `clusters.yaml` / `seamless.yaml` without altering the hashserver endpoint.
- **Merge-back.** Replace the remote `seamless.db` with the local working copy, atomically.

The bufferdir is *not* part of the working copy in this primitive. Buffers are content-addressed, shared across users, and accessed through the hashserver throughout the WIP cycle.

### "Atomic merge-back" is a contract, not a command

`scp` alone is not atomic; a recipient process that opens the remote db mid-copy sees a torn file. The merge-back primitive must implement, at minimum:

1. Stage the new file at a side path on the destination.
2. `fsync` it.
3. `rename` it over the destination (POSIX rename is atomic on the same filesystem).
4. Verify (re-open, query a known row, compare checksum).

A pre-condition check against the recorded fork-point token is recommended where the storage layer allows it. If concurrency control is required, it is layered above this primitive (see scope note below).

### Concurrency note

Concurrent access to the remote `seamless.db` by multiple working copies is **out of scope** for `seamless-share` at present. Tooling assumes external coordination (a human convention, an organizational lock, or single-user usage). The tool **must document** this assumption and must not silently corrupt state when the assumption is violated — i.e., merge-back must at least be atomic enough that a concurrent reader sees either the pre- or post-merge state, never a torn file.

## Implementer obligations (cross-pattern)

These hold across every usage pattern below. Pattern-specific obligations layer on top.

- **Closure correctness.** The share artifact's manifest enumerates every identity in the slice. Every identity in the manifest is either (a) materialized in the artifact, (b) marked scratch-by-design with its producer also in the slice, or (c) explicitly listed as out-of-slice with a documented policy for the recipient. There is no fourth category.
- **Witness preservation.** Any artifact tagged as a witness is materialized — never scratch-only — in any share whose declared purpose includes audit or cross-environment comparison. For purely-internal rehydration shares, witness handling is still documented in the manifest.
- **Idempotent import.** Importing the same share twice must be a no-op for any identity already present locally. `MetaData` conflicts on identical `(tf_checksum, result_checksum)` surface as evidence, never as import failures. Result-checksum conflicts route through `IrreproducibleTransformation` per the database protocol.
- **Predicted-hit oracle.** Every publisher-side share operation emits a **predicted-hit list**: the `tf_checksum`s the publisher expects to satisfy from cache on the recipient side. Every recipient-side import emits the **realized-hit list**. Divergence between the two is the primary integration-test signal.
- **No silent re-execution.** If, after import, a computation that the share *promised* to cache instead triggers re-execution, that is a defect — not a "soft degradation". Tests must fail when this happens.

## Test design checklist (cross-pattern)

For every concrete pattern below, implementers must produce tests covering at least these categories. Patterns will add more.

1. **Round-trip happy path** — publish, import on a clean recipient, verify realized hits match predicted hits exactly.
2. **Closure gap** — publish with a deliberately omitted dependency; recipient must refuse to claim the cache-hit guarantee, surface the gap, and fail loudly if asked to materialize the missing identity.
3. **Scratch-without-producer** — publish a scratch buffer whose producer is *not* in the slice; recipient must detect this at import time, not at first fingertip attempt.
4. **Witness scratched** — attempt to publish a witness-tagged buffer as scratch-only; publisher must reject before producing the artifact (or, for internal-only shares, the manifest must record the omission explicitly).
5. **Re-import** — import twice; second import is a no-op; manifests reconcile without spurious writes to `Transformation`, `RevTransformation`, `MetaData`, or buffer storage.
6. **Metadata divergence** — import a record whose `MetaData` body differs from a locally-stored one for the same `(tf_checksum, result_checksum)`; import succeeds, divergence is surfaced for audit, cache identity is unchanged.
7. **Result divergence** — import a `(tf_checksum, result_checksum)` whose `result_checksum` conflicts with a locally-stored one for the same `tf_checksum`; import routes through `IrreproducibleTransformation`; never silently overwrites.
8. **Cascade re-derivation** — import a slice that requires fingertipping at an internal step; verify `allow_input_fingertip` paths function and the producer is invoked exactly when (and only when) expected.
9. **Driver/nested closure** — import a share rooted at a driver transformation; verify each inner `tf_checksum` is independently hittable and editable.

## Concrete usage patterns

*To be filled in.* The remainder of this file enumerates concrete patterns explained by the project author. Each pattern follows the structure: **Intent · Closure rules · Frontier policy · Must hold · Must reject · Must test**. This introduction is the shared substrate every pattern assumes.

### Pattern 1: Consolidation and crystallization

#### Intent

During the WIP phase of a Seamless project, the local-working-copy database accumulates cache entries from many revisions of the code and parameters. At some point — either for disk space, for cognitive clarity over provenance, or for publication — the user wants to **shrink the database to a chosen kept set** and either:

- **Consolidate** — push the shrunken database back to the remote, replacing it; the user continues working from a smaller, cleaner state.
- **Crystallize** — emit the shrunken database (plus its referenced buffers) as a distributable artifact; the user's working state is unaffected.

These are the same closure operation with different destinations; they share the kept-set computation and differ only in what is done with the result.

This is the Seamless analog of `git gc` / `filter-branch` (consolidation) and `python -m build` + publish (crystallization). Source code has VCS and packaging tooling; for the `seamless.db` + bufferdir combination, `seamless-share` reinvents the necessary subset.

#### Two destinations, one operation

| Destination | Action | Effect on local working copy | Effect on remote `seamless.db` | Effect on bufferdir |
|---|---|---|---|---|
| **Consolidate** | shrink → merge-back | shrunken (or replaced by the shrunken copy) | replaced by the shrunken local copy (atomic) | not directly affected; unreferenced buffers become candidates for separate, later GC |
| **Crystallize** | shrink → emit artifact | unaffected (or shrunken, by flag) | unaffected | emit a companion bufferdir containing the kept set's buffers (using Seamless's existing file/dir digestion tools); remote unchanged |

The crystallized artifact is a `seamless.db` file in the native format, accompanied where needed by a bufferdir directory of byte-files keyed by checksum. There is no separate packaging format: Seamless's existing tooling already handles ingestion on the recipient side.

#### Keep closure

Given a kept-root set `R` (a set of `tf_checksum`s the user has named as "must survive"), the kept closure is computed by walking the identity graph in the local `seamless.db`:

1. Start with the `Transformation` rows for each `tf_checksum` in `R`.
2. For each kept transformation, traverse its inputs (declared input pins and any nested `tf_checksum`s referenced by drivers). Add producer transformations whose results are consumed as inputs.
3. Repeat to a fixed point.
4. Result: a kept set `K ⊇ R` of `tf_checksum`s.

`R` may be specified by:

- **Explicit list** of `tf_checksum`s.
- **Derived from project state** — the user points at the current code/script/notebook, and tooling extracts the `tf_checksum`s that the current state references or has just produced. (Fragile when code has mutated between resolution and execution; implementers must surface the resolved list for confirmation before destruction.)

In the absence of an explicit `R`, the default is **`R = all tf_checksums currently in the local working copy`** — i.e., no shrinking, only the local-copy-as-snapshot semantics. The user must opt into shrinking.

#### Drop policy (local working copy)

The complement `D = (local copy's tf_checksums) ∖ K` is dropped from the local working copy:

- `Transformation`, `RevTransformation`, and `MetaData` rows for `tf_checksum`s in `D` are deleted.
- `IrreproducibleTransformation` rows are **preserved by default**, regardless of whether their `tf_checksum` is in `D`. An explicit `--drop-irreproducible` flag is required to drop them; the dry-run must surface the count.
- Witness-tagged buffers referenced by `D` are surfaced in the dry-run as "evidence about to be dropped"; an explicit `--drop-witnesses` flag is required to drop them.

The bufferdir is **not** modified by this pattern's drop policy. Buffers referenced by `D` but not by `K` become candidates for a separate, later GC operation; that GC is out of this pattern's scope.

#### Merge-back (consolidation only)

After shrinking, the local working copy is merged back to the remote using the [atomic merge-back primitive](#atomic-merge-back-is-a-contract-not-a-command). Implementer obligations specific to consolidation:

- The merge-back **replaces** the remote, it does not union. Rows present remotely but absent locally are **dropped on the remote** by this operation — this is intentional (consolidation's purpose) but means the dry-run must explicitly show the rows about to disappear from the remote, not only the rows being dropped from the local working copy.
- The fork-point token recorded at fork time must be checked against the current remote state before merge-back. If divergence is detected, the tool must refuse and surface the divergence; concurrency reconciliation is out of scope.

#### Artifact emission (crystallization only)

The crystallized artifact is:

- A `seamless.db` file containing exactly the kept set `K`, its `MetaData`, and `IrreproducibleTransformation` rows for `tf_checksum`s in `K` (and optionally `IrreproducibleTransformation` rows whose `tf_checksum` is outside `K` but whose preservation the user opted into).
- A companion bufferdir (a directory of byte-files keyed by checksum) containing every buffer the kept set declares as materialized.

No bespoke packaging format. The recipient ingests via existing Seamless tooling.

**Compression at artifact emission.** Per [compression.md](seamless/docs/agent/contracts/compression.md), compression is identity-transparent — `.zst` / `.gz` forms share the canonical checksum with the uncompressed form. The publisher can ship buffers in any form without affecting cache identity. Two consequences worth anticipating:

- If buffers are shipped compressed, **pre-generate `.BUFFERLENGTH` sidecars** before stripping the uncompressed form, or the recipient's hashserver pays a full decompression cost on every length query. Cheap to generate at publication, expensive to recover later.
- The recipient must have the relevant decompression library available (`zstd` or `gzip`). The artifact has no way to declare this; the publisher should make the assumption explicit in the artifact's accompanying documentation if a non-default format is used.

#### Safety obligations

- **Dry-run is the default.** Destructive execution requires an explicit flag.
- **The dry-run must report**: `|R|`, `|K|`, `|D|`, count of `IrreproducibleTransformation` rows in scope, count of witness-tagged buffers in scope, total disk-bytes to be reclaimed.
- **`IrreproducibleTransformation` and witnesses are preserved by default.** Opt-out flags are required to drop them.
- **Atomic merge-back, always.** No partial writes to the remote.
- **Fork-point check before merge-back.** Refuse on divergence.
- **Concurrency assumed external.** The tool must document the assumption and not silently corrupt on violation.

#### Must hold (post-operation invariants)

- After consolidation: the remote `seamless.db` is structurally consistent (every `RevTransformation` row has a matching `Transformation` row; every `MetaData` row references a present `Transformation` row).
- After consolidation: every `tf_checksum` in the kept set `K` resolves to a `result_checksum` on the remote that satisfies the same policy (rehydrate / fingertip / re-execute) it satisfied before.
- After crystallization: the artifact is self-contained for its declared policy (closure correctness, per the cross-pattern obligations).
- After both: any `IrreproducibleTransformation` row that existed before exists after, unless the user explicitly opted to drop it.

#### Must reject

- A consolidation operation whose dry-run was not viewed (i.e., destructive flag without prior dry-run, in the absence of an explicit `--yes-i-know` override).
- A consolidation operation whose fork-point token does not match the remote's current state.
- A crystallization operation that would emit a witness-stripped artifact without explicit opt-in.
- Any operation whose kept set `K` is empty when `R` was specified non-empty (indicates a closure-walk bug, not a user intent).
- Any operation that would, post-operation, leave a `Transformation` row referring to a `result_checksum` neither materialized nor re-derivable from `K`.

#### Must test

In addition to the cross-pattern checklist:

10. **Fork → modify → consolidate → push** round trip, with no shrinking: remote is byte-equal to a copy of the local working copy; fork-point token validates.
11. **Fork → shrink with explicit `R` → consolidate**: remote's surviving `tf_checksum`s equal `K`; `MetaData` and `IrreproducibleTransformation` invariants hold; structural-consistency check passes.
12. **Fork → shrink → crystallize**: emitted artifact's `seamless.db` contains exactly `K`; companion bufferdir resolves every materialized buffer the artifact's policy declares.
13. **Closure-walk correctness on driver/nested transformations**: a kept driver `tf_checksum` pulls all its inner `tf_checksum`s into `K`.
14. **Scratch producer pulled into `K`**: if a kept transformation consumed a scratch output from producer P, P (and P's transitive inputs) are in `K`.
15. **`IrreproducibleTransformation` preservation**: a row outside `K` is preserved by default; the `--drop-irreproducible` flag is required to drop it.
16. **Witness preservation**: a witness-tagged buffer outside `K` is surfaced in the dry-run; the `--drop-witnesses` flag is required to drop it.
17. **Fork-point divergence**: simulate a remote change after fork; merge-back must refuse and surface the divergence.
18. **Atomic merge-back under crash injection**: kill the tool mid-merge; the remote file is either pre-state or post-state, never torn.
19. **Empty-`K` guard**: a `R` that resolves to `K = ∅` (e.g., because the user-supplied roots are absent from the local copy) must abort, not produce an empty remote.
20. **Buffer untouched**: after consolidation, the bufferdir endpoint reports the same contents as before — consolidation does not touch buffers directly.

### Pattern 2: Cache-miss diagnosis (`why-not`)

#### Intent

A user (or upstream agent) expected a cache hit on some transformation `T`, but observed a miss. The pattern provides tools to answer *"why?"* structurally: which determinant of `T`'s identity differs from a comparable cached transformation `T'`?

The pattern is **read-only and diagnostic**. It never writes to any database, bufferdir, or hashserver. Tests must assert this property.

#### Two layers, separable by contract

This pattern specifies two tools that must be implemented as separable units. A lesser agent should not collapse them into one.

| Layer | Proposed name | Inputs | Output | Purpose |
|---|---|---|---|---|
| Low-level primitive | `transformation-diff` | Two transformation references (a `tf_checksum`, an unwrapped transformation dict, or a path to a transformation definition) and a database endpoint | Structural diff annotated with plain-vs-dunder | Reusable by other tools (audit, manifest reconciliation, future cascade-explainer) |
| User-facing tool | `why-not` | One transformation reference (the missing one) and a database endpoint | A diagnosis: cache-miss-shape + (if applicable) a diff against a heuristically-chosen candidate | End-user diagnostic for "why didn't this hit?" |

`why-not` must call `transformation-diff` to produce its diff. `why-not` must not contain its own diff logic.

#### Two orthogonal axes: lookup state and candidate diff

Reporting a cache miss is the composition of two independent answers. The tool must produce both; one must never substitute for the other. Conflating them into a single "shape" enum is wrong, because a single user action routinely produces a complex result along axis 2.

**Canonical example.** Adding a parameter to a function decorated with `@direct` or `@delayed` produces, in one user action, *both*: a new input key in the transformation dict (the parameter becomes a new input pin) *and* a value difference on the plain key `code` (the function's source text changed). Both are real, independent identity-determining changes. The tool must report both. A "primary cause" framing here would mislead — there is no primary cause; the user added a parameter, and Seamless honestly reflects that as two coupled changes in the transformation dict.

**Axis 1: lookup state of the queried `tf_checksum`.** What does the queried database endpoint (or union of endpoints) say about the *exact* queried `tf_checksum`?

| Lookup state | How to detect | What it means |
|---|---|---|
| `NOT_PRESENT` | No `Transformation` row and no `IrreproducibleTransformation` row | The transformation has never been recorded (or has been deleted). Candidate diff (axis 2) is the path forward. |
| `IRREPRODUCIBLE` | `IrreproducibleTransformation` rows exist for this `tf_checksum` | The miss is intentional: a referential-transparency violation was recorded. Surface the row count and `result_checksum` set. Candidate diff is not run by default — the same `tf_checksum` is by definition not diff-against-itself. |
| `PRESENT_RESULT_UNAVAILABLE` | `Transformation` row exists; `result_checksum` named; buffer not materializable and not re-derivable per the policy | The identity is cached; the result is not. The user's observed "miss" is not an identity miss; searching for a code change will not explain it. |
| `PRESENT_AS_HIT` | `Transformation` row exists; result materializable or re-derivable per the policy | This is in fact a cache hit on the queried endpoint set. The user's observation of a miss is either against a different endpoint (the tool's endpoint set is incomplete) or describes a non-identity concern (e.g., latency, eviction). The tool must say so explicitly rather than reporting an empty diff. |

The lookup state must be reported in every run. It is the answer to "did the cache see this identity?" and is independent of any diff.

**Axis 2: candidate diff.** Only meaningful when axis 1 is `NOT_PRESENT`. The user-facing tool may select a candidate `tf_checksum` from the cache; the primitive runs the diff against it. The diff is a **list of independent differences** — never a single category. The list must be reported in full; nothing is collapsed, prioritized, or hidden.

#### Diff entries

A diff is a list of entries. Each entry is independently:

- **Side**: `key_only_in_A` / `key_only_in_B` / `value_differs`.
- **Classification**: `plain` (part of `tf_checksum`, identity-determining) or `dunder` (execution-only).
- **Value**: at default depth, checksums (or absent, for key-only-in-X entries); with `--deep`, content rendered appropriately to the buffer's type.

The aggregate flag `identity_relevant` is `true` iff at least one entry is classified `plain`. A diff with only `dunder` entries cannot explain a real cache miss; the user-facing tool must surface this prominently — it indicates the candidate was a poor choice and the real explanation lies elsewhere.

There is no "primary" difference. The tool must not present one entry as *the* reason at the expense of others. An agent or user reading the output gets the *complete* identity-relevant delta.

`transformation-diff` operates on the unwrapped transformation dict — the dict whose checksum is `tf_checksum`, plus its dunder companions. The plain/dunder classification of each key follows from [identity-and-caching.md](seamless/docs/agent/contracts/identity-and-caching.md); when in doubt, consult that contract rather than assuming.

#### Common scenarios and what the diff shows

Below are concrete user actions and the dict-level changes a lesser agent should expect when running the diff. Use these as anchors when interpreting tool output or designing test fixtures. The list is **not exhaustive and not a taxonomy**; it is a feel-for-the-shape reference. Where this list contradicts the contracts in [identity-and-caching.md](seamless/docs/agent/contracts/identity-and-caching.md) or [modules-and-closures.md](seamless/docs/agent/contracts/modules-and-closures.md), the contracts win.

For each scenario: *what the user did* → *what shifts in the transformation dict* → *what to expect in the diff output*.

##### Edits to a `@direct` / `@delayed` function

- **Add a parameter.** `f(a, b)` becomes `f(a, b, c)` and a call site passes the new value. The dict gains a new input pin for `c`. The `code` value (function source text) also changes, because the signature is part of the source. → Diff has at least two entries: one `key_only_in_input` for the new pin, one `value_differs` on `code`. Both classified `plain`. One user action, two coupled changes — this is normal.
- **Remove a parameter.** Mirror image. → One `key_only_in_candidate` plus a `code` value difference.
- **Rename a parameter.** `f(a, b)` becomes `f(a, x)`. Pin name changes; source text changes. → Three entries: `key_only_in_input` (new name), `key_only_in_candidate` (old name), `value_differs` on `code`. A rename is not "one diff"; it is three.
- **Add a default value to a parameter.** Signature in source changes; if the call site still passes the same arguments, pin values are unchanged. → One `value_differs` on `code`. No key set change. Cache miss even though the call site is byte-identical.
- **Reformat / whitespace / comment-only edit.** No semantic change, but the captured source text differs. → One `value_differs` on `code`. The cache miss is real. Seamless cannot distinguish "renamed a local" from "rewrote the algorithm" — they look identical at the dict level. A lesser agent should be prepared to tell the user: cosmetic edits invalidate the cache exactly like semantic edits. This is a property, not a bug.
- **Mild refactor preserving semantics** (rename a local, extract an inline expression). Same as above — `code` changes, identity changes.
- **Algorithmic change** (replace a loop with a comprehension, swap a constant). Indistinguishable from cosmetic at the dict level. → One `value_differs` on `code`. Semantic significance lives outside the cache key by design.

##### Changes at the call site or inputs

- **Pass a different value for the same parameter.** The pin's checksum value changes; `code` unchanged. → One `value_differs` on the affected input pin. Classified `plain`. No key set change.
- **Upstream transformation now produces a different result checksum.** From the current transformation's local view, this looks identical to "pass a different value": one input pin's checksum changed, `code` unchanged. → One entry on the affected input pin. The *cause* of the upstream change is a cascade question — out of v1 scope, but the next thing the user will ask.
- **Pass a value that normalizes to the same buffer.** Distinct user input that, after Seamless's input normalization, yields the same buffer checksum. → Empty diff; lookup state `PRESENT_AS_HIT`. The tool reports this clearly rather than implying the user was wrong about caching.

##### Changes to the transformation envelope

- **Add or remove an embedded module / closure** (the `objects` set or its equivalent). The dict's modules-related plain key set changes. → A `value_differs` (or key-add/remove) on the modules-related key. The `code` value may or may not also change, depending on whether the helper was inlined or imported.
- **Change a captured closure variable** that is implicitly captured rather than passed as an argument. Per [modules-and-closures.md](seamless/docs/agent/contracts/modules-and-closures.md), implicit closures are a known anti-pattern: identity may not track such changes correctly. → If Seamless captured the closure into a plain-key value, the diff shows that entry. If it did not, the diff is empty and the user has a real bug — a silently wrong cache hit. When the tool reports an empty diff in a context where the user is sure they changed something, the output should hint that an implicit closure may be at fault.
- **Change a pin's celltype** (e.g., `text` → `bytes`, or switch to a deep-checksum cell). Celltype declarations are part of the transformation specification. → An entry on the key that holds the celltype declaration. Whether that key is plain or dunder depends on the specific celltype semantics; consult [identity-and-caching.md](seamless/docs/agent/contracts/identity-and-caching.md) and the pin schema rather than assuming.
- **Change `__env__`** (e.g., switch conda environment). `__env__` is a dunder. → One `dunder` entry. `identity_relevant: false`. The tool surfaces this prominently: this does not explain a real cache miss. If the user observed a real miss, it is elsewhere.
- **Change `__compilation__`** (e.g., compiler flags `-O3` → `-g` for a compiled transformer). Dunder. → Same shape as `__env__`: identity-irrelevant; surfaced as such.
- **Switch decorator `@direct` ↔ `@delayed`.** Same code, same parameters, same call. Both produce equivalent transformation dicts. → Empty diff; lookup state `PRESENT_AS_HIT`. The tool must not invent a miss to explain.

##### `seamless-run` CLI scenarios

- **Change the command string.** The command lives in a plain key. → One `value_differs` on the command key.
- **Change `--metavar`.** Per the contract, `--metavar` is an execution hint and lives in a dunder. → One `dunder` entry; `identity_relevant: false`.
- **Change argument typing** (e.g., declare an arg as `--file` vs `--text`). This changes how Seamless interprets the argument's content, typically changing the resulting input pin and its checksum. → One or more entries on the affected pin; consult [seamless-run-and-argtyping.md](seamless/docs/agent/contracts/seamless-run-and-argtyping.md) for the exact key shape.
- **Switch a CLI input pin between compressed and uncompressed forms** (e.g., `file.npy` vs `file.npy.zst`). Per [compression.md](seamless/docs/agent/contracts/compression.md), compression is identity-transparent at the *canonical buffer* level — both forms share the buffer checksum — but the CLI/bash face encodes the compression suffix in the **pin name**. Different pin name → different transformation dict → different `tf_checksum`. → One or more entries on the affected pin (the pin name itself differs as a key). A user who expects "same content, same cache hit" is surprised to see a miss. The tool should surface this honestly rather than rationalizing; the diff *is* real at the transformation-dict level even though the canonical content is identical.

##### Out-of-band gotchas the tool should hint at

- **Empty diff against a believed-changed code path.** Often means an implicit closure was the changed determinant and Seamless never saw it. The tool should hint at this rather than declaring "no diff" and stopping.
- **All entries `dunder`.** The candidate is the wrong one (or the change does not affect identity); the tool should say so plainly instead of leaving the user to read the classification.
- **Diff present locally, but lookup state `PRESENT_AS_HIT` on another endpoint.** The user looked at the wrong endpoint. Report both findings — "the queried endpoint has no record; endpoint *X* has it as a hit" — so the user can correct course.

#### Default depth and the `--deep` flag

`transformation-diff` and `why-not` must default to **checksum-level comparison** of differing values:

- For a key whose value differs, the default output is `{value_A: '<hex>', value_B: '<hex>'}`.
- The `--deep` flag is opt-in. It instructs the tool to fetch the underlying buffers (via the database endpoint's hashserver) and present a content diff appropriate to the buffer's type: line-level for textual content (e.g., `code`), structural for JSON-shaped values (e.g., args), checksum-only fallback for opaque/binary content.
- `--deep` must not fail the operation when a buffer is unavailable (scratched, evicted). It must fall back to checksum-level output for that key and continue.

#### Heuristic candidate selection (user-facing tool only)

`why-not` selects a candidate transformation in the cache to diff against. The candidate-selection heuristic is a part of the user-facing tool, not the primitive. Implementer obligations:

- The selection algorithm must be **documented and deterministic**: same inputs and same database state yield the same candidate.
- The selection must **score by overlap on plain keys first** (since dunder differences cannot explain a miss), and only break ties by dunder overlap.
- The tool must report the **selection score and runner-up candidates** so a user can override and rerun the primitive against a different candidate if the heuristic chose poorly.
- The tool must refuse to select a candidate when no transformation in the cache shares at least one plain-key value with the input. Reporting NEVER_COMPUTED is preferable to inventing a misleading diff.

#### Endpoint composition

Both tools must accept arbitrary database endpoints; they must not assume "the" cache. In particular, the tools compose with the [local working copy](#local-working-copy-of-seamlessdb) primitive: a user must be able to ask `why-not` to diff against a remote database, a local working copy, a pre-consolidation snapshot, or any combination by enumerating endpoints.

When multiple endpoints are supplied, the cache-miss taxonomy is computed as the **union view** (a transformation is NEVER_COMPUTED only if absent from all endpoints; IRREPRODUCIBLE if any endpoint reports it as such; etc.). The endpoint that supplied each row must be reported in the output.

#### Out of scope (v1)

- **Cascade explanation.** Recursive `why-not` on upstream transformations whose results feed the queried transformation is **not** part of this pattern. It may be a future pattern built on top of `transformation-diff`. Implementers must keep `transformation-diff` clean enough to support it later.
- **Metadata divergence.** `MetaData` body differences are evidence-about-execution, not identity; they never cause a cache miss and are not part of this pattern. Audit tooling that consults metadata lives elsewhere.
- **Buffer-content normalization.** The `--deep` output is a presentation aid; the *identity* judgment uses checksums only. Tools must not implement "fuzzy" or "tolerance-based" identity comparison anywhere in this pattern.

#### Must hold (post-operation invariants)

- After any run of either tool: no `Transformation`, `RevTransformation`, `MetaData`, `IrreproducibleTransformation` row has been written or modified; no buffer has been written to any bufferdir or hashserver.
- The output's lookup-state field is exactly one of `{NOT_PRESENT, IRREPRODUCIBLE, PRESENT_RESULT_UNAVAILABLE, PRESENT_AS_HIT}`. The two axes (lookup state and candidate diff) are reported as independent fields; lookup state is always populated; diff is populated when (and only when) axis 1 is `NOT_PRESENT` and a candidate was selected.
- The diff field, when populated, is a complete list of every key-level difference between input and candidate. The tool does not collapse, prioritize, or omit entries. Entries differing only in classification (plain vs dunder) are not merged.
- The output's `identity_relevant` flag is `true` if and only if at least one diff entry is classified `plain`.
- When `--deep` is requested and a buffer is unavailable: the tool exits with success; that key's output falls back to checksum-level; the unavailability is reported in the output.

#### Must reject

- A `transformation-diff` invocation with fewer or more than two transformation references.
- A `why-not` invocation that asks the heuristic selector to operate on a database endpoint set that contains zero `Transformation` rows. (Distinct from "candidate found is unrelated"; this is "no haystack to search".)
- Any request that would require writing to a database or bufferdir endpoint. The tools must not have a write code path at all; reject by construction, not by runtime check.

#### Must test

In addition to the cross-pattern checklist:

21. **Identical-pair diff**: two identical transformation dicts produce a diff with no entries and `identity_relevant: false`.
22. **Plain-only diff**: two dicts differ in a single plain key; `identity_relevant: true`; entry classified `plain`.
23. **Dunder-only diff**: two dicts differ in a single dunder key; `identity_relevant: false`; entry classified `dunder`. The user-facing tool's output must explicitly state that this diff does not explain the miss.
24. **Mixed diff**: differences in both plain and dunder keys; `identity_relevant: true`; both classifications present.
25. **Key-only-in-A**: one dict has a key the other lacks; diff records the side; classification is correct.
26. **Lookup `NOT_PRESENT`**: query a `tf_checksum` absent from all endpoints; lookup state is `NOT_PRESENT`; diff is empty when no candidate is selected.
27. **Lookup `IRREPRODUCIBLE`**: query a `tf_checksum` present only in `IrreproducibleTransformation`; lookup state is `IRREPRODUCIBLE`; candidate-diff is not run by default, even if a similar-shape transformation exists in the cache.
28. **Lookup `PRESENT_RESULT_UNAVAILABLE`**: `Transformation` row exists for the queried `tf_checksum`, but the `result_checksum` cannot be materialized or re-derived; lookup state is `PRESENT_RESULT_UNAVAILABLE`; the user is not misled into looking for a code change.
29. **Lookup `PRESENT_AS_HIT`**: query a `tf_checksum` that is in fact a cache hit on the endpoint set; lookup state is `PRESENT_AS_HIT`; tool surfaces this explicitly rather than emitting an empty diff or pretending the user was right about there being a miss.
30. **Canonical multi-difference case**: starting from a `@direct` or `@delayed` function with parameters `(a, b)` and a cached `tf_checksum`, add a parameter `c` and rerun. `why-not` against the cache must report a diff with at least two entries: (i) a `key_only_in_candidate=False` (i.e., the new input key for `c` is present in the input transformation, absent from the candidate), and (ii) a `value_differs` entry on `code` (the function source changed). Both entries are classified `plain`. `identity_relevant` is `true`. No entry is collapsed or marked "primary".
31. **Deep diff on textual content**: with `--deep`, a `code`-key difference produces a line-level diff of the underlying source.
32. **Deep diff with missing buffer**: with `--deep`, a key whose underlying buffer is unavailable falls back to checksum-level output; the run does not fail.
33. **Read-only assertion**: before/after the tool runs, checksums of all queried database files and bufferdir entries are unchanged.
34. **Heuristic determinism**: same input, same database state ⇒ same candidate selected by `why-not`; same diff output.
35. **Heuristic refusal**: when no candidate in the cache shares any plain-key value with the input, `why-not` reports lookup state `NOT_PRESENT` with no candidate selected and an empty diff, rather than diffing against a maximally-unrelated candidate.
36. **Endpoint union**: with multiple endpoints, the lookup state is computed correctly under the documented union rules; per-endpoint provenance is preserved in the output.
37. **Dunder-only diff is flagged**: a candidate diff whose entries are all classified `dunder` produces `identity_relevant: false`; the user-facing tool surfaces this prominently as "candidate does not explain the miss".
38. **No cascade in v1**: a transformation whose only differing plain key references the `result_checksum` of an upstream transformation is reported as a single-step diff (one differing key); the tool does not auto-recurse.

### Pattern 3: Replay-mode verification of a crystallized artifact

#### Intent

A crystallized artifact (Pattern 1's output: a `seamless.db` plus a companion bufferdir) is meant to deliver cache hits to recipients running a known client script. **Replay mode** is the acceptance test: it runs the recipient's intended script against the artifact under tightened cache discipline, so that any miss, fingertip attempt, or buffer-fetch outside the local bufferdir is a *verification failure* rather than a silent fallback to recomputation or remote fetch.

The pattern composes the prior two: it reads Pattern 1's output, and it uses Pattern 2's `transformation-diff` primitive to explain each unexpected miss.

#### Replay mode is a Seamless runtime mode, not just a `seamless-share` tool

Replay mode changes how Seamless's transformation submission and buffer resolution paths behave. The mode flag must flow into the Seamless runtime; `seamless-share` provides the harness (set up the local bufferdir, configure endpoints, launch the script, collect findings), but the gating decisions happen inside Seamless itself.

| Mechanism | Normal mode | Replay mode |
|---|---|---|
| Cache lookup (`tf_checksum` → `result_checksum`) for non-driver | hit serves; miss queues for execution | hit serves; miss **is a finding** |
| Cache lookup for driver `tf_checksum` | hit serves | **bypassed by default**; the driver re-runs so its sub-transformations are actually submitted and their cache discipline verified. A knob is available to flip this back on (e.g., a smoke-test mode where only the outermost result needs to verify). |
| Buffer materialization (`checksum` → bytes) | resolve from local bufferdir → remote hashserver → fingertip, in order | **suspicious by default.** Every materialization must trace to an explicit publisher authorization (see below). Anything else is a finding. Seamless's existing behavior — fail when materialization is required and unsatisfied — is the substrate; replay mode tightens which sources count as "satisfied". |
| Input fingertipping (re-execute producer for a missing scratch input) | enabled per-consumer via `allow_input_fingertip` | **disabled by default**; may be selectively re-enabled by the publisher for specific authorized cases. Any fingertip attempt outside the authorization is a finding. |
| `IrreproducibleTransformation`-only hit | not a clean cache hit; normal mode may attempt re-execution | a finding: the recipient may not want this in their artifact, even if the publisher consciously shipped it |

#### Drivers are the only exception, and they must be identifiable by definition

A **driver transformation** is one whose body composes calls to other transformations at execution time — it expands at runtime into a graph of sub-transformations rather than computing a result from its inputs directly. Drivers are necessarily re-executed during replay (their sub-transformations are not knowable until they run); but every sub-transformation the driver emits must be a clean cache hit.

Replay mode identifies drivers from a **definition-time marker** in Seamless. Never by ad-hoc heuristic or post-hoc inference. The implementer should consult the current Seamless source for the marker's exact form (decorator, flag, or analogous mechanism) and rely on it directly.

Consequences:

- A non-driver `tf_checksum` that misses is a finding.
- A driver's own cache lookup is **bypassed by default**: the driver re-runs every time replay is invoked, even when its `result_checksum` is cached. This is what makes the verification meaningful — if the cached driver result were served directly, its sub-transformations would never be submitted and their cache discipline would not be tested. A knob exists to enable driver caching for cases where the user only wants to verify the outermost result (e.g., a smoke test).
- A driver that produces a sub-transformation which then misses is a finding — the miss is attached to the sub-transformation, with the driver's `tf_checksum` reported as context.
- Drivers may be nested arbitrarily deep; each level may execute; each level's outputs are verified.

**Driver weight matters.** The "re-run drivers every time" default assumes the typical idiomatic Seamless driver: one that composes sub-transformations and returns a *deep checksum* (a structured content identity that names data without materializing the bytes), so re-running it is cheap. Not every driver is this shape. A driver that does concatenation, aggregation, or anything else that requires materializing its bulk inputs is legitimately heavier — and when it re-runs locally in replay, it needs those bulk inputs materializable through the publisher's authorization (bufferdir or authorized fingertip). The mismatch — driver executes locally; buffers it consumes live remotely or have been scratched — surfaces as the normal findings (`Unauthorized materialization`, `Unauthorized fingertip`, `Authorized materialization with unsatisfied dependency`) attached to the driver's `tf_checksum` as context. No new finding kind; the existing surface handles it.

This is also where the driver-caching knob becomes most useful in practice: for an expensive driver the publisher does not want to pay on every replay, the knob can be flipped on. The trade-off is the one already named — caching the driver means its sub-transformations are not freshly submitted and verified.

#### Materialization in replay is a publisher-authorized exception

Replay mode inverts a normal-mode assumption: in normal Seamless operation, materialization is routine — buffers are resolved through a chain of fallbacks (local cache, remote hashserver, fingertipping) and the user rarely thinks about which source served any given byte. In replay mode, **every materialization is suspicious** until it is traced to a deliberate publisher authorization. This is the principle that drives the pattern; the specific mechanisms below are how that principle is currently expressed, not an exhaustive list.

Two things follow:

- Agents implementing or extending replay must anticipate the design tension. Anywhere Seamless would *quietly* resolve a buffer in normal mode, replay must instead consult an authorization and, if unauthorized, surface a finding. Decisions about how to express authorizations belong to the implementer; the durable obligation is that *the publisher's intent must be representable, auditable, and enforced*.
- The doc does not enumerate the complete set of valid authorization mechanisms. Currently anticipated mechanisms include the local bufferdir (pre-materialized bytes shipped with the artifact) and a whitelist of checksums that may be fingertipped on demand. Future mechanisms may be added; the test is whether each candidate mechanism is explicit, auditable, and verifiable, not whether it appears on this list.

##### Currently anticipated mechanisms

| Mechanism | What it ships | What the recipient does |
|---|---|---|
| Local (companion) bufferdir | the bytes themselves | reads them directly |
| Fingertip whitelist | the producer's `tf_checksum` mapping plus an authorization that this checksum may be re-derived | re-executes the producer to obtain the bytes; checksums not on the whitelist remain disabled |

Each entails publisher decisions and trade-offs the implementer must anticipate:

- **Space vs compute.** Large buffers that recompute cheaply favor the whitelist; small buffers that recompute expensively favor the bufferdir. Buffers whose producer depends on environment state the recipient lacks may not be fingertippable at all.
- **Transitive needs.** A whitelisted fingertip implies the producer must be cached, and the producer's own inputs must themselves be materializable by some authorized means. Replay should surface these transitive needs as findings as the script walks them, not assume them.
- **Authorization coherence.** An authorization that cannot be honored (e.g., a whitelist entry whose producer is absent from the cache) is a real failure shape the implementer must consider. How to surface it — refuse at startup, report as a finding when triggered, or both — is a design choice the implementer should make deliberately, not by accident.

##### The publisher's iteration loop

The publisher's expected workflow is to **converge on the minimum authorization spec**:

1. Start with the most restrictive setup that could plausibly work (empty bufferdir, empty whitelist, or deliberate seeds).
2. Run the script under replay mode.
3. For each finding: decide whether the publisher's intent is to (a) include the buffer directly, (b) authorize a fingertip, or (c) change the script so the materialization is no longer needed (e.g., `.run()` → `.compute()`).
4. Update the authorization spec accordingly. Re-run. Converge.

The converged authorization spec — whatever its shape — *is* the minimum materialization for the artifact. The crystallized artifact ships the database plus whatever authorized-materialization shape the implementer chose; replay verified that the recipient can satisfy the script's needs from exactly that shape.

#### Expected characteristics of a successful replay

If the artifact is sound and the publisher's authorization spec is right, a replay run should be:

- **Fast.** All heavy compute is already cached. The only work happening is cache lookups, driver re-execution (light for idiomatic deep-checksum-returning drivers; potentially heavier for drivers that materialize their bulk inputs — see the driver section), and any explicitly authorized fingertip re-derivations. A replay that takes orders of magnitude longer than the expected cost is a smell — something heavy is being recomputed or fetched that should not be.
- **Local.** Remote delegation (jobserver, daskserver, remote hashserver) should not be necessary. The crystallized artifact is a self-sufficiency claim; the replay is the verification of that claim. If remote delegation kicks in, the claim is in doubt.

These are observational properties, not gating thresholds — the implementer should not invent a fixed wall-clock budget. But the tool's report should make execution time and remote-traffic footprint *visible* so a publisher can notice when replay is doing more work than it should and investigate. A clean replay with no findings but unexpectedly high latency or unexpected remote traffic is its own kind of finding.

#### Failure modes (findings)

A replay run accumulates findings; it does **not** abort on the first one. The publisher iterates against a complete picture, not one finding at a time.

| Finding kind | Triggered by | What to report |
|---|---|---|
| **Unexpected miss** | non-driver `tf_checksum` lookup misses | the missing `tf_checksum`, the script position if available, the driver context (if inside one), and a `transformation-diff` / `why-not` result against the crystallized DB to explain |
| **Unauthorized materialization** | a buffer fetch could not be served from any publisher-authorized source | the checksum, the script context, the publisher's options for resolving it |
| **Unauthorized fingertip** | a consumer would have triggered input fingertipping on a checksum that is not authorized for fingertipping | the consumer's `tf_checksum`, the missing input, the producer `tf_checksum` that would have been re-executed |
| **Authorized materialization with unsatisfied dependency** | an authorized materialization cannot complete because a transitive dependency is itself not authorized or unavailable | the authorized target, the unsatisfied dependency, the transitive context |
| **Remote delegation observed** | execution dispatched to a remote backend (jobserver, daskserver, remote hashserver) during replay | what was dispatched, what backend, the script context. Replay's self-sufficiency claim is under doubt as long as this finding is present. |
| **Unexpected heavy compute** | a non-driver transformation ran (or a driver ran for an unexpectedly long time) | the offending `tf_checksum`, observed cost, and (for non-drivers) why it ran — typically this is dominated by the `Unexpected miss` finding above, but it is a useful cross-check when latency anomalies appear without a clear miss |
| **Irreproducible-only hit** | the only cache entry for the `tf_checksum` is in `IrreproducibleTransformation` | the row count, the `result_checksum` set, and a recommendation: keep with publisher acknowledgment, or remove from the artifact |

Each finding is a structured record; the report can be consumed by a human or by another agent that drives the iteration.

#### Read-only on the crystallized artifact

Replay must not write to the crystallized `seamless.db` or to the companion bufferdir. Findings live in a separate report file. The artifact under verification must be byte-identical before and after the run.

The local bufferdir is *writable* by the publisher between runs (that is how iteration converges), but the replay run itself does not add to it.

#### Composition

- **Reads Pattern 1's output**: the crystallized `seamless.db` + companion bufferdir directory.
- **Uses Pattern 2's `transformation-diff`**: each `unexpected miss` finding includes the diff against the closest cached candidate so the publisher can act.
- **Builds on operational primitives**: the same database-endpoint override that Pattern 1's working copy uses is reused here to point Seamless at the crystallized DB and the local bufferdir.

#### Implications for the implementer to anticipate

A handful of consequences fall out of this design that may not be obvious on first read. Surface them in the implementation rather than waiting for them to bite.

- **Replay and crystallization form a verification loop, not a one-shot pipeline.** Crystallization (Pattern 1) walks a closure from named roots — a *static* operation over the cache graph. Replay observes the transformations the script actually submits — a *dynamic* operation. They catch different gaps. A driver that produces sub-transformations dynamically may emit `tf_checksum`s the static walk could not have predicted; replay surfaces those as misses. The publisher's expected flow is crystallize → replay → if findings, expand the root set or rework the driver → re-crystallize → replay again, until convergence. Both tools should be designed assuming the other will be run, not in isolation.

- **Driver non-determinism becomes visible in replay.** A driver whose body depends on RNG, wall-clock time, environment variables, or other hidden state will produce *different* sub-transformations across replays — and across publisher-vs-recipient. Replay surfaces this as misses on sub-transformations whose `tf_checksum`s vary run-to-run. The deeper cause (the driver is non-deterministic) is invisible at the cache-key level: an `Unexpected miss` finding on a sub-transformation whose closest candidate differs only on environment-derived plain keys is most likely a driver-determinism problem masquerading as a cache problem. The `transformation-diff` diagnostic does not name this directly; the interpretive work falls to the human or agent reading the report. The tool's report should include enough context (the driver's `tf_checksum`, the run's environment fingerprint) that the diagnosis is reachable.

- **Replay must run with an isolated Seamless config.** The publisher's normal `seamless-config` typically resolves to endpoints (cluster, jobserver, remote hashserver) that exist precisely so normal work *can* delegate remotely. Replay must not inherit those endpoints — otherwise remote delegation silently succeeds and the self-sufficiency claim is undermined. The harness should construct a dedicated config for the replay process, not borrow the publisher's. Treat config-inheritance as a real bug surface, not a developer convenience.

- **Report ordering must be deterministic; the report should be machine-readable.** Sub-transformations within drivers often run in parallel; cache resolution returns in resolution order, which varies run-to-run. The findings report must sort entries by something stable (e.g., script line position, `tf_checksum`, finding kind) so two replays of the same setup produce byte-identical reports. Diff-based CI and regression tooling cannot otherwise tell "real change" from "concurrency noise". Relatedly, replay is a natural CI target — a publisher or a paper-archive operator wants periodic re-verification without human inspection. The report should be structured (JSON or similar), with a stable schema and meaningful exit codes that distinguish *clean* / *findings-present* / *tool-error*.

- **Preserved `IrreproducibleTransformation` rows surface in replay too.** Pattern 1 preserves these rows by default; Pattern 3 surfaces them as `Irreproducible-only hit` findings whenever the script touches them. This is the design working as intended: it forces the publisher to make an explicit decision — keep, with acknowledgment, or remove from the artifact — rather than letting inertia ship known-irreproducible identities. An implementer should expect that a "clean" replay on a real-world artifact will often include irreproducible findings the publisher must consciously sign off on.

#### Must hold

- After a replay run: the crystallized `seamless.db` and the publisher's authorized-materialization shape are byte-identical to their pre-run state.
- Findings are accumulated, not aborted on; a clean replay produces zero findings and a normal-shape script output.
- Drivers identified by the Seamless definition-time marker re-run by default (their own cache lookup is bypassed); non-drivers do not.
- Every byte materialized during the run traces to a specific publisher authorization. Any materialization that does not produces a finding rather than a silent success.
- Each `unexpected miss` finding carries a `transformation-diff`-shaped diagnostic body produced by Pattern 2's primitive.

#### A note on enforcement

The doc deliberately does *not* enumerate a list of conditions to refuse at startup. Seamless already chokes on its own when materialization is required and cannot be satisfied — that failure path is the substrate replay rides on. Adding tool-level refusals on top of it tends to either duplicate what Seamless does or hard-restrict legitimate variations of the workflow.

Two kinds of conditions remain useful for the implementer to anticipate, but the *shape* (startup refusal vs runtime finding vs both) is a deliberate implementer choice:

- **Authorization coherence**: an authorization that names a checksum whose producer is not in the cache; a knob configuration that contradicts itself; etc.
- **Visible-but-not-required behavior**: remote delegation, unexpected heavy compute, latency anomalies. Surface these so a publisher can notice; refusing at startup is usually too aggressive.

The one property of the tool itself worth committing: it has no code path that writes to the artifact under test. That is a property of the tool's implementation, not a runtime check.

#### Must test

In addition to the cross-pattern checklist:

39. **Clean replay**: a crystallized artifact whose companion bufferdir contains exactly the script's materialization needs produces zero findings; script output equals the equivalent normal-mode run.
40. **Unexpected miss is reported with diff**: introduce a deliberate code change in the replay client script so a leaf `tf_checksum` misses; finding includes the missing `tf_checksum` and a `transformation-diff`-shaped diff against the closest candidate.
41. **Driver may miss, sub-transformation may not**: a driver whose `tf_checksum` is uncached executes; its sub-transformations all hit; replay reports no findings. A driver whose sub-transformation misses produces a finding attributed to the sub-transformation, with driver context.
42. **Nested drivers**: drivers calling drivers to a small depth (e.g., 3); each level executes; the innermost leaves all hit; replay reports no findings.
43. **Unauthorized fingertip is surfaced**: a consumer that would have triggered fingertipping in normal mode, but whose target is not authorized for fingertipping in replay, produces a finding rather than triggering re-execution. The specific authorization shape (a whitelist, an annotation, ...) is an implementer choice; the test asserts the *behavior*: unauthorized → finding, never silent re-execution.
44. **Authorized fingertip succeeds when coherent**: when the publisher has authorized a fingertip and the supporting cache state is coherent (producer cached, producer's inputs materializable through some authorized means), the run completes the fingertip transparently and produces no finding for it.
45. **Authorized materialization with unsatisfied dependency is surfaced**: when an authorization is in place but cannot be honored because a transitive dependency is itself not authorized or unavailable, replay produces a finding naming the unsatisfied dependency, rather than recursively cascading into further re-execution.
46. **Unauthorized materialization is surfaced**: a script that needs a byte for which no authorization applies produces a finding naming the missing checksum and the script context, rather than reaching for a remote source or silently failing.
47. **Iterative convergence**: starting from the most-restrictive setup (no authorizations), the publisher's iteration loop (resolve findings → re-run) terminates with a minimal sufficient authorization spec — whatever shape that spec takes in the chosen implementation.
48. **Irreproducible-only hit**: a `tf_checksum` reachable only via `IrreproducibleTransformation` produces an `Irreproducible-only hit` finding, with row count and `result_checksum` set.
49. **Read-only assertion**: before/after the replay, the `seamless.db` and the artifact's authorized-materialization shape are byte-identical to their pre-run state.
50. **Driver cache bypass by default**: a driver `tf_checksum` whose `result_checksum` is in the cache nevertheless re-runs under default replay; its sub-transformations are submitted and verified.
51. **Driver caching knob**: flipping the driver-caching knob to "enabled" causes a cached driver to short-circuit on its own cache entry without submitting sub-transformations; tests assert the knob's effect is visible in the report (e.g., a count of drivers actually executed vs short-circuited).
52. **Remote delegation observed**: if any remote backend dispatch happens during replay (jobserver, daskserver, remote hashserver fetch), a `Remote delegation observed` finding is produced naming the backend and the dispatched work. Tests do not assert *whether* the run completes — they assert the *visibility* of the remote behavior in the report.
53. **Execution-time visibility**: the report includes wall-clock and per-transformation timing data sufficient for a publisher to notice latency anomalies. The test does not assert a fixed budget; it asserts the data is present.
54. **Multiple findings in one run**: a script with several independent issues produces findings for all of them in one run; the tool does not abort at the first.

## References

Agentic docs (in this monorepo):

- [identity-and-caching.md](seamless/docs/agent/contracts/identity-and-caching.md) — `tf_checksum`, plain vs dunder keys, cache identity.
- [cache-storage-and-limits.md](seamless/docs/agent/contracts/cache-storage-and-limits.md) — scratch, input fingertipping, buffer cache.
- [scratch-witness-audit.md](seamless/docs/agent/contracts/scratch-witness-audit.md) — scratch policy, witness outputs, audit-by-recomputation.
- [execution-records.md](seamless/docs/agent/contracts/execution-records.md) — `MetaData` table, optimistic null, `IrreproducibleTransformation`, database protocol 2.1.
- [content-addressed-files-and-dirs.md](seamless/docs/agent/contracts/content-addressed-files-and-dirs.md) — file/dir content addressing.
- [modules-and-closures.md](seamless/docs/agent/contracts/modules-and-closures.md) — module identities and their role in the cache.
- [compression.md](seamless/docs/agent/contracts/compression.md) — compression is identity-transparent.

Seamless-adoption skill references (under `~/.claude/skills/seamless-adoption/references/`):

- `env-null-hypothesis.md` — the optimistic null, falsification through recomputation, witness comparison.
- `deep-checksums.md` — naming-without-materializing, structured content identity.
- `seamless-primitives.md` — Seamless mental model for porting and refactoring.
