(() => {
  "use strict";

  const POC_ID_PATTERN = /^poc_[a-z0-9][a-z0-9_-]{2,63}$/;
  const ROUTE_PATTERN =
    /^\/app\/pocs\/(poc_[a-z0-9][a-z0-9_-]{2,63})\/sources\/new$/;
  const RECEIPT_ID_PATTERN = /^srcpt_[a-z0-9][a-z0-9_-]{7,95}$/;
  const SHA256_PATTERN = /^[a-f0-9]{64}$/;
  const MEETING_SESSION_ID_PATTERN = /^meetsess_[a-f0-9]{64}$/;
  const MEETING_SESSION_SCHEMA = "exitspec-meeting-session/1.0";
  const MEETING_SESSION_DISCLOSURE_ID =
    "meeting_synthetic_disclosure_v1";
  const MEETING_SESSION_MODE = "FIXED_SYNTHETIC_MEETING";
  const MEETING_SESSION_PROVIDER = "exitspec.synthetic";
  const MEETING_SESSION_STATE_ACTIONS = Object.freeze({
    SETUP: "RECORD_CONSENT",
    READY: "START_CAPTURE",
    LIVE: "DRAFT_REQUIREMENTS",
    DRAFT_READY: "REVIEW_REQUIREMENTS",
  });
  const MEETING_SESSION_FAILURES = Object.freeze({
    MEETING_SESSION_INVALID_REQUEST:
      "The meeting request was refused safely. Review this step and try again.",
    MEETING_SESSION_DRAFT_UNAVAILABLE:
      "This draft cannot accept a meeting session. Return to the POC workspace.",
    MEETING_SESSION_WRONG_SOURCE_TYPE:
      "This POC did not choose Meeting as its starting source.",
    MEETING_SESSION_CAPACITY_EXCEEDED:
      "The local meeting demo is at capacity. Restart the local demo safely.",
    MEETING_SESSION_NOT_FOUND:
      "No meeting session exists for this POC yet.",
    MEETING_SESSION_IDEMPOTENCY_CONFLICT:
      "This meeting action conflicts with an earlier attempt. Reload the current step.",
    MEETING_SESSION_DISCLOSURE_MISMATCH:
      "The meeting notice changed. Review the current notice before continuing.",
    MEETING_SESSION_CONSENT_REQUIRED:
      "Both synthetic participants must consent before capture starts.",
    MEETING_SESSION_INVALID_TRANSITION:
      "That meeting action is no longer current. ExitSpec will recover the active step.",
    MEETING_SESSION_ADAPTER_FAILED:
      "The synthetic meeting adapter stopped safely. Retry the same step.",
    MEETING_SESSION_FINALIZATION_FAILED:
      "The transcript did not reach human review. Retry the same draft step.",
  });
  const STT_CAPTURE_ID_PATTERN = /^sttcap_[a-f0-9]{64}$/;
  const STT_OPERATION_ID_PATTERN = /^sttop_[a-f0-9]{64}$/;
  const STT_DISCLOSURE_ID = "stt_demo_disclosure_v1";
  const STT_MODE = "FIXED_SYNTHETIC_TRANSCRIPT";
  const STT_LIVE_SCHEMA = "exitspec-stt-browser-fireworks/1.0";
  const STT_LIVE_DISCLOSURE_ID = "stt_fireworks_disclosure_v1";
  const STT_LIVE_MODE = "FIREWORKS_PRERECORDED_TRANSCRIPTION";
  const STT_MEDIA_TYPE = "audio/webm";
  const STT_DURATION_SOURCE = "BROWSER_MONOTONIC_CLOCK_DECLARED";
  const STT_PROVIDER_FAILURES = Object.freeze({
    STT_PROVIDER_CONFIGURATION: Object.freeze({
      userMessage:
        "Fireworks STT is not configured. Audio was cleared; use Paste transcript or ask the demo operator to check the server configuration.",
    }),
    STT_PROVIDER_AUTHENTICATION: Object.freeze({
      userMessage:
        "Fireworks rejected the server credential. Audio was cleared; replace the API key before recording again, or use Paste transcript.",
    }),
    STT_PROVIDER_ACCOUNT_UNAVAILABLE: Object.freeze({
      userMessage:
        "The Fireworks account cannot run STT right now. Audio was cleared; restore account balance before recording again, or use Paste transcript.",
    }),
    STT_PROVIDER_RATE_LIMITED: Object.freeze({
      userMessage:
        "Fireworks rate-limited this request. Audio was cleared; record a new clip later, or use Paste transcript now.",
    }),
    STT_PROVIDER_TIMEOUT: Object.freeze({
      userMessage:
        "Fireworks STT timed out after one attempt. Audio was cleared; check provider status before recording again, or use Paste transcript.",
    }),
    STT_PROVIDER_SERVICE_UNAVAILABLE: Object.freeze({
      userMessage:
        "Fireworks STT is temporarily unavailable. Audio was cleared; record a new clip later, or use Paste transcript now.",
    }),
    STT_PROVIDER_TRANSPORT: Object.freeze({
      userMessage:
        "The Fireworks connection failed safely. Audio was cleared; check connectivity before recording again, or use Paste transcript.",
    }),
    STT_PROVIDER_INVALID_RESPONSE: Object.freeze({
      userMessage:
        "The Fireworks transcript could not be trusted. Audio was cleared; use Paste transcript or review the provider integration.",
    }),
  });
  const SOURCE_KINDS = Object.freeze([
    "EMAIL",
    "MEETING",
    "DOCUMENT",
    "EXISTING_CONTRACT",
  ]);
  const SOURCE_LABELS = Object.freeze({
    EMAIL: "Email",
    MEETING: "Meeting",
    DOCUMENT: "Notes or document",
    EXISTING_CONTRACT: "Existing contract",
  });
  const SOURCE_ENTRY_IDS = Object.freeze({
    EMAIL: "email-entry",
    MEETING: "meeting-entry",
    DOCUMENT: "document-entry",
    EXISTING_CONTRACT: "contract-entry",
  });
  const SOURCE_INPUT_IDS = Object.freeze({
    EMAIL: "email-text",
    MEETING: "meeting-transcript",
    DOCUMENT: "document-text",
    EXISTING_CONTRACT: "contract-json",
  });
  const routeMatch = window.location.pathname.match(ROUTE_PATTERN);
  const pocId =
    routeMatch && POC_ID_PATTERN.test(routeMatch[1]) ? routeMatch[1] : null;
  const pocApi = pocId ? `/api/pocs/${pocId}` : null;
  const sourcesApi = pocApi ? `${pocApi}/sources` : null;
  const sttApi = pocApi ? `${pocApi}/stt` : null;
  const sttDisclosureApi = sttApi ? `${sttApi}/disclosure` : null;
  const sttConsentsApi = sttApi ? `${sttApi}/consents` : null;
  const meetingSessionsApi = pocApi ? `${pocApi}/meeting-sessions` : null;
  const meetingSessionDisclosureApi = meetingSessionsApi
    ? `${meetingSessionsApi}/disclosure`
    : null;
  const meetingSessionCurrentApi = meetingSessionsApi
    ? `${meetingSessionsApi}/current`
    : null;

  const form = document.querySelector("#source-intake-form");
  const chooser = document.querySelector("#source-chooser");
  const currentTask = document.querySelector("#source-current-task");
  const captureButton = document.querySelector("#capture-source");
  const status = document.querySelector("#source-intake-status");
  const errorPanel = document.querySelector("#intake-error");
  const resultPanel = document.querySelector("#capture-result");
  const sourceEmpty = document.querySelector("#source-empty");
  const sourceRadios = Array.from(
    document.querySelectorAll('input[name="source_kind"]')
  );
  const meetingModeChooser = document.querySelector("#meeting-mode-chooser");
  const meetingModeRadios = Array.from(
    document.querySelectorAll('input[name="meeting_mode"]')
  );
  const meetingSessionPanel = document.querySelector(
    "#meeting-session-panel"
  );
  const meetingPastePanel = document.querySelector("#meeting-paste-panel");
  const meetingRecordPanel = document.querySelector("#meeting-record-panel");
  const meetingSessionDisclosureCopy = document.querySelector(
    "#meeting-session-disclosure"
  );
  const meetingSessionStateBadge = document.querySelector(
    "#meeting-session-state-badge"
  );
  const meetingSessionStatus = document.querySelector(
    "#meeting-session-status"
  );
  const meetingSessionConsentPanel = document.querySelector(
    "#meeting-session-consent"
  );
  const meetingSessionNoticeAck = document.querySelector(
    "#meeting-session-notice-ack"
  );
  const meetingSessionParticipantsConsent = document.querySelector(
    "#meeting-session-participants-consent"
  );
  const meetingSessionSyntheticAck = document.querySelector(
    "#meeting-session-synthetic-ack"
  );
  const meetingSessionConsentCheckboxes = [
    meetingSessionNoticeAck,
    meetingSessionParticipantsConsent,
    meetingSessionSyntheticAck,
  ];
  const meetingSessionSteps = Array.from(
    document.querySelectorAll("[data-meeting-step]")
  );
  const recordingNoticeAck = document.querySelector("#recording-notice-ack");
  const allSpeakersConsent = document.querySelector("#all-speakers-consent");
  const syntheticOutputAck = document.querySelector("#synthetic-output-ack");
  const consentCheckboxes = [
    recordingNoticeAck,
    allSpeakersConsent,
    syntheticOutputAck,
  ];
  const startRecordingButton = document.querySelector("#start-recording");
  const stopRecordingButton = document.querySelector("#stop-recording");
  const recordingTimer = document.querySelector("#recording-timer");
  const recordingStatus = document.querySelector("#recording-status");
  const sourceEntries = Object.fromEntries(
    SOURCE_KINDS.map((sourceKind) => [
      sourceKind,
      document.querySelector(`#${SOURCE_ENTRY_IDS[sourceKind]}`),
    ])
  );
  const sourceInputs = Object.fromEntries(
    SOURCE_KINDS.map((sourceKind) => [
      sourceKind,
      document.querySelector(`#${SOURCE_INPUT_IDS[sourceKind]}`),
    ])
  );

  let selectedSource = null;
  let preferredSource = null;
  let inFlight = false;
  let pendingAttempt = null;
  let meetingMode = "SESSION";
  let meetingSessionDisclosure = null;
  let meetingSession = null;
  let meetingSessionUnavailable = false;
  const meetingSessionOperationKeys = {
    CREATE: null,
    CONSENT: null,
    START: null,
    DRAFT: null,
  };
  let sttDisclosure = null;
  let sttUnavailable = false;
  let captureConsent = null;
  let mediaRecorder = null;
  let mediaStream = null;
  let audioChunks = [];
  let recordedByteCount = 0;
  let recordingFailed = false;
  let recordedAudio = null;
  let recordedDurationMs = null;
  let recordingStartedAt = null;
  let recordingInterval = null;
  let recordingTimeout = null;
  let recordingWatchdog = null;

  class SafeRequestError extends Error {
    constructor(statusCode, retrySameAttempt, failureCode = null) {
      super("Source request failed.");
      this.name = "SafeRequestError";
      this.statusCode = statusCode;
      this.retrySameAttempt = retrySameAttempt;
      this.failureCode = failureCode;
    }
  }

  function endpointFor(sourceKind) {
    if (!sourcesApi) {
      return null;
    }
    switch (sourceKind) {
      case "EMAIL":
        return `${sourcesApi}/email-text`;
      case "MEETING":
        return `${sourcesApi}/meeting`;
      case "DOCUMENT":
        return `${sourcesApi}/document`;
      case "EXISTING_CONTRACT":
        return `${sourcesApi}/contract`;
      default:
        return null;
    }
  }

  function sttCaptureEndpoint(captureId) {
    return sttApi && STT_CAPTURE_ID_PATTERN.test(captureId)
      ? `${sttApi}/captures/${captureId}`
      : null;
  }

  function meetingSessionEndpoint(sessionId, action = null) {
    if (
      !meetingSessionsApi ||
      !MEETING_SESSION_ID_PATTERN.test(sessionId) ||
      (action !== null && !["consent", "start", "draft"].includes(action))
    ) {
      return null;
    }
    const sessionPath = `${meetingSessionsApi}/${sessionId}`;
    return action === null ? sessionPath : `${sessionPath}/${action}`;
  }

  function isTrustedApiPath(value) {
    if (
      !pocId ||
      typeof value !== "string" ||
      value.includes("?") ||
      value.includes("#")
    ) {
      return false;
    }
    try {
      const parsed = new URL(value, window.location.origin);
      return (
        parsed.origin === window.location.origin &&
        parsed.pathname === value &&
        parsed.search === "" &&
        parsed.hash === "" &&
        (value === pocApi ||
          value === sourcesApi ||
          value === sttDisclosureApi ||
          value === sttConsentsApi ||
          value === meetingSessionsApi ||
          value === meetingSessionDisclosureApi ||
          value === meetingSessionCurrentApi ||
          (value.startsWith(`${sttApi}/captures/`) &&
            STT_CAPTURE_ID_PATTERN.test(value.split("/").at(-1)) &&
            value === sttCaptureEndpoint(value.split("/").at(-1))) ||
          (value.startsWith(`${meetingSessionsApi}/meetsess_`) &&
            (() => {
              const parts = value.split("/");
              const sessionId = parts[5];
              const action = parts.length === 7 ? parts[6] : null;
              return value === meetingSessionEndpoint(sessionId, action);
            })()) ||
          SOURCE_KINDS.some((sourceKind) => value === endpointFor(sourceKind)))
      );
    } catch {
      return false;
    }
  }

  function hasExactKeys(payload, keys) {
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      return false;
    }
    const actual = Object.keys(payload).sort();
    const expected = [...keys].sort();
    return (
      actual.length === expected.length &&
      actual.every((key, index) => key === expected[index])
    );
  }

  function isSafeBoundedText(value, maximum) {
    return (
      typeof value === "string" &&
      value.trim().length > 0 &&
      value.length <= maximum &&
      !/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(value)
    );
  }

  function trustedSttProviderFailure(payload) {
    return hasExactKeys(payload, ["code", "error", "next_action"]) &&
      Object.prototype.hasOwnProperty.call(STT_PROVIDER_FAILURES, payload.code)
      ? payload.code
      : null;
  }

  function isTrustedDraft(payload) {
    return Boolean(
      payload &&
        typeof payload === "object" &&
        !Array.isArray(payload) &&
        payload.poc_id === pocId &&
        isSafeBoundedText(payload.display_name, 160) &&
        isSafeBoundedText(payload.customer_label, 160) &&
        SOURCE_KINDS.includes(payload.first_source_choice) &&
        payload.archive_state === "ACTIVE"
    );
  }

  function trustedMeetingSessionFailure(payload) {
    return hasExactKeys(payload, ["code", "error", "next_action"]) &&
      Object.prototype.hasOwnProperty.call(
        MEETING_SESSION_FAILURES,
        payload.code
      )
      ? payload.code
      : null;
  }

  function isTrustedMeetingSessionAdapter(payload) {
    return Boolean(
      hasExactKeys(payload, [
        "adapter_id",
        "adapter_version",
        "mode",
        "provider",
        "provider_connected",
        "synthetic_only",
        "transcript_only",
      ]) &&
        payload.provider === MEETING_SESSION_PROVIDER &&
        payload.adapter_id === "exitspec-synthetic-meeting" &&
        payload.adapter_version === "v1" &&
        payload.mode === MEETING_SESSION_MODE &&
        payload.provider_connected === false &&
        payload.transcript_only === true &&
        payload.synthetic_only === true
    );
  }

  function isTrustedMeetingSessionDisclosure(payload) {
    return Boolean(
      hasExactKeys(payload, [
        "adapter",
        "consent_required_before_capture",
        "customer_data_allowed",
        "disclosure_id",
        "fixed_script",
        "may_assign_verdict",
        "may_confirm_contract",
        "may_freeze_contract",
        "may_start_measurement",
        "notice",
        "participant_count",
        "raw_audio_requested",
        "raw_transcript_returned_to_browser",
        "schema_version",
        "synthetic_only",
      ]) &&
        payload.schema_version === MEETING_SESSION_SCHEMA &&
        payload.disclosure_id === MEETING_SESSION_DISCLOSURE_ID &&
        isSafeBoundedText(payload.notice, 1200) &&
        isTrustedMeetingSessionAdapter(payload.adapter) &&
        payload.participant_count === 2 &&
        payload.fixed_script === true &&
        payload.consent_required_before_capture === true &&
        payload.customer_data_allowed === false &&
        payload.raw_audio_requested === false &&
        payload.raw_transcript_returned_to_browser === false &&
        payload.may_confirm_contract === false &&
        payload.may_freeze_contract === false &&
        payload.may_start_measurement === false &&
        payload.may_assign_verdict === false &&
        payload.synthetic_only === true
    );
  }

  function isSafeMeetingTimestamp(value) {
    return (
      typeof value === "string" &&
      value.length <= 40 &&
      Number.isFinite(Date.parse(value))
    );
  }

  function isTrustedMeetingSession(payload) {
    if (
      !hasExactKeys(payload, [
        "adapter",
        "consent_recorded",
        "consented_at",
        "created_at",
        "disclosure_id",
        "draft_created",
        "drafted_at",
        "may_assign_verdict",
        "may_confirm_contract",
        "may_freeze_contract",
        "may_start_measurement",
        "next_action",
        "orchestration_sha256",
        "participant_count",
        "poc_id",
        "proposal_count",
        "raw_audio_received",
        "raw_transcript_returned_to_browser",
        "review_state",
        "review_url",
        "schema_version",
        "session_id",
        "source_receipt_id",
        "started_at",
        "state",
        "synthetic_only",
        "transcript_capture_started",
        "updated_at",
      ]) ||
      payload.schema_version !== MEETING_SESSION_SCHEMA ||
      payload.poc_id !== pocId ||
      !MEETING_SESSION_ID_PATTERN.test(payload.session_id) ||
      !Object.prototype.hasOwnProperty.call(
        MEETING_SESSION_STATE_ACTIONS,
        payload.state
      ) ||
      payload.next_action !== MEETING_SESSION_STATE_ACTIONS[payload.state] ||
      !isTrustedMeetingSessionAdapter(payload.adapter) ||
      payload.disclosure_id !== MEETING_SESSION_DISCLOSURE_ID ||
      payload.participant_count !== 2 ||
      !isSafeMeetingTimestamp(payload.created_at) ||
      !isSafeMeetingTimestamp(payload.updated_at) ||
      Date.parse(payload.updated_at) < Date.parse(payload.created_at) ||
      payload.raw_audio_received !== false ||
      payload.raw_transcript_returned_to_browser !== false ||
      payload.may_confirm_contract !== false ||
      payload.may_freeze_contract !== false ||
      payload.may_start_measurement !== false ||
      payload.may_assign_verdict !== false ||
      payload.synthetic_only !== true
    ) {
      return false;
    }

    const sourceFacts = [
      payload.source_receipt_id,
      payload.proposal_count,
      payload.orchestration_sha256,
      payload.review_url,
      payload.review_state,
    ];
    if (payload.state === "SETUP") {
      return (
        payload.consent_recorded === false &&
        payload.transcript_capture_started === false &&
        payload.draft_created === false &&
        payload.consented_at === null &&
        payload.started_at === null &&
        payload.drafted_at === null &&
        sourceFacts.every((value) => value === null)
      );
    }
    if (payload.state === "READY") {
      return (
        payload.consent_recorded === true &&
        payload.transcript_capture_started === false &&
        payload.draft_created === false &&
        isSafeMeetingTimestamp(payload.consented_at) &&
        payload.started_at === null &&
        payload.drafted_at === null &&
        sourceFacts.every((value) => value === null)
      );
    }
    if (payload.state === "LIVE") {
      return (
        payload.consent_recorded === true &&
        payload.transcript_capture_started === true &&
        payload.draft_created === false &&
        isSafeMeetingTimestamp(payload.consented_at) &&
        isSafeMeetingTimestamp(payload.started_at) &&
        payload.drafted_at === null &&
        sourceFacts.every((value) => value === null)
      );
    }
    return Boolean(
      payload.consent_recorded === true &&
        payload.transcript_capture_started === true &&
        payload.draft_created === true &&
        isSafeMeetingTimestamp(payload.consented_at) &&
        isSafeMeetingTimestamp(payload.started_at) &&
        isSafeMeetingTimestamp(payload.drafted_at) &&
        RECEIPT_ID_PATTERN.test(payload.source_receipt_id) &&
        Number.isSafeInteger(payload.proposal_count) &&
        payload.proposal_count >= 0 &&
        payload.proposal_count <= 64 &&
        SHA256_PATTERN.test(payload.orchestration_sha256) &&
        payload.review_url === `/app/pocs/${pocId}/review` &&
        payload.review_state === "NEEDS_REVIEW"
    );
  }

  function isTrustedMeetingSessionAction(payload) {
    return Boolean(
      hasExactKeys(payload, ["idempotent_replay", "session"]) &&
        typeof payload.idempotent_replay === "boolean" &&
        isTrustedMeetingSession(payload.session)
    );
  }

  function isTrustedSourceList(payload) {
    return Boolean(
      hasExactKeys(payload, ["poc_id", "sources"]) &&
        payload.poc_id === pocId &&
        Array.isArray(payload.sources) &&
        payload.sources.length <= 128 &&
        payload.sources.every(
          (source) =>
            hasExactKeys(source, [
              "idempotent_replay",
              "poc_id",
              "proposal_count",
              "source_kind",
              "source_receipt_id",
              "status",
            ]) &&
            source.poc_id === pocId &&
            SOURCE_KINDS.includes(source.source_kind) &&
            RECEIPT_ID_PATTERN.test(source.source_receipt_id) &&
            Number.isSafeInteger(source.proposal_count) &&
            source.proposal_count >= 0 &&
            source.proposal_count <= 64 &&
            source.status === "NEEDS_REVIEW" &&
            source.idempotent_replay === false
        )
    );
  }

  function isTrustedCaptureResponse(payload, sourceKind) {
    return Boolean(
      hasExactKeys(payload, [
        "idempotent_replay",
        "poc_id",
        "proposal_count",
        "source_kind",
        "source_receipt_id",
        "status",
      ]) &&
        payload.poc_id === pocId &&
        payload.source_kind === sourceKind &&
        RECEIPT_ID_PATTERN.test(payload.source_receipt_id) &&
        Number.isSafeInteger(payload.proposal_count) &&
        payload.proposal_count >= 0 &&
        payload.proposal_count <= 64 &&
        payload.status === "NEEDS_REVIEW" &&
        typeof payload.idempotent_replay === "boolean"
    );
  }

  function isTrustedSttDisclosure(payload) {
    const common = Boolean(
      payload &&
        isSafeBoundedText(payload.notice, 1200) &&
        payload.media_type === STT_MEDIA_TYPE &&
        payload.duration_source === STT_DURATION_SOURCE &&
        payload.webm_signature_required === true &&
        Number.isSafeInteger(payload.min_duration_ms) &&
        payload.min_duration_ms >= 250 &&
        Number.isSafeInteger(payload.max_duration_ms) &&
        payload.max_duration_ms <= 8000 &&
        payload.max_duration_ms > payload.min_duration_ms &&
        Number.isSafeInteger(payload.max_audio_bytes) &&
        payload.max_audio_bytes > 0 &&
        payload.max_audio_bytes <= 65536 &&
        payload.consent_required_before_microphone === true &&
        payload.one_local_operator_only === true &&
        payload.raw_audio_retained === false &&
        payload.raw_transcript_retained === false
    );
    if (!common) {
      return false;
    }
    if (payload.mode === STT_MODE) {
      return Boolean(
        hasExactKeys(payload, [
          "consent_required_before_microphone",
          "disclosure_id",
          "duration_source",
          "fixed_output",
          "max_audio_bytes",
          "max_duration_ms",
          "media_type",
          "min_duration_ms",
          "mode",
          "notice",
          "one_local_operator_only",
          "provider_connected",
          "raw_audio_retained",
          "raw_transcript_retained",
          "schema_version",
          "spoken_words_transcribed",
          "webm_signature_required",
        ]) &&
          payload.schema_version === "exitspec-stt-browser-demo/1.0" &&
          payload.disclosure_id === STT_DISCLOSURE_ID &&
          Array.isArray(payload.fixed_output) &&
          payload.fixed_output.length === 2 &&
          payload.fixed_output.every((item) => isSafeBoundedText(item, 240)) &&
          payload.spoken_words_transcribed === false &&
          payload.provider_connected === false
      );
    }
    return Boolean(
      hasExactKeys(payload, [
        "consent_required_before_microphone",
        "disclosure_id",
        "duration_source",
        "max_audio_bytes",
        "max_duration_ms",
        "media_type",
        "min_duration_ms",
        "mode",
        "notice",
        "one_local_operator_only",
        "provider",
        "provider_model",
        "provider_policy_checked_at",
        "provider_region",
        "provider_retention_mode",
        "provider_transport_configured",
        "raw_audio_retained",
        "raw_transcript_retained",
        "schema_version",
        "spoken_words_transcribed",
        "webm_signature_required",
      ]) &&
        payload.schema_version === STT_LIVE_SCHEMA &&
        payload.disclosure_id === STT_LIVE_DISCLOSURE_ID &&
        payload.mode === STT_LIVE_MODE &&
        payload.provider === "fireworks" &&
        payload.provider_model === "whisper-v3" &&
        payload.provider_region === "us-virginia-1" &&
        payload.provider_policy_checked_at === "2026-08-05" &&
        payload.provider_retention_mode === "ZERO_RETENTION" &&
        payload.spoken_words_transcribed === true &&
        payload.provider_transport_configured === true
    );
  }

  function isTrustedSttConsent(payload) {
    const expiresAt = payload ? Date.parse(payload.expires_at) : Number.NaN;
    const currentTime = Date.now();
    const shared = Boolean(
      payload &&
        payload.poc_id === pocId &&
        STT_CAPTURE_ID_PATTERN.test(payload.capture_id) &&
        payload.state === "READY" &&
        Number.isFinite(expiresAt) &&
        expiresAt > currentTime &&
        expiresAt <= currentTime + 120000 &&
        payload.recording_notice_acknowledged === true &&
        payload.all_speakers_consented === true &&
        payload.microphone_authority_issued === true &&
        payload.audio_egress_authority_issued === false &&
        payload.synthetic_only === true
    );
    if (!shared) {
      return false;
    }
    if (sttDisclosure && sttDisclosure.mode === STT_LIVE_MODE) {
      return Boolean(
        hasExactKeys(payload, [
          "all_speakers_consented",
          "audio_egress_authority_issued",
          "capture_id",
          "disclosure_id",
          "expires_at",
          "microphone_authority_issued",
          "poc_id",
          "provider",
          "provider_model",
          "provider_processing_acknowledged",
          "recording_notice_acknowledged",
          "schema_version",
          "state",
          "synthetic_only",
        ]) &&
          payload.schema_version === STT_LIVE_SCHEMA &&
          payload.disclosure_id === STT_LIVE_DISCLOSURE_ID &&
          payload.provider === "fireworks" &&
          payload.provider_model === "whisper-v3" &&
          payload.provider_processing_acknowledged === true
      );
    }
    return Boolean(
      hasExactKeys(payload, [
        "all_speakers_consented",
        "audio_egress_authority_issued",
        "capture_id",
        "disclosure_id",
        "expires_at",
        "microphone_authority_issued",
        "poc_id",
        "recording_notice_acknowledged",
        "schema_version",
        "state",
        "synthetic_demo_acknowledged",
        "synthetic_only",
      ]) &&
        payload.schema_version === "exitspec-stt-browser-demo/1.0" &&
        payload.disclosure_id === STT_DISCLOSURE_ID &&
        payload.synthetic_demo_acknowledged === true
    );
  }

  function isTrustedSttCaptureResponse(payload, captureId) {
    const shared = Boolean(
      payload &&
        payload.capture_id === captureId &&
        STT_OPERATION_ID_PATTERN.test(payload.operation_id) &&
        payload.poc_id === pocId &&
        payload.source_kind === "MEETING" &&
        RECEIPT_ID_PATTERN.test(payload.source_receipt_id) &&
        Number.isSafeInteger(payload.proposal_count) &&
        payload.proposal_count >= 0 &&
        payload.proposal_count <= 64 &&
        payload.status === "NEEDS_REVIEW" &&
        payload.duration_source === STT_DURATION_SOURCE &&
        payload.webm_signature_verified === true &&
        typeof payload.idempotent_replay === "boolean" &&
        payload.raw_audio_retained === false &&
        payload.raw_transcript_retained === false
    );
    if (!shared) {
      return false;
    }
    if (sttDisclosure && sttDisclosure.mode === STT_LIVE_MODE) {
      return Boolean(
        hasExactKeys(payload, [
          "capture_id",
          "duration_source",
          "idempotent_replay",
          "mode",
          "operation_id",
          "poc_id",
          "proposal_count",
          "provider",
          "provider_connected",
          "provider_model",
          "provider_region",
          "provider_retention_mode",
          "raw_audio_retained",
          "raw_transcript_retained",
          "schema_version",
          "source_kind",
          "source_receipt_id",
          "spoken_words_transcribed",
          "status",
          "webm_signature_verified",
        ]) &&
          payload.schema_version === STT_LIVE_SCHEMA &&
          payload.mode === STT_LIVE_MODE &&
          payload.spoken_words_transcribed === true &&
          payload.provider_connected === true &&
          payload.provider === "fireworks" &&
          payload.provider_model === "whisper-v3" &&
          payload.provider_region === "us-virginia-1" &&
          payload.provider_retention_mode === "ZERO_RETENTION"
      );
    }
    return Boolean(
      hasExactKeys(payload, [
        "capture_id",
        "duration_source",
        "idempotent_replay",
        "mode",
        "operation_id",
        "poc_id",
        "proposal_count",
        "provider_connected",
        "raw_audio_retained",
        "raw_transcript_retained",
        "schema_version",
        "source_kind",
        "source_receipt_id",
        "spoken_words_transcribed",
        "status",
        "webm_signature_verified",
      ]) &&
        payload.schema_version === "exitspec-stt-browser-demo/1.0" &&
        payload.mode === STT_MODE &&
        payload.spoken_words_transcribed === false &&
        payload.provider_connected === false
    );
  }

  async function requestJson(path, options = {}) {
    if (!isTrustedApiPath(path)) {
      throw new SafeRequestError(null, true);
    }
    let response;
    try {
      response = await fetch(path, {
        ...options,
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
          ...(options.headers || {}),
        },
      });
    } catch {
      throw new SafeRequestError(null, true);
    }

    let responseUrl;
    try {
      responseUrl = new URL(response.url || path, window.location.origin);
    } catch {
      throw new SafeRequestError(response.status, true);
    }
    if (
      responseUrl.origin !== window.location.origin ||
      responseUrl.pathname !== path ||
      responseUrl.search ||
      responseUrl.hash
    ) {
      throw new SafeRequestError(response.status, true);
    }
    if (!response.ok) {
      const retrySameAttempt =
        response.status >= 500 ||
        response.status === 408 ||
        response.status === 429;
      const failurePayload = await response.json().catch(() => null);
      const failureCode =
        trustedSttProviderFailure(failurePayload) ||
        trustedMeetingSessionFailure(failurePayload);
      throw new SafeRequestError(
        response.status,
        failureCode ? false : retrySameAttempt,
        failureCode
      );
    }
    const payload = await response.json().catch(() => null);
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new SafeRequestError(response.status, true);
    }
    return payload;
  }

  function newIdempotencyKey() {
    if (
      window.crypto &&
      typeof window.crypto.randomUUID === "function"
    ) {
      return `source-${window.crypto.randomUUID()}`;
    }
    const bytes = new Uint8Array(16);
    window.crypto.getRandomValues(bytes);
    const suffix = Array.from(bytes, (value) =>
      value.toString(16).padStart(2, "0")
    ).join("");
    return `source-${suffix}`;
  }

  function newScopedIdempotencyKey(scope) {
    const sourceKey = newIdempotencyKey();
    return `${scope}-${sourceKey.slice("source-".length)}`;
  }

  function meetingOperationKey(action) {
    if (!Object.prototype.hasOwnProperty.call(meetingSessionOperationKeys, action)) {
      return null;
    }
    if (!meetingSessionOperationKeys[action]) {
      meetingSessionOperationKeys[action] = newScopedIdempotencyKey(
        `meeting-${action.toLowerCase()}`
      );
    }
    return meetingSessionOperationKeys[action];
  }

  function allMeetingSessionAcknowledgementsChecked() {
    return meetingSessionConsentCheckboxes.every(
      (checkbox) => checkbox.checked
    );
  }

  function supportsSyntheticRecording() {
    return Boolean(
      navigator.mediaDevices &&
        typeof navigator.mediaDevices.getUserMedia === "function" &&
        typeof window.MediaRecorder === "function" &&
        typeof window.MediaRecorder.isTypeSupported === "function" &&
        window.MediaRecorder.isTypeSupported(STT_MEDIA_TYPE) &&
        window.crypto &&
        window.crypto.subtle &&
        typeof window.crypto.subtle.digest === "function"
    );
  }

  function allRecordingAcknowledgementsChecked() {
    return consentCheckboxes.every((checkbox) => checkbox.checked);
  }

  function isRecording() {
    return Boolean(mediaRecorder && mediaRecorder.state === "recording");
  }

  function selectedValue(sourceKind) {
    const input = sourceInputs[sourceKind];
    if (!input || input.disabled) {
      return null;
    }
    if (sourceKind === "EXISTING_CONTRACT") {
      const rawValue = input.value;
      if (!isSafeBoundedText(rawValue, 40000)) {
        return null;
      }
      try {
        const parsed = JSON.parse(rawValue);
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
          return null;
        }
      } catch {
        return null;
      }
      return rawValue;
    }
    const maximum = 20000;
    return isSafeBoundedText(input.value, maximum) ? input.value : null;
  }

  function buildSourcePayload(sourceKind, value, idempotencyKey) {
    switch (sourceKind) {
      case "EMAIL":
        return { email_text: value, idempotency_key: idempotencyKey };
      case "MEETING":
        return { transcript_text: value, idempotency_key: idempotencyKey };
      case "DOCUMENT":
        return { document_text: value, idempotency_key: idempotencyKey };
      case "EXISTING_CONTRACT":
        return { contract_json: value, idempotency_key: idempotencyKey };
      default:
        return null;
    }
  }

  function clearError() {
    errorPanel.hidden = true;
    errorPanel.textContent = "";
  }

  function clearRecordingTimers() {
    if (recordingInterval !== null) {
      window.clearInterval(recordingInterval);
      recordingInterval = null;
    }
    if (recordingTimeout !== null) {
      window.clearTimeout(recordingTimeout);
      recordingTimeout = null;
    }
    if (recordingWatchdog !== null) {
      window.clearTimeout(recordingWatchdog);
      recordingWatchdog = null;
    }
  }

  function stopMediaStream() {
    if (mediaStream) {
      mediaStream.getTracks().forEach((track) => track.stop());
      mediaStream = null;
    }
  }

  function discardRecordedAudio() {
    recordedAudio = null;
    recordedDurationMs = null;
    captureConsent = null;
    audioChunks = [];
    recordedByteCount = 0;
  }

  function recordingElapsedMs() {
    return recordingStartedAt === null
      ? 0
      : Math.max(0, Math.round(window.performance.now() - recordingStartedAt));
  }

  function renderMeetingSessionControls() {
    const active = selectedSource === "MEETING" && meetingMode === "SESSION";
    const state = meetingSession ? meetingSession.state : "SETUP";
    const stepByState = {
      SETUP: 0,
      READY: 1,
      LIVE: 2,
      DRAFT_READY: 3,
    };
    const currentStep = stepByState[state];
    meetingSessionSteps.forEach((step, index) => {
      const stepState =
        index < currentStep
          ? "complete"
          : index === currentStep
            ? "current"
            : "upcoming";
      step.dataset.state = stepState;
      if (stepState === "current") {
        step.setAttribute("aria-current", "step");
      } else {
        step.removeAttribute("aria-current");
      }
    });

    const consentStep = state === "SETUP";
    meetingSessionConsentPanel.hidden = !consentStep;
    meetingSessionConsentCheckboxes.forEach((checkbox) => {
      checkbox.disabled =
        !active || inFlight || meetingSessionUnavailable || !consentStep;
    });

    meetingSessionStateBadge.dataset.state = "idle";
    meetingSessionStatus.dataset.state = "idle";
    if (meetingSessionUnavailable) {
      meetingSessionStateBadge.textContent = "Unavailable";
      meetingSessionStatus.dataset.state = "blocked";
      meetingSessionStatus.textContent =
        "The synthetic meeting boundary could not be validated. Use Paste transcript.";
    } else if (state === "SETUP") {
      meetingSessionStateBadge.textContent = "Not connected";
      meetingSessionStatus.textContent = allMeetingSessionAcknowledgementsChecked()
        ? "Ready. The next action records consent before any synthetic capture."
        : "Review all three acknowledgements to record consent.";
    } else if (state === "READY") {
      meetingSessionStateBadge.dataset.state = "ready";
      meetingSessionStateBadge.textContent = "Consent recorded";
      meetingSessionStatus.dataset.state = "ready";
      meetingSessionStatus.textContent =
        "Consent is bound. Start the fixed synthetic transcript capture.";
    } else if (state === "LIVE") {
      meetingSessionStateBadge.dataset.state = "live";
      meetingSessionStateBadge.textContent = "Synthetic running";
      meetingSessionStatus.dataset.state = "live";
      meetingSessionStatus.textContent =
        "The synthetic transcript is ready. Draft its measurable requirements now.";
    } else {
      meetingSessionStateBadge.dataset.state = "draft";
      meetingSessionStateBadge.textContent = "Draft ready";
      meetingSessionStatus.dataset.state = "draft";
      meetingSessionStatus.textContent =
        "The source is redacted and waiting for human review.";
    }

    if (!active) {
      return;
    }
    captureButton.hidden = false;
    captureButton.disabled = inFlight || meetingSessionUnavailable;
    if (state === "SETUP") {
      captureButton.disabled =
        captureButton.disabled ||
        !meetingSessionDisclosure ||
        !allMeetingSessionAcknowledgementsChecked();
      captureButton.textContent = inFlight ? "Recording consent…" : "Record consent";
      status.textContent = inFlight
        ? "Creating the bounded session and recording exact consent…"
        : "Consent is required before synthetic capture.";
    } else if (state === "READY") {
      captureButton.textContent = inFlight
        ? "Starting…"
        : "Start synthetic capture";
      status.textContent = inFlight
        ? "Starting the server-owned synthetic adapter…"
        : "The next action starts a fixed synthetic transcript, not Zoom.";
    } else if (state === "LIVE") {
      captureButton.textContent = inFlight
        ? "Drafting…"
        : "Draft requirements now";
      status.textContent = inFlight
        ? "Sealing, redacting, and creating review-only proposals…"
        : "Draft now sends the fixed transcript to human review.";
    } else {
      captureButton.textContent = "Review proposals";
      status.textContent = "The meeting source is ready for human review.";
    }
  }

  async function loadMeetingSession() {
    try {
      const disclosure = await requestJson(meetingSessionDisclosureApi);
      if (!isTrustedMeetingSessionDisclosure(disclosure)) {
        throw new SafeRequestError(200, true);
      }
      meetingSessionDisclosure = disclosure;
      meetingSessionDisclosureCopy.textContent = disclosure.notice;
      try {
        const current = await requestJson(meetingSessionCurrentApi);
        if (!isTrustedMeetingSession(current)) {
          throw new SafeRequestError(200, true);
        }
        meetingSession = current;
      } catch (error) {
        if (
          error instanceof SafeRequestError &&
          error.failureCode === "MEETING_SESSION_NOT_FOUND"
        ) {
          meetingSession = null;
        } else {
          throw error;
        }
      }
    } catch {
      meetingSessionDisclosure = null;
      meetingSession = null;
      meetingSessionUnavailable = true;
      meetingSessionDisclosureCopy.textContent =
        "The synthetic meeting boundary could not be validated. Paste a transcript instead.";
    }
    renderSelectedSource();
  }

  async function recoverMeetingSession() {
    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        const current = await requestJson(meetingSessionCurrentApi);
        return isTrustedMeetingSession(current) ? current : null;
      } catch (error) {
        if (
          error instanceof SafeRequestError &&
          error.failureCode === "MEETING_SESSION_NOT_FOUND"
        ) {
          return null;
        }
        if (attempt === 0) {
          await new Promise((resolve) => window.setTimeout(resolve, 250));
        }
      }
    }
    return null;
  }

  async function runMeetingSessionAction(
    action,
    endpoint,
    body,
    expectedState
  ) {
    if (!endpoint || !isTrustedApiPath(endpoint)) {
      throw new SafeRequestError(null, false);
    }
    const response = await requestJson(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (
      !isTrustedMeetingSessionAction(response) ||
      response.session.state !== expectedState
    ) {
      throw new SafeRequestError(200, true);
    }
    meetingSession = response.session;
    return action;
  }

  function safeMeetingSessionFailureCopy(error) {
    if (
      error instanceof SafeRequestError &&
      error.failureCode &&
      Object.prototype.hasOwnProperty.call(
        MEETING_SESSION_FAILURES,
        error.failureCode
      )
    ) {
      return MEETING_SESSION_FAILURES[error.failureCode];
    }
    return "The meeting step did not return a trusted result. ExitSpec recovered the latest safe state when possible.";
  }

  async function advanceMeetingSession() {
    if (
      selectedSource !== "MEETING" ||
      meetingMode !== "SESSION" ||
      inFlight ||
      meetingSessionUnavailable ||
      !meetingSessionDisclosure
    ) {
      return;
    }
    const startingState = meetingSession ? meetingSession.state : "SETUP";
    if (
      startingState === "SETUP" &&
      !allMeetingSessionAcknowledgementsChecked()
    ) {
      return;
    }
    if (startingState === "DRAFT_READY") {
      renderSuccess({ proposal_count: meetingSession.proposal_count });
      return;
    }

    inFlight = true;
    clearError();
    renderSelectedSource();
    try {
      if (!meetingSession) {
        await runMeetingSessionAction(
          "CREATE",
          meetingSessionsApi,
          { idempotency_key: meetingOperationKey("CREATE") },
          "SETUP"
        );
      }
      if (meetingSession.state === "SETUP") {
        await runMeetingSessionAction(
          "CONSENT",
          meetingSessionEndpoint(meetingSession.session_id, "consent"),
          {
            all_participants_consented: true,
            disclosure_id: meetingSessionDisclosure.disclosure_id,
            idempotency_key: meetingOperationKey("CONSENT"),
            recording_notice_acknowledged: true,
            synthetic_demo_acknowledged: true,
          },
          "READY"
        );
      } else if (meetingSession.state === "READY") {
        await runMeetingSessionAction(
          "START",
          meetingSessionEndpoint(meetingSession.session_id, "start"),
          { idempotency_key: meetingOperationKey("START") },
          "LIVE"
        );
      } else if (meetingSession.state === "LIVE") {
        await runMeetingSessionAction(
          "DRAFT",
          meetingSessionEndpoint(meetingSession.session_id, "draft"),
          { idempotency_key: meetingOperationKey("DRAFT") },
          "DRAFT_READY"
        );
      }

      if (meetingSession.state === "DRAFT_READY") {
        renderSuccess({ proposal_count: meetingSession.proposal_count });
      }
    } catch (error) {
      const recovered = await recoverMeetingSession();
      if (recovered) {
        meetingSession = recovered;
        if (meetingSession.state === "DRAFT_READY") {
          renderSuccess({ proposal_count: meetingSession.proposal_count });
          return;
        }
      }
      errorPanel.textContent = safeMeetingSessionFailureCopy(error);
      errorPanel.hidden = false;
    } finally {
      inFlight = false;
      if (!form.hidden) {
        renderSelectedSource();
      }
    }
  }

  function renderRecordingControls() {
    const active = selectedSource === "MEETING" && meetingMode === "RECORD";
    const recording = isRecording();
    const elapsed = recordingElapsedMs();
    const maximum = sttDisclosure ? sttDisclosure.max_duration_ms : 8000;
    const minimum = sttDisclosure ? sttDisclosure.min_duration_ms : 250;
    const locked = inFlight || Boolean(pendingAttempt);
    const sessionLocked = Boolean(meetingSession);

    meetingModeChooser.disabled =
      selectedSource !== "MEETING" || locked || recording || sessionLocked;
    meetingModeRadios.forEach((radio) => {
      radio.disabled =
        meetingModeChooser.disabled ||
        (radio.value === "RECORD" && (!sttDisclosure || sttUnavailable)) ||
        (radio.value === "SESSION" &&
          (!meetingSessionDisclosure || meetingSessionUnavailable));
    });
    consentCheckboxes.forEach((checkbox) => {
      checkbox.disabled =
        !active || locked || recording || Boolean(recordedAudio);
    });
    startRecordingButton.disabled =
      !active ||
      locked ||
      recording ||
      Boolean(recordedAudio) ||
      !sttDisclosure ||
      sttUnavailable ||
      !supportsSyntheticRecording() ||
      !allRecordingAcknowledgementsChecked();
    stopRecordingButton.disabled = !recording || elapsed < minimum;
    recordingTimer.textContent = `${(Math.min(elapsed, maximum) / 1000).toFixed(1)}s / ${(maximum / 1000).toFixed(1)}s`;

    if (!active) {
      return;
    }
    if (sttUnavailable || !supportsSyntheticRecording()) {
      recordingStatus.dataset.state = "blocked";
      recordingStatus.textContent =
        "Browser recording is unavailable here. Use Paste transcript.";
    } else if (recording) {
      recordingStatus.dataset.state = "recording";
      recordingStatus.textContent =
        "Recording locally. Stop when ready; eight seconds is the hard limit.";
    } else if (recordedAudio) {
      recordingStatus.dataset.state = "ready";
      recordingStatus.textContent =
        sttDisclosure && sttDisclosure.mode === STT_LIVE_MODE
          ? "Clip ready. The next action sends it once to Fireworks, redacts the transcript, and creates review proposals."
          : "Clip ready. Create review proposals to run the fixed synthetic handoff.";
    } else if (inFlight) {
      recordingStatus.dataset.state = "pending";
      recordingStatus.textContent = "Recording consent before microphone access…";
    } else if (!allRecordingAcknowledgementsChecked()) {
      recordingStatus.dataset.state = "idle";
      recordingStatus.textContent =
        "Review all three acknowledgements to enable the microphone.";
    } else {
      recordingStatus.dataset.state = "ready";
      recordingStatus.textContent =
        "Ready. Microphone permission is requested only after consent is recorded.";
    }
  }

  function renderMeetingMode() {
    meetingSessionPanel.hidden = meetingMode !== "SESSION";
    meetingPastePanel.hidden = meetingMode !== "PASTE";
    meetingRecordPanel.hidden = meetingMode !== "RECORD";
    renderRecordingControls();
    renderMeetingSessionControls();
  }

  async function loadSttDisclosure() {
    try {
      const disclosure = await requestJson(sttDisclosureApi);
      if (!isTrustedSttDisclosure(disclosure)) {
        throw new SafeRequestError(200, true);
      }
      sttDisclosure = disclosure;
      document.querySelector("#stt-disclosure").textContent = disclosure.notice;
      const liveProvider = disclosure.mode === STT_LIVE_MODE;
      document.querySelector("#record-adapter-label").textContent = liveProvider
        ? "Provider-backed transcription"
        : "Local synthetic adapter";
      document.querySelector("#meeting-record-mode-label").textContent = liveProvider
        ? "Record with Fireworks STT"
        : "Record synthetic demo";
      document.querySelector("#meeting-source-option-copy").textContent = liveProvider
        ? "Paste or transcribe one short synthetic clip, or run a session"
        : "Run a session, paste, or use the local mic demo";
      document.querySelector("#record-demo-heading").textContent = liveProvider
        ? "Transcribe one short synthetic clip"
        : "Record one short demo clip";
      document.querySelector("#stt-mode-badge").textContent = liveProvider
        ? "Fireworks STT · experimental"
        : "Not real STT";
      document.querySelector("#stt-fixed-output-panel").hidden = liveProvider;
      document.querySelector("#processing-scope-ack-copy").textContent = liveProvider
        ? "I understand this synthetic clip will be sent once to Fireworks for transcription."
        : "I understand my spoken words will not be transcribed.";
      document.querySelector("#stt-proof-copy").textContent = liveProvider
        ? "Consent is recorded before microphone permission; the exact clip is byte-bound, sent once to the pinned Fireworks endpoint, never persisted by ExitSpec, and the redacted transcript remains NEEDS_REVIEW."
        : "Consent is recorded before microphone permission; audio is byte-bound, processed once on loopback, never persisted, and cleared after the attempt. No provider, Zoom, or Google Meet connection is implied.";
      if (!liveProvider) {
        const outputItems = Array.from(
          document.querySelectorAll("#stt-fixed-output li")
        );
        disclosure.fixed_output.forEach((item, index) => {
          outputItems[index].textContent = item;
        });
      }
    } catch {
      sttDisclosure = null;
      sttUnavailable = true;
      document.querySelector("#stt-disclosure").textContent =
        "The recording boundary could not be validated. Paste a transcript instead.";
    }
    renderSelectedSource();
  }

  function finishRecording() {
    const durationMs = recordingElapsedMs();
    const chunks = audioChunks;
    const failed = recordingFailed;
    clearRecordingTimers();
    stopMediaStream();
    mediaRecorder = null;
    recordingStartedAt = null;
    audioChunks = [];
    recordedByteCount = 0;
    recordingFailed = false;

    const clip = new Blob(chunks, { type: STT_MEDIA_TYPE });
    const valid = Boolean(
      sttDisclosure &&
        !failed &&
        durationMs >= sttDisclosure.min_duration_ms &&
        durationMs <= sttDisclosure.max_duration_ms + 250 &&
        clip.size > 0 &&
        clip.size <= sttDisclosure.max_audio_bytes
    );
    if (!valid) {
      discardRecordedAudio();
      errorPanel.textContent =
        "The clip was empty, too short, or too large. Start a new recording.";
      errorPanel.hidden = false;
    } else {
      recordedAudio = clip;
      recordedDurationMs = Math.min(durationMs, sttDisclosure.max_duration_ms);
    }
    renderSelectedSource();
  }

  function stopRecording() {
    if (!isRecording()) {
      return;
    }
    const recorder = mediaRecorder;
    clearRecordingTimers();
    recordingWatchdog = window.setTimeout(() => {
      if (mediaRecorder !== recorder) {
        return;
      }
      recorder.ondataavailable = null;
      recorder.onerror = null;
      recorder.onstop = null;
      mediaRecorder = null;
      recordingStartedAt = null;
      recordingFailed = false;
      discardRecordedAudio();
      errorPanel.textContent =
        "Browser recording did not finish safely. Microphone access was stopped; start a new clip or paste a transcript.";
      errorPanel.hidden = false;
      renderSelectedSource();
    }, 750);
    try {
      recorder.stop();
    } catch {
      recordingFailed = true;
    } finally {
      stopMediaStream();
    }
  }

  async function beginRecording() {
    if (
      selectedSource !== "MEETING" ||
      meetingMode !== "RECORD" ||
      inFlight ||
      isRecording() ||
      !sttDisclosure ||
      !allRecordingAcknowledgementsChecked() ||
      !supportsSyntheticRecording()
    ) {
      return;
    }

    inFlight = true;
    clearError();
    renderSelectedSource();
    try {
      const consent = await requestJson(sttConsentsApi, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          all_speakers_consented: true,
          disclosure_id: sttDisclosure.disclosure_id,
          idempotency_key: newScopedIdempotencyKey("stt-consent"),
          recording_notice_acknowledged: true,
          ...(sttDisclosure.mode === STT_LIVE_MODE
            ? { provider_processing_acknowledged: true }
            : { synthetic_demo_acknowledged: true }),
        }),
      });
      if (!isTrustedSttConsent(consent)) {
        throw new SafeRequestError(200, false);
      }

      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
        },
        video: false,
      });
      mediaStream = stream;
      const recorder = new MediaRecorder(stream, {
        audioBitsPerSecond: 16000,
        mimeType: STT_MEDIA_TYPE,
      });
      captureConsent = consent;
      mediaRecorder = recorder;
      audioChunks = [];
      recordedByteCount = 0;
      recordingFailed = false;
      recorder.ondataavailable = (event) => {
        if (event.data instanceof Blob && event.data.size > 0) {
          if (
            !sttDisclosure ||
            recordedByteCount + event.data.size > sttDisclosure.max_audio_bytes
          ) {
            recordingFailed = true;
            if (recorder.state === "recording") {
              stopRecording();
            }
            return;
          }
          recordedByteCount += event.data.size;
          audioChunks.push(event.data);
        }
      };
      recorder.onerror = () => {
        recordingFailed = true;
        errorPanel.textContent =
          "Browser recording failed. No audio was sent; start a new clip or paste a transcript.";
        errorPanel.hidden = false;
        if (recorder.state === "recording") {
          stopRecording();
        }
      };
      recorder.onstop = finishRecording;
      recorder.start(200);
      recordingStartedAt = window.performance.now();
      recordingInterval = window.setInterval(() => {
        renderRecordingControls();
      }, 100);
      recordingTimeout = window.setTimeout(
        stopRecording,
        sttDisclosure.max_duration_ms
      );
    } catch (error) {
      clearRecordingTimers();
      stopMediaStream();
      mediaRecorder = null;
      recordingStartedAt = null;
      recordingFailed = false;
      discardRecordedAudio();
      errorPanel.textContent =
        error instanceof SafeRequestError
          ? "Consent could not be recorded safely. The microphone was not enabled."
          : "Microphone access failed. No audio was sent; use Paste transcript or try again.";
      errorPanel.hidden = false;
    } finally {
      inFlight = false;
      renderSelectedSource();
    }
  }

  function bytesToBase64(bytes) {
    let binary = "";
    const chunkSize = 8192;
    for (let offset = 0; offset < bytes.length; offset += chunkSize) {
      binary += String.fromCharCode(
        ...bytes.subarray(offset, Math.min(offset + chunkSize, bytes.length))
      );
    }
    return window.btoa(binary);
  }

  async function sha256Hex(buffer) {
    const digest = await window.crypto.subtle.digest("SHA-256", buffer);
    return Array.from(new Uint8Array(digest), (value) =>
      value.toString(16).padStart(2, "0")
    ).join("");
  }

  async function recoverSttCapture(endpoint, captureId) {
    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        const response = await requestJson(endpoint);
        return isTrustedSttCaptureResponse(response, captureId)
          ? response
          : null;
      } catch {
        if (attempt === 0) {
          await new Promise((resolve) => window.setTimeout(resolve, 250));
        }
      }
    }
    return null;
  }

  async function submitRecordedDemo() {
    if (
      !recordedAudio ||
      !recordedDurationMs ||
      !captureConsent ||
      !sttDisclosure
    ) {
      return;
    }
    const captureId = captureConsent.capture_id;
    const endpoint = sttCaptureEndpoint(captureId);
    if (!endpoint || !isTrustedApiPath(endpoint)) {
      blockIntake("The recording route is invalid. No audio was sent.");
      return;
    }

    inFlight = true;
    clearError();
    renderSelectedSource();
    let audioBuffer = null;
    let audioBytes = null;
    let requestBody = null;
    try {
      audioBuffer = await recordedAudio.arrayBuffer();
      audioBytes = new Uint8Array(audioBuffer);
      requestBody = JSON.stringify({
        audio_base64: bytesToBase64(audioBytes),
        audio_sha256: await sha256Hex(audioBuffer),
        byte_length: audioBytes.byteLength,
        duration_ms: recordedDurationMs,
        idempotency_key: newScopedIdempotencyKey("stt-capture"),
        media_type: STT_MEDIA_TYPE,
      });
      discardRecordedAudio();
      const response = await requestJson(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: requestBody,
      });
      if (!isTrustedSttCaptureResponse(response, captureId)) {
        throw new SafeRequestError(200, false);
      }
      renderSuccess(response);
    } catch (error) {
      discardRecordedAudio();
      const recovered =
        error instanceof SafeRequestError && error.failureCode
          ? null
          : await recoverSttCapture(endpoint, captureId);
      if (recovered) {
        renderSuccess(recovered);
        return;
      }
      errorPanel.textContent = safeSttFailureCopy(error);
      errorPanel.hidden = false;
    } finally {
      audioBytes = null;
      audioBuffer = null;
      requestBody = null;
      inFlight = false;
      if (!form.hidden) {
        renderSelectedSource();
      }
    }
  }

  function safeSttFailureCopy(error) {
    if (
      error instanceof SafeRequestError &&
      error.failureCode &&
      Object.prototype.hasOwnProperty.call(
        STT_PROVIDER_FAILURES,
        error.failureCode
      )
    ) {
      return STT_PROVIDER_FAILURES[error.failureCode].userMessage;
    }
    return "The recording attempt did not produce a trusted handoff. Audio was cleared; record a new clip or use Paste transcript.";
  }

  function safeFailureCopy(error) {
    if (!(error instanceof SafeRequestError)) {
      return "The source could not be captured safely. Retry the same attempt.";
    }
    if (error.statusCode === 400) {
      if (selectedSource === "MEETING") {
        return "The transcript was not accepted. Use Speaker: message lines, or paste one natural single-speaker text block.";
      }
      return "The source was not accepted. Review the selected input and try again.";
    }
    if (error.statusCode === 404) {
      return "This draft or source route is unavailable. No source was captured.";
    }
    if (
      error.statusCode === 403 ||
      error.statusCode === 409 ||
      error.statusCode === 415
    ) {
      return "The source request was refused safely. Review the draft and try again.";
    }
    if (error.statusCode === 413 || error.statusCode === 422) {
      if (selectedSource === "MEETING") {
        return "The transcript was not accepted. Check the 20,000-character limit and use Speaker: message lines or one natural text block.";
      }
      return "The selected source was not accepted. Review it and start a new capture attempt.";
    }
    return "The response was interrupted or could not be trusted. Retry uses the same source key.";
  }

  function setControlsDisabled(disabled) {
    const sessionLocked = Boolean(meetingSession);
    chooser.disabled = disabled;
    if (sessionLocked) {
      chooser.disabled = true;
    }
    sourceRadios.forEach((radio) => {
      radio.disabled = disabled || sessionLocked;
    });
    Object.values(sourceInputs).forEach((input) => {
      input.disabled =
        disabled ||
        !selectedSource ||
        input.id !== SOURCE_INPUT_IDS[selectedSource] ||
        (selectedSource === "MEETING" && meetingMode !== "PASTE");
    });
  }

  function renderSelectedSource() {
    const sessionPath =
      selectedSource === "MEETING" && meetingMode === "SESSION";
    const recordingPath =
      selectedSource === "MEETING" && meetingMode === "RECORD";
    const recording = isRecording();
    Object.entries(sourceEntries).forEach(([sourceKind, entry]) => {
      const active = sourceKind === selectedSource;
      entry.hidden = !active;
      entry.setAttribute("aria-hidden", String(!active));
    });
    sourceEmpty.hidden = Boolean(selectedSource);
    document.querySelector("#source-work-heading").textContent = selectedSource
      ? SOURCE_LABELS[selectedSource]
      : "Choose a source";
    document.querySelector("#preferred-source-copy").textContent =
      selectedSource && selectedSource === preferredSource
        ? "Original starting choice"
        : selectedSource
          ? "Alternate source"
          : "No source selected";
    setControlsDisabled(inFlight || Boolean(pendingAttempt) || recording);
    renderMeetingMode();

    if (sessionPath) {
      renderMeetingSessionControls();
      return;
    }

    if (recordingPath) {
      captureButton.hidden = !recordedAudio;
      captureButton.disabled =
        inFlight || recording || !recordedAudio || !captureConsent;
      captureButton.textContent = inFlight
        ? recordedAudio
          ? "Creating proposals…"
          : "Preparing microphone…"
        : recordedAudio
          ? "Create review proposals"
          : "Record first";
      status.textContent = recording
        ? "Recording the consented browser clip…"
        : recordedAudio
          ? sttDisclosure && sttDisclosure.mode === STT_LIVE_MODE
            ? "Clip ready. The next action performs one Fireworks transcription and creates review-only proposals."
            : "Clip ready. The next action creates review-only proposals."
          : "Record one short clip after reviewing the disclosure.";
      return;
    }

    captureButton.hidden = false;
    captureButton.disabled =
      !selectedSource ||
      inFlight ||
      (!pendingAttempt && selectedValue(selectedSource) === null);
    captureButton.textContent = pendingAttempt
      ? "Retry capture"
      : inFlight
        ? "Capturing…"
        : "Capture source";
    status.textContent = pendingAttempt
      ? "The response was interrupted. Retry will use the same source key."
      : inFlight
        ? "Capturing the source for human review…"
        : selectedSource
          ? "Check the source, then capture it."
          : "Choose one source to continue.";
  }

  function clearSensitiveInputs() {
    clearRecordingTimers();
    stopMediaStream();
    discardRecordedAudio();
    document.querySelector("#email-text").value = "";
    document.querySelector("#meeting-transcript").value = "";
    document.querySelector("#document-text").value = "";
    document.querySelector("#contract-json").value = "";
    consentCheckboxes.forEach((checkbox) => {
      checkbox.checked = false;
    });
    meetingSessionConsentCheckboxes.forEach((checkbox) => {
      checkbox.checked = false;
    });
  }

  function renderSuccess(payload) {
    const destination = `/app/pocs/${encodeURIComponent(pocId)}/review`;
    clearSensitiveInputs();
    pendingAttempt = null;
    form.hidden = true;
    currentTask.hidden = false;
    resultPanel.hidden = false;
    document.querySelector("#current-task-heading").textContent =
      "Review the extracted proposals";
    document.querySelector("#task-guidance").textContent =
      "The source is captured. Human review decides which claims may become acceptance criteria.";
    document.querySelector("#proposal-count").textContent =
      `${payload.proposal_count} ${payload.proposal_count === 1 ? "proposal" : "proposals"}`;
    document.querySelector("#review-state").textContent = "NEEDS_REVIEW";
    const reviewProposals = document.querySelector("#review-proposals");
    reviewProposals.setAttribute("href", destination);
    reviewProposals.hidden = false;
    const addAnotherSource = document.querySelector("#add-another-source");
    addAnotherSource.setAttribute(
      "href",
      `/app/pocs/${pocId}/sources/new`
    );
    addAnotherSource.hidden = false;
    resultPanel.focus();
    try {
      window.location.replace(destination);
    } catch {
      // The verified fallback panel remains available if navigation is blocked.
    }
  }

  function applyDraft(draft, sourceList) {
    preferredSource = draft.first_source_choice;
    selectedSource = preferredSource;
    const hasExistingSources = sourceList.sources.length > 0;
    document.querySelector("#poc-title").textContent = draft.display_name;
    document.querySelector("#poc-context").textContent =
      `${draft.customer_label} · local draft`;
    document.querySelector("#current-task-heading").textContent =
      hasExistingSources ? "Add one customer source" : "Capture one customer source";
    document.querySelector("#task-guidance").textContent = hasExistingSources
      ? "New text creates fresh proposals. For an agreement revision, include the complete replacement TTFT + error-rate plan; prior criteria are not silently carried forward."
      : "The original starting choice is selected first. You may choose another source without changing the POC type.";
    document.querySelector("#existing-source-count").textContent =
      `${sourceList.sources.length} existing ${sourceList.sources.length === 1 ? "source" : "sources"}`;

    sourceRadios.forEach((radio) => {
      radio.checked = radio.value === preferredSource;
      const option = radio.closest("[data-source-option]");
      const marker = option.querySelector(".starting-choice");
      option.dataset.preferred = String(radio.value === preferredSource);
      marker.hidden = radio.value !== preferredSource;
    });

    currentTask.setAttribute("aria-busy", "false");
    renderSelectedSource();
  }

  function blockIntake(message) {
    clearRecordingTimers();
    if (isRecording()) {
      mediaRecorder.stop();
    }
    stopMediaStream();
    discardRecordedAudio();
    setControlsDisabled(true);
    captureButton.disabled = true;
    currentTask.setAttribute("aria-busy", "false");
    status.textContent = "Source intake is unavailable.";
    errorPanel.textContent = message;
    errorPanel.hidden = false;
  }

  sourceRadios.forEach((radio) => {
    radio.addEventListener("change", () => {
      if (!radio.checked || inFlight || pendingAttempt) {
        return;
      }
      if (selectedSource === "MEETING" && meetingMode === "RECORD") {
        discardRecordedAudio();
      }
      selectedSource = radio.value;
      clearError();
      renderSelectedSource();
      if (!sourceInputs[selectedSource].disabled) {
        sourceInputs[selectedSource].focus();
      } else if (selectedSource === "MEETING" && meetingMode === "SESSION") {
        meetingSessionNoticeAck.focus();
      } else {
        document.querySelector("#record-demo-heading").focus?.();
      }
    });
  });

  meetingModeRadios.forEach((radio) => {
    radio.addEventListener("change", () => {
      if (!radio.checked || inFlight || pendingAttempt || isRecording()) {
        return;
      }
      discardRecordedAudio();
      meetingMode = radio.value;
      clearError();
      renderSelectedSource();
      if (meetingMode === "PASTE") {
        sourceInputs.MEETING.focus();
      } else if (meetingMode === "SESSION") {
        meetingSessionNoticeAck.focus();
      } else {
        recordingNoticeAck.focus();
      }
    });
  });

  consentCheckboxes.forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      if (!inFlight && !isRecording() && !recordedAudio) {
        clearError();
        renderSelectedSource();
      }
    });
  });

  meetingSessionConsentCheckboxes.forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      if (!inFlight && meetingMode === "SESSION") {
        clearError();
        renderSelectedSource();
      }
    });
  });

  startRecordingButton.addEventListener("click", beginRecording);
  stopRecordingButton.addEventListener("click", stopRecording);

  Object.values(sourceInputs).forEach((input) => {
    input.addEventListener("input", () => {
      if (!inFlight && !pendingAttempt) {
        clearError();
        renderSelectedSource();
      }
    });
    input.addEventListener("change", () => {
      if (!inFlight && !pendingAttempt) {
        clearError();
        renderSelectedSource();
      }
    });
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (inFlight || !selectedSource) {
      return;
    }

    if (selectedSource === "MEETING" && meetingMode === "SESSION") {
      await advanceMeetingSession();
      return;
    }

    if (selectedSource === "MEETING" && meetingMode === "RECORD") {
      await submitRecordedDemo();
      return;
    }

    if (!pendingAttempt) {
      const value = selectedValue(selectedSource);
      if (value === null || !form.reportValidity()) {
        status.textContent =
          selectedSource === "EXISTING_CONTRACT"
            ? "Enter one valid JSON object before capture."
            : "Complete the selected source before capture.";
        return;
      }
      const idempotencyKey = newIdempotencyKey();
      const payload = buildSourcePayload(
        selectedSource,
        value,
        idempotencyKey
      );
      const endpoint = endpointFor(selectedSource);
      if (!payload || !isTrustedApiPath(endpoint)) {
        blockIntake("The source route is invalid. No source was captured.");
        return;
      }
      pendingAttempt = {
        endpoint,
        idempotencyKey,
        payload,
        sourceKind: selectedSource,
      };
    }

    inFlight = true;
    clearError();
    renderSelectedSource();

    try {
      const response = await requestJson(pendingAttempt.endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(pendingAttempt.payload),
      });
      if (!isTrustedCaptureResponse(response, pendingAttempt.sourceKind)) {
        throw new SafeRequestError(200, true);
      }
      renderSuccess(response);
    } catch (error) {
      if (
        error instanceof SafeRequestError &&
        !error.retrySameAttempt
      ) {
        pendingAttempt = null;
      }
      errorPanel.textContent = safeFailureCopy(error);
      errorPanel.hidden = false;
    } finally {
      inFlight = false;
      if (!form.hidden) {
        renderSelectedSource();
      }
    }
  });

  async function initialise() {
    if (
      !pocId ||
      !pocApi ||
      !sourcesApi ||
      !sttApi ||
      !meetingSessionsApi ||
      !meetingSessionDisclosureApi ||
      !meetingSessionCurrentApi
    ) {
      blockIntake(
        "This source-intake address is invalid. Return to the POC workspace."
      );
      return;
    }
    try {
      const [draft, sourceList] = await Promise.all([
        requestJson(pocApi),
        requestJson(sourcesApi),
      ]);
      if (!isTrustedDraft(draft) || !isTrustedSourceList(sourceList)) {
        throw new SafeRequestError(200, true);
      }
      applyDraft(draft, sourceList);
      if (preferredSource === "MEETING") {
        await Promise.all([loadSttDisclosure(), loadMeetingSession()]);
      } else {
        meetingSessionUnavailable = true;
        meetingSessionDisclosureCopy.textContent =
          "Guided sessions require Meeting as the POC starting source. Paste a transcript instead.";
        await loadSttDisclosure();
        renderSelectedSource();
      }
    } catch {
      blockIntake(
        "The draft could not be validated. No source request is available."
      );
    }
  }

  window.addEventListener("pagehide", () => {
    clearRecordingTimers();
    if (isRecording()) {
      mediaRecorder.ondataavailable = null;
      mediaRecorder.onstop = null;
      mediaRecorder.stop();
    }
    stopMediaStream();
    discardRecordedAudio();
  });

  initialise();
})();
