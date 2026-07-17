# Native dependency license inventory

This inventory corresponds to `Cargo.lock` for `0.1.0b1`. Registry source and checksums are in
that lockfile. Release automation must regenerate and audit this table when the lockfile changes.

| Crate | Version | SPDX license expression |
|---|---:|---|
| heck | 0.5.0 | MIT OR Apache-2.0 |
| libc | 0.2.186 | MIT OR Apache-2.0 |
| once_cell | 1.21.4 | MIT OR Apache-2.0 |
| portable-atomic | 1.13.1 | Apache-2.0 OR MIT |
| proc-macro2 | 1.0.106 | MIT OR Apache-2.0 |
| pyo3 | 0.28.3 | MIT OR Apache-2.0 |
| pyo3-build-config | 0.28.3 | MIT OR Apache-2.0 |
| pyo3-ffi | 0.28.3 | MIT OR Apache-2.0 |
| pyo3-macros | 0.28.3 | MIT OR Apache-2.0 |
| pyo3-macros-backend | 0.28.3 | MIT OR Apache-2.0 |
| quote | 1.0.46 | MIT OR Apache-2.0 |
| syn | 2.0.119 | MIT OR Apache-2.0 |
| target-lexicon | 0.13.5 | Apache-2.0 WITH LLVM-exception |
| unicode-ident | 1.0.24 | (MIT OR Apache-2.0) AND Unicode-3.0 |

License texts are supplied by the corresponding crates and are available from the Cargo registry
source used to create the wheel. Binary releases must bundle the applicable texts or an
equivalent generated license artifact before publication.
