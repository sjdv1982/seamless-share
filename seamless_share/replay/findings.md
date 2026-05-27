# replay findings

Finding IDs are the first 16 hex characters of a SHA-256 digest over the
finding kind and that kind's required fields. Volatile `context` data is not
part of the ID.

- `unexpected_miss`: non-driver cache lookup missed.
- `unauthorized_materialization`: bytes were requested outside authorized
  sources.
- `unauthorized_fingertip`: input fingertipping was requested without explicit
  authorization.
- `authorized_materialization_unsatisfied_dependency`: an authorized fingertip
  could not satisfy a transitive dependency.
- `remote_delegation_observed`: replay used or attempted a remote backend.
- `unexpected_heavy_compute`: transformation execution cost was suspicious.
- `irreproducible_only_hit`: only irreproducible cache rows were available.
- `authorization_incoherent`: startup authorization could not be honored as
  written.
