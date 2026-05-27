# seamless-share replay

`seamless-share replay` runs a Python client script against a crystallized
`seamless.db` plus companion bufferdir under replay-mode environment variables.
The harness is read-only with respect to the artifact and writes findings only
to stdout or an explicit report path.

Default behavior uses an isolated synthesized config, bypasses driver cache
entries, and treats the supplied bufferdir as the only materialization source.
Runtime hooks emit JSON Lines events to the harness; the harness turns those
events into deterministic JSON reports.
