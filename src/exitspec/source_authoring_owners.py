"""Trusted owner transaction for source-bound drafting; no provider or HTTP entry.

Lock order is source, draft, review, assisted publication, then the caller's
operation lock. The closure reservation surrounds only that short transaction.
The concrete publication token owns prepared pointer replacements, never hooks.
"""

from __future__ import annotations

import hashlib
import re
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime
from threading import get_ident

from .assisted_authoring import (
    ASSISTED_AUTHORING_ADAPTER_NAME,
    ASSISTED_AUTHORING_ADAPTER_VERSION,
    AssistedAuthoringError,
    AssistedDraftResult,
    ProcessLocalAssistedAuthoringService,
    SourceNeutralProposalBatch,
    _materialize_source_neutral_attempt,
    _validate_source_neutral_batch,
)
from .poc_creation import (
    DraftPOCCommitConflict,
    DraftPOCNotFound,
    DraftPOCSnapshot,
    ProcessLocalDraftPOCService,
)
from .poc_proposal_review import (
    ProcessLocalProposalReviewService,
    ProposalReviewDecisionConflict,
)
from .poc_proposal_review import (
    _AuthoringCommitGuard as _ReviewCommitGuard,
)
from .poc_source_intake import (
    POCSourceIntakeInvalid,
    POCSourceIntakeRevisionRequired,
    ProcessLocalPOCSourceIntake,
)
from .poc_sources import POCSourceSnapshot
from .workspace_closure import POCClosureConflict, ProcessLocalPOCClosureService


class SourceAuthoringOwnersError(ValueError):
    """Content-free owner failure. It contains no source or provider response."""

    def __init__(self, code: str = "OWNER_UNAVAILABLE") -> None:
        super().__init__("Source authoring owner transaction was refused.")
        self.code = code


@dataclass(frozen=True, slots=True, repr=False)
class SourceAuthoringSnapshot:
    """Captured owner values, not an egress capability or provenance seal."""

    source: POCSourceSnapshot
    draft: DraftPOCSnapshot
    source_receipt_id: str

    @property
    def poc_id(self) -> str:
        return self.draft.poc_id


class PreparedSourceAuthoringPublication:
    """One thread/context-bound publication, with all fallible work prepared."""

    __slots__ = (
        "_assisted",
        "_attempts",
        "_expected_attempts",
        "_expected_registration",
        "_expected_results",
        "_guard",
        "_registration",
        "_results",
        "_review",
        "_used",
        "result",
    )

    def __init__(
        self,
        guard,
        registration,
        results,
        attempts,
        result,
        *,
        expected_registration,
        expected_results,
        expected_attempts,
    ) -> None:
        self._guard = guard
        self._review = guard._owners._review
        self._assisted = guard._owners._assisted
        self._registration = registration
        self._results = results
        self._attempts = attempts
        self._expected_registration = expected_registration
        self._expected_results = expected_results
        self._expected_attempts = expected_attempts
        self._used = False
        self.result = result

    def publish(self) -> None:
        """Final suffix: concrete pointer swaps only; caller holds operation lock.

        The caller must prepare its own success receipt/maps before invoking
        this method, then replace those pointers immediately while still in the
        same transaction. No callback, allocation, logging or IPC belongs there.
        """

        guard = self._guard
        if (
            not guard._active
            or guard._thread != get_ident()
            or self._used
            or guard._prepared_token is not self
        ):
            raise SourceAuthoringOwnersError("PUBLICATION_UNAVAILABLE")
        # These three stores use copy-on-write publication. A same-thread
        # reentrant action can replace them despite the held RLocks; never erase
        # that action with an older prepared copy. Check all before any swap.
        if (
            self._review._authoring_current_proposals is not self._expected_registration
            or self._assisted._results_by_request is not self._expected_results
            or self._assisted._source_attempts is not self._expected_attempts
        ):
            raise SourceAuthoringOwnersError("PUBLICATION_CONFLICT")
        self._used = True
        # No fallible owner hooks after this first visible swap. Review and A3
        # readers cannot observe these replacements until their locks release.
        self._review._authoring_current_proposals = self._registration
        self._assisted._results_by_request = self._results
        self._assisted._source_attempts = self._attempts


class _OwnerGuard:
    __slots__ = (
        "_active",
        "_draft_owner",
        "_expected_draft",
        "_expected_latest",
        "_expected_source",
        "_identity_key",
        "_owners",
        "_poc_id",
        "_prepared_token",
        "_review_guard",
        "_snapshot",
        "_source_key",
        "_source_owner",
        "_thread",
    )

    def __init__(self, owners, snapshot, review_guard) -> None:
        self._owners = owners
        self._snapshot = snapshot
        self._review_guard = review_guard
        self._active = True
        self._thread = get_ident()
        self._prepared_token = None
        self._source_owner = owners._intake._source_service
        self._draft_owner = owners._drafts
        self._poc_id = snapshot.poc_id
        self._source_key = (snapshot.poc_id, snapshot.source.source_id)
        self._identity_key = (
            snapshot.poc_id,
            snapshot.source.kind,
            snapshot.source.external_id,
        )
        self._expected_source = self._source_owner._by_id[self._source_key]
        self._expected_latest = self._source_owner._identity_latest[self._identity_key]
        self._expected_draft = self._draft_owner._records[self._poc_id]

    def check_current_locked(self) -> None:
        """Final direct identity check after the caller's last clock/failpoint.

        All owner locks are already held. Public mutations from another thread
        cannot enter; a reentrant synthetic callback can, so reread concrete
        owner dictionaries without callbacks, model equality or lock acquisition.
        Call immediately before D or the prepared F suffix, after preparation.
        """

        if not self._active or self._thread != get_ident():
            raise SourceAuthoringOwnersError("PUBLICATION_UNAVAILABLE")
        if (
            self._source_owner._by_id.get(self._source_key) is not self._expected_source
            or self._source_owner._identity_latest.get(self._identity_key)
            is not self._expected_latest
        ):
            raise SourceAuthoringOwnersError("SOURCE_STALE")
        if self._draft_owner._records.get(self._poc_id) is not self._expected_draft:
            raise SourceAuthoringOwnersError("DRAFT_STALE")

    def prepare(
        self,
        batch: SourceNeutralProposalBatch,
        *,
        request_sha256: str,
        provider: str,
        model: str,
        endpoint: str,
        generated_at: datetime,
    ) -> PreparedSourceAuthoringPublication:
        if (
            not self._active
            or self._thread != get_ident()
            or self._prepared_token is not None
        ):
            raise SourceAuthoringOwnersError("PUBLICATION_UNAVAILABLE")
        if type(request_sha256) is not str or not re.fullmatch(
            r"[a-f0-9]{64}", request_sha256
        ):
            raise SourceAuthoringOwnersError("INVALID_RESULT")
        if type(batch) is not SourceNeutralProposalBatch:
            raise SourceAuthoringOwnersError("INVALID_RESULT")
        # Capture originals before validation/materialization/review preparation:
        # a fallible integration boundary can reenter an independent A3 action.
        service = self._owners._assisted
        expected_registration = self._owners._review._authoring_current_proposals
        expected_results = service._results_by_request
        expected_attempts = service._source_attempts
        try:
            # Reparse even an already-typed batch: model_construct/model_copy
            # and freely constructed provider objects are not validation proof.
            batch = SourceNeutralProposalBatch.model_validate(
                batch.model_dump(mode="json")
            )
            _validate_source_neutral_batch(batch, source=self._snapshot.source)
            stored = _materialize_source_neutral_attempt(
                poc_id=self._snapshot.poc_id,
                source_receipt_id=self._snapshot.source_receipt_id,
                source=self._snapshot.source,
                batch=batch,
                request_sha256=request_sha256,
                generated_at=generated_at,
                provider=provider,
                model=model,
                endpoint=endpoint,
                adapter_name=ASSISTED_AUTHORING_ADAPTER_NAME,
                adapter_version=ASSISTED_AUTHORING_ADAPTER_VERSION,
            )
            self._review_guard.prepare(stored.receipt.proposal_ids)
            registration = self._review_guard._authoring_current_proposals
            if type(registration) is not dict:
                raise SourceAuthoringOwnersError("PUBLICATION_UNAVAILABLE")
            results = dict(expected_results)
            results[stored.receipt.authoring_receipt_id] = stored
            attempts = dict(expected_attempts)
            attempts[(self._snapshot.poc_id, self._snapshot.source.source_id)] = stored
            result = AssistedDraftResult(stored.receipt, stored.proposals)
            prepared = PreparedSourceAuthoringPublication(
                self,
                registration,
                results,
                attempts,
                result,
                expected_registration=expected_registration,
                expected_results=expected_results,
                expected_attempts=expected_attempts,
            )
            self._prepared_token = prepared
            return prepared
        except Exception:  # noqa: BLE001 - sanitize every fallible preparation boundary
            raise SourceAuthoringOwnersError("PREPARATION_FAILED") from None


class SourceAuthoringOwners:
    """Real existing owner composition; deterministic no-op guards are refused."""

    def __init__(self, *, source_intake, drafts, review, assisted, run_if_open) -> None:
        if (
            type(source_intake) is not ProcessLocalPOCSourceIntake
            or type(drafts) is not ProcessLocalDraftPOCService
            or type(review) is not ProcessLocalProposalReviewService
            or type(assisted) is not ProcessLocalAssistedAuthoringService
            or type(getattr(run_if_open, "__self__", None))
            is not ProcessLocalPOCClosureService
            or getattr(run_if_open, "__func__", None)
            is not ProcessLocalPOCClosureService.run_if_open
        ):
            raise SourceAuthoringOwnersError()
        self._intake, self._drafts, self._review, self._assisted = (
            source_intake,
            drafts,
            review,
            assisted,
        )
        self._run_if_open = run_if_open
        self._require_bound_owners()

    def _require_bound_owners(self):
        expected = {
            "_source_lookup": self._intake.source_snapshot,
            "_draft_lookup": self._drafts.get,
            "_source_commit_guard": self._intake.authoring_commit_guard,
            "_draft_commit_guard": self._drafts.authoring_commit_guard,
            "_review_commit_guard": self._review.authoring_commit_guard,
            "_decision_lookup": self._review.source_has_decision,
        }
        if any(
            getattr(self._assisted, name) != method for name, method in expected.items()
        ):
            raise SourceAuthoringOwnersError()

    def capture(self, poc_id: str, source_receipt_id: str) -> SourceAuthoringSnapshot:
        try:
            source = self._intake.source_snapshot(poc_id, source_receipt_id)
            draft = self._drafts.get(poc_id)
        except Exception:  # noqa: BLE001 - owner errors must not contain source text
            raise SourceAuthoringOwnersError("SOURCE_UNAVAILABLE") from None
        snapshot = SourceAuthoringSnapshot(source, draft, source_receipt_id)
        return self.run_guarded(snapshot, lambda _guard: snapshot)

    def run_guarded(self, snapshot: SourceAuthoringSnapshot, transaction):
        if (
            type(snapshot) is not SourceAuthoringSnapshot
            or type(snapshot.source) is not POCSourceSnapshot
            or type(snapshot.draft) is not DraftPOCSnapshot
            or snapshot.source.poc_id != snapshot.poc_id
            or not callable(transaction)
        ):
            raise SourceAuthoringOwnersError("INVALID_SNAPSHOT")

        def guarded():
            with ExitStack() as stack:
                try:
                    self._require_bound_owners()
                    source = stack.enter_context(
                        self._intake.authoring_commit_guard(
                            snapshot.poc_id, snapshot.source_receipt_id, snapshot.source
                        )
                    )
                    draft = stack.enter_context(
                        self._drafts.authoring_commit_guard(
                            snapshot.poc_id, snapshot.draft
                        )
                    )
                    review = stack.enter_context(
                        self._review.authoring_commit_guard(
                            snapshot.poc_id, snapshot.source_receipt_id
                        )
                    )
                    if type(review) is not _ReviewCommitGuard:
                        raise SourceAuthoringOwnersError()
                    source.prepare()
                    draft.prepare()
                    # Guard checks exact owner equality; independently recompute
                    # the redacted text digest instead of trusting a caller digest.
                    if (
                        hashlib.sha256(
                            snapshot.source.redacted_text.encode("utf-8")
                        ).hexdigest()
                        != snapshot.source.content_sha256
                    ):
                        raise SourceAuthoringOwnersError("SOURCE_STALE")
                    stack.enter_context(
                        self._assisted.source_authoring_publication_guard(
                            snapshot.poc_id, snapshot.source.source_id
                        )
                    )
                except POCSourceIntakeRevisionRequired:
                    raise SourceAuthoringOwnersError("SOURCE_STALE") from None
                except DraftPOCCommitConflict:
                    raise SourceAuthoringOwnersError("DRAFT_STALE") from None
                except (POCSourceIntakeInvalid, DraftPOCNotFound):
                    raise SourceAuthoringOwnersError("SOURCE_UNAVAILABLE") from None
                except ProposalReviewDecisionConflict:
                    raise SourceAuthoringOwnersError("REVIEW_DECIDED") from None
                except AssistedAuthoringError:
                    raise SourceAuthoringOwnersError(
                        "PUBLICATION_UNAVAILABLE"
                    ) from None
                except SourceAuthoringOwnersError:
                    raise
                except Exception:  # noqa: BLE001 - sanitize owner preparation failures
                    raise SourceAuthoringOwnersError() from None
                guard = _OwnerGuard(self, snapshot, review)
                try:
                    return transaction(guard)
                finally:
                    guard._active = False

        try:
            return self._run_if_open(snapshot.poc_id, guarded)
        except POCClosureConflict:
            raise SourceAuthoringOwnersError("POC_CLOSED") from None
