# Inferdrome A10 retrospective conformance demo

These portable artifacts prove ExitSpec's deterministic consumer behavior for one checksum-pinned Inferdrome bundle. The raw archive is not vendored; its exact local gate remains publication-gated.

The three customer contracts ask the unchanged run three different questions and produce PASS, FAIL, and semantic NOT_PROVEN. Corrupt and synthetic fixtures are ingestion rejections and receive no acceptance verdict or receipt.

Regenerate into a new directory with:

```bash
PYTHONPATH=src python -m exitspec.inferdrome_managed_demo --archive /absolute/path/to/capture.tar.gz --output /absolute/new/output
```
