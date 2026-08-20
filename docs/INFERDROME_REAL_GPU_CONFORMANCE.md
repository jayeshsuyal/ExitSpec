# Inferdrome real-GPU conformance handoff

Status: producer profile pinned; external archive publication remains owner-gated.

ExitSpec vendors the standalone producer contracts published by Inferdrome
draft PR [#10](https://github.com/jayeshsuyal/inferdrome/pull/10). It does not
import Inferdrome Python modules and does not inherit an Inferdrome acceptance
verdict.

The consumer pins are:

| Artifact | Canonical SHA-256 |
| --- | --- |
| Managed vLLM `0.26.0` profile | `sha256:9d03b5d0822ed829ddbfa4c87c75530885b9ad51ee2c0cb7c5e31a075996fe34` |
| Local GPU proof schema | `sha256:cf83bbdea2bba4c30b8f0e2c5f34f34a4077501207881fdbdab021571d665547` |
| A10 publication review | `sha256:7f1b3be53695e9e3a2009eb28ce008bb2486ae882e52364e26bece770a6d33ff` |
| A10 handoff manifest | `sha256:bc90ac7d0044b32556ce8e78181635f2a2d218e3de7a793062e5dc2b3d6cd4bd` |

These document digests are ordinary SHA-256 over RFC 8785 canonical JSON
bytes. The exact retained archive is separately pinned by raw-byte SHA-256:

```text
sha256:f2408fd0649a7c79f5962872003781ebb9c878b802db27d633cf246f13b6f424
689272 bytes
```

The raw archive is not committed here. Inferdrome classified it
`EXTERNAL_ONLY` pending owner licensing, privacy, and publication approval.
The offline conformance test accepts it only through the
`EXITSPEC_INFERDROME_A10_ARCHIVE` environment variable, checks its exact size
and checksum before parsing, and manually materializes only bounded regular
files and directories into a new private directory. No browser upload or
runtime download is authorized.

At this commit the exact real bundle intentionally still rejects with
`INTERNAL_INCONSISTENCY` because `local_gpu_proof` has not yet been granted
consumer acceptance authority. The next bounded verifier change must validate
that field against the pinned schema and semantic profile before the unchanged
bundle can enter applicability evaluation.
