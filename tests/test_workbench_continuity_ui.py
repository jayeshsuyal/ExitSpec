import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path


STATIC_ROOT = Path(__file__).resolve().parents[1] / "src" / "exitspec" / "static"

DYNAMIC_PAGES = {
    "new_poc.html": {
        "new-poc-main",
        "new-poc-form",
        "identity-panel",
        "display-name",
        "customer-label",
        "use-case",
        "owner",
        "create-poc",
        "created-panel",
        "add-first-source",
        "creation-error",
    },
    "source_intake.html": {
        "source-intake-main",
        "poc-title",
        "poc-context",
        "source-current-task",
        "source-intake-form",
        "source-chooser",
        "source-work",
        "capture-source",
        "capture-result",
        "review-proposals",
        "add-another-source",
        "intake-error",
    },
    "proposal_review.html": {
        "proposal-review-main",
        "poc-title",
        "poc-context",
        "proposal-current-task",
        "proposal-evidence",
        "proposal-decision-form",
        "reviewer",
        "rationale",
        "discard-proposal",
        "keep-proposal",
        "review-complete",
        "define-criteria",
        "proposal-review-error",
    },
    "contract_definition.html": {
        "contract-definition-main",
        "poc-title",
        "poc-context",
        "definition-current-task",
        "definition-evidence",
        "contract-definition-form",
        "metric",
        "threshold",
        "minimum-samples",
        "concurrency",
        "save-definition",
        "definition-complete",
        "prepare-agreement",
        "contract-definition-error",
    },
    "agreement.html": {
        "agreement-main",
        "poc-title",
        "poc-context",
        "agreement-workbench",
        "create-draft-form",
        "create-customer-draft",
        "confirmation-panel",
        "confirmation-form",
        "confirm-agreement",
        "freeze-panel",
        "freeze-form",
        "freeze-contract",
        "agreement-complete",
        "continue-to-proof",
        "agreement-error",
    },
    "proof.html": {
        "performance-main",
        "performance-title",
        "performance-phase",
        "performance-customer",
        "performance-owner",
        "performance-current-task",
        "execution-acknowledged",
        "run-proof",
        "agreement-status",
        "execution-status",
        "evidence-status",
        "requirement-list",
        "evidence-verdict",
        "evidence-pack-link",
        "closure-panel",
        "performance-error",
    },
}

SHARED_CLASSES = {
    "workbench-main",
    "workbench-object-header",
    "workbench-object-identity",
    "workbench-current-task",
    "workbench-primary-slot",
}
SHARED_SINGLETON_CLASSES = SHARED_CLASSES - {"workbench-primary-slot"}


@dataclass
class _Element:
    tag: str
    identifier: str | None
    classes: set[str]
    text_parts: list[str] = field(default_factory=list)
    descendant_ids: set[str] = field(default_factory=set)

    @property
    def text(self) -> str:
        return " ".join(" ".join(self.text_parts).split())


class _MarkupAudit(HTMLParser):
    _VOID_TAGS = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self) -> None:
        super().__init__()
        self.elements: list[_Element] = []
        self.stack: list[_Element] = []
        self.stylesheets: list[str] = []

    @property
    def ids(self) -> set[str]:
        return {
            element.identifier
            for element in self.elements
            if element.identifier is not None
        }

    @property
    def classes(self) -> set[str]:
        return {
            class_name
            for element in self.elements
            for class_name in element.classes
        }

    def with_class(self, class_name: str) -> list[_Element]:
        return [
            element
            for element in self.elements
            if class_name in element.classes
        ]

    def _start(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
        *,
        push: bool,
    ) -> None:
        attributes = dict(attrs)
        identifier = attributes.get("id")
        classes = set((attributes.get("class") or "").split())
        element = _Element(tag, identifier, classes)
        self.elements.append(element)

        if identifier:
            for ancestor in self.stack:
                ancestor.descendant_ids.add(identifier)

        rel = set((attributes.get("rel") or "").split())
        if tag == "link" and "stylesheet" in rel:
            href = attributes.get("href")
            if href:
                self.stylesheets.append(href)

        if push and tag not in self._VOID_TAGS:
            self.stack.append(element)

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self._start(tag, attrs, push=True)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self._start(tag, attrs, push=False)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if not data.strip():
            return
        for ancestor in self.stack:
            ancestor.text_parts.append(data)


def _read(name: str) -> str:
    return (STATIC_ROOT / name).read_text(encoding="utf-8")


def _audit(name: str) -> _MarkupAudit:
    parser = _MarkupAudit()
    parser.feed(_read(name))
    return parser


def _one_with_class(audit: _MarkupAudit, class_name: str) -> _Element:
    matches = audit.with_class(class_name)
    assert len(matches) == 1, class_name
    return matches[0]


def _function(source: str, name: str) -> str:
    marker = f"function {name}("
    start = source.index(marker)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"Function {name} is not closed")


def test_six_dynamic_pages_share_one_workbench_shell_without_losing_ids():
    for page, required_ids in DYNAMIC_PAGES.items():
        audit = _audit(page)

        assert audit.stylesheets[-1] == "/workbench.css", page
        assert SHARED_CLASSES.issubset(audit.classes), page
        assert required_ids.issubset(audit.ids), page
        for class_name in SHARED_SINGLETON_CLASSES:
            assert len(audit.with_class(class_name)) == 1, (page, class_name)
        assert audit.with_class("workbench-primary-slot"), page


def test_new_poc_keeps_object_identity_separate_from_the_current_question():
    audit = _audit("new_poc.html")
    identity = _one_with_class(audit, "workbench-object-identity")
    current_task = _one_with_class(audit, "workbench-current-task")
    primary_slot = _one_with_class(audit, "workbench-primary-slot")

    assert "New POC" in identity.text
    assert "How are the requirements arriving?" not in identity.text
    assert "How are the requirements arriving?" in current_task.text
    assert (
        current_task.identifier == "new-poc-form"
        or "new-poc-form" in current_task.descendant_ids
    )
    assert "create-poc" in primary_slot.descendant_ids
    assert "created-panel" in audit.ids


def test_source_success_remains_inside_the_current_task_with_both_next_links():
    audit = _audit("source_intake.html")
    current_task = _one_with_class(audit, "workbench-current-task")

    assert {
        "source-intake-form",
        "capture-result",
        "review-proposals",
        "add-another-source",
    }.issubset(current_task.descendant_ids)

    javascript = _read("source_intake.js")
    success = _function(javascript, "renderSuccess")
    assert "currentTask.hidden = true" not in success
    assert "resultPanel.hidden = false" in success


def test_trusted_completions_keep_fallbacks_then_replace_to_canonical_routes():
    create_html = _read("new_poc.html")
    create_js = _read("new_poc.js")
    create = _function(create_js, "renderCreated")
    assert 'id="created-panel"' in create_html
    assert "createdPanel.hidden = false" in create
    assert "`/app/pocs/${encodeURIComponent(" in create
    assert ")}/sources/new`" in create
    assert create.index("createdPanel.hidden = false") < create.index(
        "window.location.replace(destination)"
    )
    create_submit = create_js.split(
        'form.addEventListener("submit"', 1
    )[1]
    assert create_submit.index("isTrustedDraftResponse(result)") < (
        create_submit.index("renderCreated(result)")
    )

    cases = (
        (
            "source_intake.html",
            "source_intake.js",
            "renderSuccess",
            "capture-result",
            "`/app/pocs/${encodeURIComponent(pocId)}/review`",
            "resultPanel.hidden = false",
        ),
        (
            "proposal_review.html",
            "proposal_review.js",
            "renderCompletion",
            "review-complete",
            "`/app/pocs/${encodeURIComponent(pocId)}/define`",
            "completionPanel.hidden = false",
        ),
        (
            "contract_definition.html",
            "contract_definition.js",
            "renderCompletion",
            "definition-complete",
            "`/app/pocs/${encodeURIComponent(pocId)}/agreement`",
            "completionPanel.hidden = false",
        ),
        (
            "agreement.html",
            "agreement.js",
            "renderAgreementState",
            "agreement-complete",
            "`/app/pocs/${encodeURIComponent(pocId)}`",
            "showOnly(completionPanel)",
        ),
    )
    for (
        html_name,
        js_name,
        function_name,
        fallback_id,
        route,
        fallback_reveal,
    ) in cases:
        html = _read(html_name)
        javascript = _read(js_name)
        function = _function(javascript, function_name)

        assert f'id="{fallback_id}"' in html, html_name
        assert route in function, js_name
        assert fallback_reveal in function, js_name
        assert "window.location.replace(destination)" in function, js_name
        assert function.index(fallback_reveal) < function.index(
            "window.location.replace(destination)"
        ), js_name


def test_agreement_identity_comes_from_a_strict_poc_projection():
    javascript = _read("agreement.js")
    initialise = _function(javascript, "initialise")

    assert 'const pocApi = pocId ? `/api/pocs/${pocId}` : null;' in javascript
    assert "requestJson(pocApi)" in initialise
    assert "requestJson(agreementApi)" in initialise
    identity_validator = _function(javascript, "isTrustedPOCDraft")
    exact_keys = _function(javascript, "hasExactKeys")
    assert 'typeof payload !== "object"' in exact_keys
    assert "Array.isArray(payload)" in exact_keys
    assert "hasExactKeys(payload, POC_DRAFT_KEYS)" in identity_validator
    assert "payload.poc_id !== pocId" in identity_validator
    for field in ("display_name", "customer_label", "owner"):
        assert f"isSafeBoundedText(payload.{field}, 160)" in identity_validator
    assert 'payload.archive_state !== "ACTIVE"' in identity_validator
    assert initialise.index("isTrustedPOCDraft(draft)") < initialise.index(
        'document.querySelector("#poc-title").textContent'
    )
    assert re.search(r"\.poc_id\s*!==\s*pocId", javascript)
    for field in ("display_name", "customer_label", "owner"):
        assert f".{field}" in initialise
    assert 'document.querySelector("#poc-title").textContent' in initialise
    assert 'document.querySelector("#poc-context").textContent' in initialise
    assert '"Performance agreement"' not in javascript


def test_proof_primary_action_is_the_rightmost_shared_task_slot():
    html = _read("proof.html")
    audit = _audit("proof.html")
    primary_slot = _one_with_class(audit, "workbench-primary-slot")

    assert "run-proof" in primary_slot.descendant_ids
    current_task = html.split('id="performance-current-task"', 1)[1].split(
        "</section>", 1
    )[0]
    assert current_task.index('class="state-boundary"') < current_task.index(
        "workbench-primary-slot"
    )
    assert "workbench-primary-slot" not in current_task.split(
        'class="state-boundary"', 1
    )[0]


def test_workbench_is_bounded_at_1280x720_and_reflows_when_narrow_or_zoomed():
    assert (STATIC_ROOT / "workbench.css").is_file()
    css = _read("workbench.css")
    desktop, responsive = css.split("@media", 1)

    assert ".workbench-main" in desktop
    assert "width: min(100%, 1180px)" in desktop
    assert "min-height: 0" in desktop
    assert "overflow: hidden" in desktop
    assert ".workbench-current-task" in desktop
    assert ".workbench-primary-slot" in desktop

    assert "max-width: 900px" in responsive
    assert "max-height: 680px" in responsive
    assert "overflow: auto" in responsive
    assert re.search(
        r"\.workbench-main\s*\{[^}]*overflow:\s*visible",
        responsive,
        re.DOTALL,
    )
    assert "grid-template-columns: 1fr" in responsive
