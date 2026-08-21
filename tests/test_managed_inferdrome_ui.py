import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path


STATIC_ROOT = Path(__file__).resolve().parents[1] / "src" / "exitspec" / "static"
AGREEMENT_HTML = (STATIC_ROOT / "agreement.html").read_text(encoding="utf-8")
AGREEMENT_JS = (STATIC_ROOT / "agreement.js").read_text(encoding="utf-8")
REVIEW_JS = (STATIC_ROOT / "review.js").read_text(encoding="utf-8")
PROOF_HTML = (STATIC_ROOT / "proof.html").read_text(encoding="utf-8")
PROOF_JS = (STATIC_ROOT / "proof.js").read_text(encoding="utf-8")

MANAGED_CATALOG_KEYS = {
    "configured",
    "profiles",
    "rejected_count",
}
MANAGED_PROFILE_KEYS = {
    "adapter",
    "bundle_digest",
    "chronology",
    "claims_assurance",
    "display_name",
    "endpoint",
    "endpoint_class",
    "gpu_models",
    "measured_requests",
    "metric_definition_id",
    "model",
    "observed_configured_max_concurrency",
    "privacy",
    "producer",
    "profile_id",
    "reducer_id",
    "run_id",
    "target_provider",
    "warmup_requests",
}
MANAGED_IMPORT_EXTENSION_KEYS = {
    "anomalous_count",
    "observed_configured_max_concurrency",
    "required_configured_max_concurrency",
}


class _MarkupInventory(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: dict[str, tuple[str, dict[str, str | None]]] = {}
        self.labels_for: set[str] = set()
        self.controls: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        identifier = attributes.get("id")
        if identifier:
            self.ids[str(identifier)] = (tag, attributes)
        if tag == "label" and attributes.get("for"):
            self.labels_for.add(str(attributes["for"]))
        if tag in {"input", "select", "textarea", "button"}:
            self.controls.append((tag, attributes))


def _frozen_string_arrays(source: str) -> dict[str, set[str]]:
    arrays: dict[str, set[str]] = {}
    pattern = re.compile(
        r"\bconst\s+([A-Z][A-Z0-9_]*)\s*=\s*"
        r"Object\.freeze\(\s*\[(.*?)\]\s*\);",
        re.DOTALL,
    )
    for match in pattern.finditer(source):
        arrays[match.group(1)] = set(
            re.findall(r"[\"']([a-z][a-z0-9_]*)[\"']", match.group(2))
        )
    return arrays


def _constant_with_keys(source: str, expected: set[str]) -> str:
    for name, keys in _frozen_string_arrays(source).items():
        if keys == expected:
            return name
    raise AssertionError(f"No frozen exact-key list found for {sorted(expected)}")


def _function(source: str, name: str) -> str:
    start = source.index(f"function {name}")
    following = source.find("\n  function ", start + len(name) + 9)
    return source[start:] if following < 0 else source[start:following]


def _assert_distinct_ingestion_and_acceptance_copy(source: str) -> None:
    normalized = source.lower().replace("_", " ")
    assert "ingestion rejected" in normalized
    assert "not proven" in normalized
    assert "no acceptance verdict" in normalized or "no verdict" in normalized
    assert "insufficient evidence" in normalized


def test_agreement_has_one_labeled_pathless_managed_profile_selector():
    inventory = _MarkupInventory()
    inventory.feed(AGREEMENT_HTML)

    selector = inventory.ids.get("managed-evidence-profile-select")
    assert selector is not None
    assert selector[0] == "select"
    assert "managed-evidence-profile-select" in inventory.labels_for

    summary_ids = {
        "managed-evidence-profile",
        "managed-evidence-profile-heading",
        "managed-profile-status",
        "managed-profile-model",
        "managed-profile-hardware",
        "managed-profile-workload",
        "managed-profile-semantics",
    }
    assert summary_ids.issubset(inventory.ids)
    managed_ids = {
        identifier
        for identifier in inventory.ids
        if identifier.startswith("managed-evidence-profile")
        or identifier.startswith("managed-profile-")
    }
    assert len(managed_ids) <= 12

    for tag, attributes in inventory.controls:
        assert not (tag == "input" and attributes.get("type") == "file")
        identity = " ".join(
            str(attributes.get(key, "")) for key in ("id", "name")
        ).lower()
        assert "bundle-path" not in identity
        assert "archive-path" not in identity
        assert "file-path" not in identity

    normalized = AGREEMENT_HTML.lower().replace("_", "-")
    assert "inferdrome-intake" not in normalized
    assert "managed-evidence-intake" not in normalized
    assert "intake-source" not in normalized


def test_agreement_fetches_and_exactly_validates_pathless_profiles():
    assert re.search(
        r"`\$\{agreementApi\}/managed-evidence`",
        AGREEMENT_JS,
    )
    assert re.search(
        r"(?:requestJson|fetch)\(\s*managedEvidenceApi\b",
        AGREEMENT_JS,
    )
    assert "bundle_path" not in AGREEMENT_JS
    assert "archive_path" not in AGREEMENT_JS
    assert "file_path" not in AGREEMENT_JS

    catalog_keys = _constant_with_keys(AGREEMENT_JS, MANAGED_CATALOG_KEYS)
    profile_keys = _constant_with_keys(AGREEMENT_JS, MANAGED_PROFILE_KEYS)
    for constant in (catalog_keys, profile_keys):
        assert re.search(
            rf"hasExactKeys\([^;]{{0,240}}\b{re.escape(constant)}\b",
            AGREEMENT_JS,
            re.DOTALL,
        )


def test_agreement_adds_managed_identity_only_to_managed_preparation():
    managed_assignment = re.search(
        r"INFERDROME_EXTERNAL_BUNDLE[\s\S]{0,2200}"
        r"(?:\.inferdrome_run_id\s*=|\binferdrome_run_id\s*:)"
        r"[\s\S]{0,600}"
        r"(?:\.inferdrome_bundle_digest\s*=|"
        r"\binferdrome_bundle_digest\s*:)",
        AGREEMENT_JS,
    )
    assert managed_assignment is not None

    reference = _function(AGREEMENT_JS, "useReferenceTarget")
    for value in (
        "ExitSpec local reference",
        "OpenAI-compatible deterministic reference",
        "exitspec/reference-stream-v1",
        "/api/reference/inference/v1/chat/completions",
    ):
        assert value in AGREEMENT_JS
    assert 'input.value === "EXIT_SPEC_STREAMING_PROBE"' in reference
    assert "inferdrome_run_id" not in reference
    assert "inferdrome_bundle_digest" not in reference


def test_agreement_explains_rejection_separately_from_insufficient_evidence():
    _assert_distinct_ingestion_and_acceptance_copy(AGREEMENT_JS)


def test_customer_review_supports_the_exact_managed_metric_identity():
    assert "inference_performance_v3" in REVIEW_JS
    assert ".evidence_identity" in REVIEW_JS
    assert ".adapter_id" in REVIEW_JS
    assert ".adapter_version" in REVIEW_JS
    assert "vllm_first_choices_event_v0_26" in REVIEW_JS
    assert "nearest_rank_v1" in REVIEW_JS
    assert re.search(r"first[- ]choices[- ]event", REVIEW_JS, re.IGNORECASE)
    assert re.search(r"role[- ]only", REVIEW_JS, re.IGNORECASE)
    assert re.search(r"empty[- ]content", REVIEW_JS, re.IGNORECASE)
    assert "RETROSPECTIVE" in REVIEW_JS
    assert "NOT_AVAILABLE" in REVIEW_JS
    _assert_distinct_ingestion_and_acceptance_copy(REVIEW_JS)


def test_managed_proof_uses_the_frozen_selection_and_blocks_switching():
    assert "inference_performance_v3" in PROOF_JS
    assert "agreement.draft.inferdrome_run_id" in PROOF_JS
    assert "agreement.draft.inferdrome_bundle_digest" in PROOF_JS
    assert re.search(
        r"inferdromeBundle\.disabled\s*=\s*true",
        PROOF_JS,
    )

    change_handler = PROOF_JS.split(
        'inferdromeBundle.addEventListener("change"', 1
    )[1].split('runButton.addEventListener("click"', 1)[0]
    assert re.search(r"managed", change_handler, re.IGNORECASE)
    assert "return" in change_handler

    assert "function trustedCatalog" in PROOF_JS
    assert "selectedCatalogEntry" in PROOF_JS
    assert "requestJson(inferdromeCatalogApi)" in PROOF_JS
    assert "run_id: selectedBundle.run_id" in PROOF_JS
    assert "bundle_digest: selectedBundle.bundle_digest" in PROOF_JS
    assert "bundle_path" not in PROOF_JS


def test_managed_proof_accepts_v2_receipts_and_conditional_result_fields():
    assert "irc2_" in PROOF_JS
    assert "irc_" in PROOF_JS
    extension_keys = _constant_with_keys(
        PROOF_JS,
        MANAGED_IMPORT_EXTENSION_KEYS,
    )
    assert re.search(
        rf"hasExactKeys\([^;]{{0,320}}\b{re.escape(extension_keys)}\b",
        PROOF_JS,
        re.DOTALL,
    )
    for key in MANAGED_IMPORT_EXTENSION_KEYS:
        assert key in PROOF_JS


def test_proof_exposes_one_simple_managed_result_summary():
    inventory = _MarkupInventory()
    inventory.feed(PROOF_HTML)
    expected_id_terms = {
        "summary": ("result", "summary"),
        "p95 TTFT": ("p95", "ttft"),
        "error rate": ("error", "rate"),
        "records": ("records",),
        "required concurrency": ("required", "concurrency"),
        "observed concurrency": ("observed", "concurrency"),
    }
    matched: dict[str, str] = {}
    for label, terms in expected_id_terms.items():
        candidates = [
            identifier
            for identifier in inventory.ids
            if all(term in identifier.lower() for term in terms)
        ]
        assert candidates, f"Missing simple {label} result ID"
        matched[label] = candidates[0]
    for label, identifier in matched.items():
        if label != "summary":
            assert (
                f'"#{identifier}"' in PROOF_JS
                or f"'#{identifier}'" in PROOF_JS
            )


def test_managed_ui_javascript_parses():
    for name in ("agreement.js", "review.js", "proof.js"):
        completed = subprocess.run(
            ["node", "--check", str(STATIC_ROOT / name)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, (name, completed.stderr)
