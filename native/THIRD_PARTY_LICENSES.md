# Native dependency license inventory

This inventory corresponds to `Cargo.lock` for `0.1.0`. Registry source and checksums are in
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

For binary distribution this project selects the Apache-2.0 alternative wherever a crate offers
`MIT OR Apache-2.0`; the complete Apache-2.0 text is shipped as the project `LICENSE`. The two
additional terms required by the selected expressions—LLVM-exception and Unicode-3.0—are bundled
below. None of the pinned crate roots contains a `NOTICE` file.

The generated native CycloneDX SBOM and machine-readable inventory cross-check this table against
every non-root `Cargo.lock` package; a missing row is a hard error.

## LLVM Exceptions to the Apache 2.0 License

As an exception, if, as a result of your compiling your source code, portions of this Software are
embedded into an Object form of such source code, you may redistribute such embedded portions in
such Object form without complying with the conditions of Sections 4(a), 4(b) and 4(d) of the
License.

In addition, if you combine or link compiled forms of this Software with software that is licensed
under the GPLv2 ("Combined Software") and if a court of competent jurisdiction determines that the
patent provision (Section 3), the indemnity provision (Section 9) or other Section of the License
conflicts with the conditions of the GPLv2, you may retroactively and prospectively choose to deem
waived or otherwise exclude such Section(s) of the License, but only in their entirety and only
with respect to the Combined Software.

## Unicode License v3

Copyright © 1991-2023 Unicode, Inc.

NOTICE TO USER: Carefully read the following legal agreement. BY DOWNLOADING, INSTALLING, COPYING
OR OTHERWISE USING DATA FILES, AND/OR SOFTWARE, YOU UNEQUIVOCALLY ACCEPT, AND AGREE TO BE BOUND BY,
ALL OF THE TERMS AND CONDITIONS OF THIS AGREEMENT. IF YOU DO NOT AGREE, DO NOT DOWNLOAD, INSTALL,
COPY, DISTRIBUTE OR USE THE DATA FILES OR SOFTWARE.

Permission is hereby granted, free of charge, to any person obtaining a copy of data files and any
associated documentation (the "Data Files") or software and any associated documentation (the
"Software") to deal in the Data Files or Software without restriction, including without
limitation the rights to use, copy, modify, merge, publish, distribute, and/or sell copies of the
Data Files or Software, and to permit persons to whom the Data Files or Software are furnished to
do so, provided that either (a) this copyright and permission notice appear with all copies of the
Data Files or Software, or (b) this copyright and permission notice appear in associated
Documentation.

THE DATA FILES AND SOFTWARE ARE PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE
AND NONINFRINGEMENT OF THIRD PARTY RIGHTS.

IN NO EVENT SHALL THE COPYRIGHT HOLDER OR HOLDERS INCLUDED IN THIS NOTICE BE LIABLE FOR ANY CLAIM,
OR ANY SPECIAL INDIRECT OR CONSEQUENTIAL DAMAGES, OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS OF
USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING
OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THE DATA FILES OR SOFTWARE.

Except as contained in this notice, the name of a copyright holder shall not be used in advertising
or otherwise to promote the sale, use or other dealings in these Data Files or Software without
prior written authorization of the copyright holder.
