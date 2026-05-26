# why-not

`seamless-share why-not` is a read-only diagnostic for Seamless transformation
cache misses. It always reports lookup state for the queried endpoint set. When
the transformation is absent, it can compare the input transformation dict with
a near-enough candidate using the same diff primitive exposed as
`seamless-share transformation-diff`.

V1 supports JSON transformation dict files and local SQLite database endpoints
with adjacent bufferdir-style transformation dict buffers keyed by
`tf_checksum`. Remote and named endpoints are intentionally rejected until their
read-only transformation-dict and buffer APIs are available here.
