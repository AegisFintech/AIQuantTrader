# Rust Performance Boundary

Rust `1.96.0` is pinned for future measured hot paths. This directory has no
Cargo manifest, lockfile, crate, or application code in Phase 2.

[Cargo workspaces contain one or more packages](https://doc.rust-lang.org/stable/cargo/reference/workspaces.html).
Creating an empty manifest or a no-op crate would provide false validation and
violate the repository's evidence-first performance policy. The first Rust PR
must include:

- a stable Python/Rust interface;
- a representative benchmark and Python baseline;
- an accepted latency or throughput target;
- deterministic correctness tests;
- an owner and fallback path.

That PR creates the workspace manifest and lockfile with the real crate.
