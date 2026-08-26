import crypto from "node:crypto";
import { spawnSync } from "node:child_process";
import http from "node:http";
import process from "node:process";

import express from "express";
import WebSocket from "ws";

import {
  assertCredentialRotationGate,
  assertFreshCaptureDirectory,
  assertPreparedCaptureWorkspace,
  computeEndpointValidationResponse,
  createArtifactRecorder,
  parseBoundedJsonObject,
  safePublicConfigurationSnapshot,
  sha256Hex,
  signOAuthState,
  verifyOAuthState,
  verifyZoomWebhookSignature,
} from "./operator-capture-lib.mjs";

const OFFICIAL_SAMPLE_COMMIT = "5c39fca2ed97d75bcbdb318cf246a037835f7d37";
const CLIENT_ID = required("ZOOM_CLIENT_ID", 8, 512);
const CLIENT_SECRET = required("ZOOM_CLIENT_SECRET", 16, 1024);
const SECRET_TOKEN = required("ZOOM_SECRET_TOKEN", 16, 1024);
const CAPTURE_ID = required("CAPTURE_ID", 4, 104);
const RAW_DIRECTORY = required("CAPTURE_RAW_DIR", 8, 4096);
const PUBLIC_BASE_URL = normalizedPublicBaseUrl(required("PUBLIC_BASE_URL", 8, 2048));
const WEBHOOK_PATH = safePublicPath(required("WEBHOOK_PATH", 24, 160), "/zoom-webhook/");
const OAUTH_CALLBACK_PATH = safePublicPath(required("OAUTH_CALLBACK_PATH", 24, 160), "/zoom-oauth/");
const PUBLIC_PORT = boundedInteger(process.env.PUBLIC_PORT ?? "3000", 1024, 65535);
const OPERATOR_PORT = boundedInteger(process.env.OPERATOR_PORT ?? "3001", 1024, 65535);
const CHAOS_AFTER_TRANSCRIPT_SECONDS = boundedInteger(
  process.env.CHAOS_FORCE_DISCONNECT_MEDIA_AFTER_FIRST_TRANSCRIPT_SEC ?? "3",
  1,
  30,
);
const CAPTURE_WINDOW_SECONDS = boundedInteger(
  process.env.CAPTURE_WINDOW_SECONDS ?? "900",
  60,
  1800,
);
const NETWORK_AUTHORIZED = process.env.ALLOW_REAL_ZOOM_NETWORK === "true";
const CREDITS_CONFIRMED = process.env.RTMS_CREDITS_CONFIRMED === "true";
const SYNTHETIC_ONLY_CONFIRMED = process.env.SYNTHETIC_CAPTURE_AUTHORIZED === "true";
const CREDENTIAL_ROTATION_ATTESTED = process.env.ZOOM_CREDENTIAL_ROTATION_ATTESTED === "true";
const CREDENTIAL_ROTATION_RECEIPT_ID = process.env.ZOOM_CREDENTIAL_ROTATION_RECEIPT_ID ?? "";
const OAUTH_REDIRECT_URI = `${PUBLIC_BASE_URL}${OAUTH_CALLBACK_PATH}`;
const WEBHOOK_URL = `${PUBLIC_BASE_URL}${WEBHOOK_PATH}`;
const OPERATOR_CSRF = crypto.randomBytes(32).toString("hex");
const OAUTH_STATE_SECRET = crypto.randomBytes(32).toString("hex");

if (PUBLIC_PORT === OPERATOR_PORT || WEBHOOK_PATH === OAUTH_CALLBACK_PATH) {
  throw new Error("Public and operator controls must use distinct endpoints.");
}
const PREPARED_WORKSPACE = assertPreparedCaptureWorkspace(RAW_DIRECTORY, CAPTURE_ID);
assertCredentialRotationGate({
  networkAuthorized: NETWORK_AUTHORIZED,
  creditsConfirmed: CREDITS_CONFIRMED,
  syntheticCaptureAuthorized: SYNTHETIC_ONLY_CONFIRMED,
  rotationAttested: CREDENTIAL_ROTATION_ATTESTED,
  rotationReceiptId: CREDENTIAL_ROTATION_RECEIPT_ID,
});
verifyCanonicalExitSpecPreflight(PREPARED_WORKSPACE.repositoryRoot);
assertFreshCaptureDirectory(RAW_DIRECTORY);
const recorder = createArtifactRecorder(RAW_DIRECTORY);
const configurationSnapshot = safePublicConfigurationSnapshot({
    schema_version: "exitspec.zoom-operator-configuration.v1",
    capture_id: CAPTURE_ID,
    provider: "ZOOM_RTMS",
    app_type: "GENERAL_APP",
    management_type: "USER_MANAGED",
    requested_media: ["transcript"],
    excluded_media: ["audio", "video", "screen_share", "chat"],
    required_scopes: [
      "meeting:read:meeting_audio",
      "meeting:read:meeting_transcript",
      "meeting:update:participant_rtms_app_status",
    ],
    provider_enforced_prerequisite_scopes: [
      "meeting:read:meeting_audio",
    ],
    required_events: [
      "meeting.rtms_started",
      "meeting.rtms_stopped",
      "meeting.rtms_interrupted",
    ],
    start_mode: "ON_DEMAND_AFTER_LOCAL_CONSENT_GATE",
    webhook_url: WEBHOOK_URL,
    oauth_redirect_uri: OAUTH_REDIRECT_URI,
    credentials_present: {
      client_id_configured: true,
      client_credential_configured: true,
      webhook_credential_configured: true,
    },
    client_id_sha256: sha256Hex(Buffer.from(CLIENT_ID, "utf8")),
    pinned_zoom_sample_commit: OFFICIAL_SAMPLE_COMMIT,
    real_zoom_network_authorized: NETWORK_AUTHORIZED,
    rtms_credits_confirmed: CREDITS_CONFIRMED,
    synthetic_capture_authorized: SYNTHETIC_ONLY_CONFIRMED,
    credential_rotation_attested: CREDENTIAL_ROTATION_ATTESTED,
    acceptance_verdict_authority: false,
    mapper_authority: false,
    publication_authority: false,
  });

const activeStreams = new Map();
const seenWebhookDigests = new Map();
let lastAuthenticatedWebhook = null;
let oauthTokens = null;
let pendingOAuthState = null;
let consentConfirmedAt = null;
let activeMeetingId = null;
let activeParticipantUserId = null;
let captureStopTimer = null;

function log(section, message) {
  console.log(`[${new Date().toISOString()}] [${section}] ${message}`);
}

function required(name, minimum = 1, maximum = 4096) {
  const value = process.env[name];
  if (typeof value !== "string"
    || value.length < minimum
    || value.length > maximum
    || /[\u0000\r\n]/.test(value)) {
    throw new Error(`${name} is required.`);
  }
  return value;
}

function boundedInteger(value, minimum, maximum) {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < minimum || parsed > maximum) {
    throw new Error("A bounded integer configuration value is invalid.");
  }
  return parsed;
}

function verifyCanonicalExitSpecPreflight(repositoryRoot) {
  const pythonCommand = process.env.EXITSPEC_PYTHON ?? "python3";
  if (typeof pythonCommand !== "string"
    || pythonCommand.length < 1
    || pythonCommand.length > 1024
    || /[\u0000\r\n]/.test(pythonCommand)) {
    throw new Error("EXITSPEC_PYTHON is invalid.");
  }
  const result = spawnSync(
    pythonCommand,
    [
      "-m",
      "exitspec.zoom_fixture_capture",
      "verify-preflight",
      "--capture-id",
      CAPTURE_ID,
      "--repository-root",
      repositoryRoot,
    ],
    {
      cwd: repositoryRoot,
      encoding: "utf8",
      env: { PATH: process.env.PATH ?? "" },
      maxBuffer: 1024 * 1024,
      timeout: 15_000,
    },
  );
  if (result.error || result.status !== 0 || result.signal) {
    throw new Error("ExitSpec canonical preflight verification failed.");
  }
}

function normalizedPublicBaseUrl(value) {
  const url = new URL(value);
  if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash) {
    throw new Error("PUBLIC_BASE_URL must be an HTTPS origin.");
  }
  return url.origin;
}

function safePublicPath(value, requiredPrefix) {
  if (!value.startsWith(requiredPrefix) || !/^\/[a-z0-9/_-]{24,160}$/.test(value)) {
    throw new Error("A public callback path is not sufficiently scoped.");
  }
  return value;
}

function optionalParticipantUserId(value) {
  if (value === undefined || value === null || value === "") return null;
  if (typeof value !== "string"
    || value.length > 256
    || !/^[a-zA-Z0-9._~@:+/=-]+$/.test(value)) {
    throw new Error("Participant user ID is invalid.");
  }
  return value;
}

function safeZoomWebSocketUrl(value) {
  const url = new URL(value);
  const host = url.hostname.toLowerCase();
  if (url.protocol !== "wss:" || !(host.endsWith(".zoom.us") || host.endsWith(".zoomgov.com"))) {
    throw new Error("Zoom supplied an unsupported WebSocket endpoint.");
  }
  return url.toString();
}

function exactFrame(value) {
  if (Buffer.isBuffer(value)) {
    return value;
  }
  if (value instanceof ArrayBuffer) {
    return Buffer.from(value);
  }
  if (Array.isArray(value)) {
    return Buffer.concat(value.map((part) => Buffer.from(part)));
  }
  return Buffer.from(value);
}

function generatedObservation(event, detail = {}) {
  return Buffer.from(JSON.stringify({
    schema_version: "exitspec.zoom-operator-event.v1",
    event,
    observed_at: new Date().toISOString(),
    ...detail,
  }), "utf8");
}

function generateRtmsSignature(meetingUuid, streamId) {
  return crypto
    .createHmac("sha256", CLIENT_SECRET)
    .update(`${CLIENT_ID},${meetingUuid},${streamId}`)
    .digest("hex");
}

function createStream(meetingUuid, streamId, serverUrl) {
  return {
    meetingUuid,
    streamId,
    serverUrl: safeZoomWebSocketUrl(serverUrl),
    mediaUrl: null,
    signalingSocket: null,
    mediaSocket: null,
    transcriptCount: 0,
    chaosInjected: false,
    reconnectPending: false,
    stopped: false,
  };
}

function closeSocket(socket) {
  if (!socket) return;
  try {
    if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) {
      socket.close();
    }
  } catch {
    // The operator trace records the close path; cleanup is best effort.
  }
}

function connectSignaling(stream) {
  if (!NETWORK_AUTHORIZED || stream.stopped) return;
  const socket = new WebSocket(stream.serverUrl);
  stream.signalingSocket = socket;
  recorder.record(
    "disconnect_reconnect_trace",
    { direction: "LOCAL_EVENT", channel: "signaling" },
    generatedObservation("SIGNALING_CONNECT_ATTEMPT", { reconnect: stream.reconnectPending }),
  );

  socket.on("open", () => {
    const payload = Buffer.from(JSON.stringify({
      msg_type: 1,
      protocol_version: 1,
      meeting_uuid: stream.meetingUuid,
      rtms_stream_id: stream.streamId,
      sequence: crypto.randomInt(1, 1_000_000_000),
      signature: generateRtmsSignature(stream.meetingUuid, stream.streamId),
      buffer_data: false,
    }), "utf8");
    recorder.record(
      "signaling_websocket_handshake",
      { direction: "OUTBOUND", channel: "signaling" },
      payload,
    );
    socket.send(payload);
    log("SIGNALING", "Connection opened and exact handshake request captured.");
  });

  socket.on("message", (data) => {
    const raw = exactFrame(data);
    let message;
    try {
      message = parseBoundedJsonObject(raw);
    } catch {
      recorder.record(
        "disconnect_reconnect_trace",
        { direction: "INBOUND", channel: "signaling", parse_state: "REJECTED" },
        raw,
      );
      return;
    }

    if (message.msg_type === 2) {
      recorder.record(
        "signaling_websocket_handshake",
        { direction: "INBOUND", channel: "signaling" },
        raw,
      );
      if (message.status_code !== 0) {
        log("SIGNALING", "Zoom rejected the signaling handshake; no media connection opened.");
        return;
      }
      const suppliedMediaUrl = message.media_server?.server_urls?.transcript
        ?? message.media_server?.server_urls?.all;
      stream.mediaUrl = safeZoomWebSocketUrl(suppliedMediaUrl);
      stream.reconnectPending = false;
      connectMedia(stream);
      return;
    }

    if (message.msg_type === 12 && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ msg_type: 13, timestamp: message.timestamp }));
      return;
    }

    if (message.msg_type === 6) {
      const eventType = message.event?.event_type;
      if (eventType === 3 || eventType === 4) {
        recorder.record(
          "participant_lifecycle_events",
          { direction: "INBOUND", channel: "signaling", event_type: eventType },
          raw,
        );
      }
      if (eventType === 7) {
        recorder.record(
          "disconnect_reconnect_trace",
          { direction: "INBOUND", channel: "signaling", event_type: eventType },
          raw,
        );
        reconnectMedia(stream);
      }
      return;
    }

    if (message.msg_type === 8 && message.state === 2) {
      recorder.record(
        "disconnect_reconnect_trace",
        { direction: "INBOUND", channel: "signaling", stream_state: message.state },
        raw,
      );
      if (message.reason === 14) reconnectMedia(stream);
      return;
    }

    if (message.msg_type === 9) {
      recorder.record(
        "participant_lifecycle_events",
        { direction: "INBOUND", channel: "signaling", message_type: 9 },
        raw,
      );
    }
  });

  socket.on("close", (code) => {
    recorder.record(
      "disconnect_reconnect_trace",
      { direction: "LOCAL_EVENT", channel: "signaling", close_code: code },
      generatedObservation("SIGNALING_CLOSED", { close_code: code }),
    );
    stream.signalingSocket = null;
    log("SIGNALING", "Connection closed; waiting for Zoom's explicit reconnect signal.");
  });

  socket.on("error", () => {
    recorder.record(
      "disconnect_reconnect_trace",
      { direction: "LOCAL_EVENT", channel: "signaling" },
      generatedObservation("SIGNALING_ERROR"),
    );
  });
}

function connectMedia(stream) {
  if (!NETWORK_AUTHORIZED || stream.stopped || !stream.mediaUrl || stream.mediaSocket) return;
  const socket = new WebSocket(stream.mediaUrl);
  stream.mediaSocket = socket;

  socket.on("open", () => {
    const payload = Buffer.from(JSON.stringify({
      msg_type: 3,
      protocol_version: 1,
      meeting_uuid: stream.meetingUuid,
      rtms_stream_id: stream.streamId,
      signature: generateRtmsSignature(stream.meetingUuid, stream.streamId),
      media_type: 8,
      payload_encryption: false,
    }), "utf8");
    recorder.record(
      "transcript_websocket_handshake",
      { direction: "OUTBOUND", channel: "transcript" },
      payload,
    );
    socket.send(payload);
    log("TRANSCRIPT", "Connection opened and transcript-only handshake request captured.");
  });

  socket.on("message", (data) => {
    const raw = exactFrame(data);
    let message;
    try {
      message = parseBoundedJsonObject(raw);
    } catch {
      recorder.record(
        "disconnect_reconnect_trace",
        { direction: "INBOUND", channel: "transcript", parse_state: "REJECTED" },
        raw,
      );
      return;
    }

    if (message.msg_type === 4) {
      recorder.record(
        "transcript_websocket_handshake",
        { direction: "INBOUND", channel: "transcript" },
        raw,
      );
      if (message.status_code === 0 && stream.signalingSocket?.readyState === WebSocket.OPEN) {
        stream.signalingSocket.send(JSON.stringify({ msg_type: 7, rtms_stream_id: stream.streamId }));
        stream.reconnectPending = false;
        log("TRANSCRIPT", "Handshake accepted; CLIENT_READY_ACK sent on signaling channel.");
      }
      return;
    }

    if (message.msg_type === 12 && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ msg_type: 13, timestamp: message.timestamp }));
      return;
    }

    if (message.msg_type === 17) {
      stream.transcriptCount += 1;
      recorder.record(
        "transcript_packets",
        { direction: "INBOUND", channel: "transcript", ordinal: stream.transcriptCount },
        raw,
      );
      log("TRANSCRIPT", `Captured opaque transcript packet ${stream.transcriptCount}; content was not logged.`);
      if (!stream.chaosInjected) {
        stream.chaosInjected = true;
        setTimeout(() => {
          if (socket.readyState !== WebSocket.OPEN || stream.stopped) return;
          recorder.record(
            "disconnect_reconnect_trace",
            { direction: "LOCAL_EVENT", channel: "transcript" },
            generatedObservation("CONTROLLED_MEDIA_DISCONNECT_AFTER_FIRST_TRANSCRIPT"),
          );
          socket.terminate();
          log("CHAOS", "Injected the one approved media disconnect after transcript delivery.");
        }, CHAOS_AFTER_TRANSCRIPT_SECONDS * 1000);
      }
    }
  });

  socket.on("close", (code) => {
    recorder.record(
      "disconnect_reconnect_trace",
      { direction: "LOCAL_EVENT", channel: "transcript", close_code: code },
      generatedObservation("TRANSCRIPT_SOCKET_CLOSED", { close_code: code }),
    );
    if (stream.mediaSocket === socket) stream.mediaSocket = null;
  });

  socket.on("error", () => {
    recorder.record(
      "disconnect_reconnect_trace",
      { direction: "LOCAL_EVENT", channel: "transcript" },
      generatedObservation("TRANSCRIPT_SOCKET_ERROR"),
    );
  });
}

function reconnectMedia(stream) {
  if (stream.stopped || stream.reconnectPending) return;
  stream.reconnectPending = true;
  closeSocket(stream.mediaSocket);
  stream.mediaSocket = null;
  recorder.record(
    "disconnect_reconnect_trace",
    { direction: "LOCAL_EVENT", channel: "transcript" },
    generatedObservation("MEDIA_RECONNECT_SCHEDULED", { delay_ms: 3000 }),
  );
  setTimeout(() => connectMedia(stream), 3000);
}

function stopAndRemoveStream(streamId) {
  const stream = activeStreams.get(streamId);
  if (!stream) return;
  stream.stopped = true;
  closeSocket(stream.mediaSocket);
  closeSocket(stream.signalingSocket);
  activeStreams.delete(streamId);
}

async function processWebhook(rawBody, headers, { controlledReplay = false } = {}) {
  const body = exactFrame(rawBody);
  const payload = parseBoundedJsonObject(body);
  const event = payload.event;
  const digest = sha256Hex(body);

  if (event === "endpoint.url_validation") {
    recorder.record(
      "endpoint_validation_request",
      { direction: "INBOUND", transport: "HTTPS" },
      body,
    );
    const response = computeEndpointValidationResponse(SECRET_TOKEN, payload.payload?.plainToken);
    const responseBody = Buffer.from(JSON.stringify(response), "utf8");
    recorder.record(
      "endpoint_validation_response",
      { direction: "OUTBOUND", transport: "HTTPS", status_code: 200 },
      responseBody,
    );
    return { status: 200, body: responseBody };
  }

  const timestamp = String(headers["x-zm-request-timestamp"] ?? "");
  const signature = String(headers["x-zm-signature"] ?? "");
  const replayNow = controlledReplay && /^\d{10}$/.test(timestamp)
    ? Number(timestamp) * 1000
    : Date.now();
  if (!verifyZoomWebhookSignature({
    secretToken: SECRET_TOKEN,
    timestamp,
    signature,
    rawBody: body,
    nowMs: replayNow,
  })) {
    return { status: 401, body: Buffer.from('{"error":"unauthorized"}', "utf8") };
  }

  const first = seenWebhookDigests.get(digest);
  if (first) {
    if (!first.duplicateTraceWritten) {
      recorder.record(
        "duplicate_delivery_trace",
        { direction: "INBOUND", delivery: "ORIGINAL", event: first.event },
        first.body,
      );
      first.duplicateTraceWritten = true;
    }
    recorder.record(
      "duplicate_delivery_trace",
      {
        direction: "INBOUND",
        delivery: controlledReplay ? "CONTROLLED_EXACT_REPLAY" : "PROVIDER_DUPLICATE",
        event,
      },
      body,
    );
    log("WEBHOOK", `Duplicate delivery captured for ${event}; downstream action suppressed.`);
    return { status: 200, body: Buffer.from('{"ok":true,"duplicate":true}', "utf8") };
  }

  seenWebhookDigests.set(digest, {
    body: Buffer.from(body),
    event,
    duplicateTraceWritten: false,
  });
  lastAuthenticatedWebhook = { body: Buffer.from(body), headers: { ...headers } };

  if (event === "meeting.rtms_started") {
    recorder.record(
      "rtms_started_webhook",
      { direction: "INBOUND", transport: "HTTPS", signature_verified: true },
      body,
    );
    const meetingUuid = payload.payload?.meeting_uuid;
    const streamId = payload.payload?.rtms_stream_id;
    const serverUrl = payload.payload?.server_urls;
    if (typeof meetingUuid !== "string" || typeof streamId !== "string" || typeof serverUrl !== "string") {
      return { status: 422, body: Buffer.from('{"error":"unsupported_payload"}', "utf8") };
    }
    if (activeStreams.has(streamId)) {
      stopAndRemoveStream(streamId);
    }
    const stream = createStream(meetingUuid, streamId, serverUrl);
    activeStreams.set(streamId, stream);
    connectSignaling(stream);
  } else if (event === "meeting.rtms_interrupted") {
    recorder.record(
      "disconnect_reconnect_trace",
      { direction: "INBOUND", transport: "HTTPS", event },
      body,
    );
    const streamId = payload.payload?.rtms_stream_id;
    const stream = activeStreams.get(streamId);
    if (stream) {
      closeSocket(stream.mediaSocket);
      closeSocket(stream.signalingSocket);
      stream.serverUrl = safeZoomWebSocketUrl(payload.payload?.server_urls ?? stream.serverUrl);
      stream.reconnectPending = true;
      setTimeout(() => connectSignaling(stream), 3000);
    }
  } else if (event === "meeting.rtms_stopped") {
    recorder.record(
      "rtms_stopped_webhook",
      { direction: "INBOUND", transport: "HTTPS", signature_verified: true },
      body,
    );
    stopAndRemoveStream(payload.payload?.rtms_stream_id);
  }

  return { status: 200, body: Buffer.from('{"ok":true}', "utf8") };
}

async function getAccessToken() {
  if (!oauthTokens) throw new Error("OAuth authorization has not completed.");
  if (oauthTokens.expiresAt > Date.now() + 30_000) return oauthTokens.accessToken;
  if (!oauthTokens.refreshToken) throw new Error("OAuth token expired without a refresh token.");
  const response = await fetch("https://zoom.us/oauth/token", {
    method: "POST",
    headers: {
      Authorization: `Basic ${Buffer.from(`${CLIENT_ID}:${CLIENT_SECRET}`).toString("base64")}`,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: new URLSearchParams({
      grant_type: "refresh_token",
      refresh_token: oauthTokens.refreshToken,
    }),
  });
  if (!response.ok) throw new Error("Zoom OAuth refresh failed.");
  const token = await response.json();
  oauthTokens = {
    accessToken: token.access_token,
    refreshToken: token.refresh_token ?? oauthTokens.refreshToken,
    expiresAt: Date.now() + Number(token.expires_in ?? 3600) * 1000,
  };
  return oauthTokens.accessToken;
}

async function updateRtmsStatus(action, meetingId, participantUserId = null) {
  if (!NETWORK_AUTHORIZED || !CREDITS_CONFIRMED || !SYNTHETIC_ONLY_CONFIRMED) {
    throw new Error("Real Zoom calls remain blocked by operator configuration.");
  }
  assertCredentialRotationGate({
    networkAuthorized: NETWORK_AUTHORIZED,
    creditsConfirmed: CREDITS_CONFIRMED,
    syntheticCaptureAuthorized: SYNTHETIC_ONLY_CONFIRMED,
    rotationAttested: CREDENTIAL_ROTATION_ATTESTED,
    rotationReceiptId: CREDENTIAL_ROTATION_RECEIPT_ID,
  });
  if (!consentConfirmedAt) throw new Error("Both synthetic participants must confirm consent first.");
  if (!/^\d{9,13}$/.test(meetingId)) throw new Error("Meeting ID is invalid.");
  const accessToken = await getAccessToken();
  const settings = { client_id: CLIENT_ID };
  if (participantUserId) settings.participant_user_id = participantUserId;
  const response = await fetch(
    `https://api.zoom.us/v2/live_meetings/${encodeURIComponent(meetingId)}/rtms_app/status`,
    {
      method: "PATCH",
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ action, settings }),
    },
  );
  if (!response.ok) {
    throw new Error(`Zoom RTMS ${action} request failed with status ${response.status}.`);
  }
  recorder.record(
    "timestamp_observations",
    { direction: "LOCAL_EVENT", action: `RTMS_${action.toUpperCase()}` },
    generatedObservation(`RTMS_${action.toUpperCase()}_ACCEPTED`, { status_code: response.status }),
  );
}

const publicApp = express();
publicApp.disable("x-powered-by");
publicApp.post(
  WEBHOOK_PATH,
  express.raw({ type: "application/json", limit: "256kb" }),
  async (request, response) => {
    try {
      const result = await processWebhook(request.body, {
        "x-zm-request-timestamp": request.get("x-zm-request-timestamp"),
        "x-zm-signature": request.get("x-zm-signature"),
      });
      response.status(result.status).type("application/json").send(result.body);
    } catch {
      response.status(400).json({ error: "request_rejected" });
    }
  },
);

publicApp.get(OAUTH_CALLBACK_PATH, async (request, response) => {
  try {
    if (!verifyOAuthState(OAUTH_STATE_SECRET, request.query.state)
      || request.query.state !== pendingOAuthState
      || typeof request.query.code !== "string") {
      throw new Error("OAuth state was rejected.");
    }
    pendingOAuthState = null;
    const tokenResponse = await fetch("https://zoom.us/oauth/token", {
      method: "POST",
      headers: {
        Authorization: `Basic ${Buffer.from(`${CLIENT_ID}:${CLIENT_SECRET}`).toString("base64")}`,
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: new URLSearchParams({
        grant_type: "authorization_code",
        code: request.query.code,
        redirect_uri: OAUTH_REDIRECT_URI,
      }),
    });
    if (!tokenResponse.ok) throw new Error("OAuth exchange failed.");
    const token = await tokenResponse.json();
    oauthTokens = {
      accessToken: token.access_token,
      refreshToken: token.refresh_token ?? null,
      expiresAt: Date.now() + Number(token.expires_in ?? 3600) * 1000,
    };
    response.type("text/plain").send("ExitSpec synthetic Zoom authorization completed. Tokens remain process-memory only. Return to the local operator page.");
    log("OAUTH", "Authorization completed; token values were not logged or written.");
  } catch {
    response.status(400).type("text/plain").send("Zoom authorization failed closed. Return to the local operator page and retry.");
  }
});

const operatorApp = express();
operatorApp.disable("x-powered-by");
operatorApp.use(express.urlencoded({ extended: false, limit: "16kb" }));

function requireCsrf(request, response, next) {
  if (request.body?.csrf !== OPERATOR_CSRF && request.query?.csrf !== OPERATOR_CSRF) {
    return response.status(403).type("text/plain").send("Operator request rejected.");
  }
  return next();
}

operatorApp.get("/", (_request, response) => {
  const inventory = recorder.inventory();
  const completeCount = inventory.filter((item) => item.byte_count > 0).length;
  response.type("html").send(`<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ExitSpec Zoom synthetic capture</title>
<style>body{font:15px system-ui;background:#0e141b;color:#f1f4f7;margin:0;padding:32px}main{max-width:760px;margin:auto}.card{background:#18222d;border:1px solid #2a3747;border-radius:12px;padding:20px;margin:14px 0}button,a.action{background:#d97745;color:#fff;border:0;border-radius:8px;padding:10px 14px;text-decoration:none;font-weight:650;cursor:pointer}input{display:block;margin:8px 0 14px;padding:9px;width:min(100%,520px);box-sizing:border-box}.muted{color:#9aa8b8}.ok{color:#8fc7a8}.blocked{color:#e1a47d}code{word-break:break-all}</style></head>
<body><main><h1>ExitSpec · Zoom synthetic capture</h1>
<p class="muted">Private operator harness. Transcript only. No ExitSpec verdict authority.</p>
<section class="card"><h2>Gate</h2>
<p>Real network: <strong class="${NETWORK_AUTHORIZED ? "ok" : "blocked"}">${NETWORK_AUTHORIZED ? "authorized" : "blocked"}</strong></p>
<p>Developer credits: <strong class="${CREDITS_CONFIRMED ? "ok" : "blocked"}">${CREDITS_CONFIRMED ? "confirmed" : "not confirmed"}</strong></p>
<p>OAuth: <strong class="${oauthTokens ? "ok" : "blocked"}">${oauthTokens ? "authorized in memory" : "not authorized"}</strong></p>
<p>Two-person consent: <strong class="${consentConfirmedAt ? "ok" : "blocked"}">${consentConfirmedAt ? "confirmed" : "not confirmed"}</strong></p>
<p>Evidence roles with bytes: <strong>${completeCount}/12</strong></p></section>
<section class="card"><h2>1 · Consent</h2><p>This is a synthetic ExitSpec Zoom RTMS test. Transcript only; no customer data.</p>
<form method="post" action="/operator/consent"><input type="hidden" name="csrf" value="${OPERATOR_CSRF}">
<label><input required type="checkbox" name="host" value="yes"> Synthetic host accepted</label>
<label><input required type="checkbox" name="guest" value="yes"> Synthetic guest accepted</label>
<button>Record in-memory consent gate</button></form></section>
<section class="card"><h2>2 · OAuth</h2><a class="action" href="/oauth/start?csrf=${OPERATOR_CSRF}">Authorize synthetic capture app</a></section>
<section class="card"><h2>3 · Start/stop</h2><form method="post" action="/operator/start"><input type="hidden" name="csrf" value="${OPERATOR_CSRF}">
<label>Meeting ID<input required name="meeting_id" inputmode="numeric" autocomplete="off"></label>
<label>Participant user ID (optional)<input name="participant_user_id" autocomplete="off"></label><button>Start transcript RTMS</button></form>
<form method="post" action="/operator/stop"><input type="hidden" name="csrf" value="${OPERATOR_CSRF}"><button>Stop RTMS</button></form></section>
<section class="card"><h2>4 · Duplicate trace</h2><form method="post" action="/operator/replay"><input type="hidden" name="csrf" value="${OPERATOR_CSRF}"><button>Replay last authenticated webhook locally</button></form></section>
<section class="card"><h2>Provider configuration</h2><p>Webhook: <code>${WEBHOOK_URL}</code></p><p>OAuth redirect: <code>${OAUTH_REDIRECT_URI}</code></p></section>
</main></body></html>`);
});

operatorApp.post("/operator/consent", requireCsrf, (request, response) => {
  if (request.body.host !== "yes" || request.body.guest !== "yes") {
    return response.status(400).type("text/plain").send("Both synthetic participants must consent.");
  }
  consentConfirmedAt = new Date().toISOString();
  recorder.record(
    "timestamp_observations",
    { direction: "LOCAL_EVENT", event: "CONSENT_GATE_CONFIRMED" },
    generatedObservation("TWO_SYNTHETIC_PARTICIPANT_CONSENT_CONFIRMED"),
  );
  return response.redirect("/");
});

operatorApp.get("/oauth/start", requireCsrf, (_request, response) => {
  const nonce = crypto.randomBytes(16).toString("hex");
  pendingOAuthState = signOAuthState(OAUTH_STATE_SECRET, nonce, Date.now());
  const authorize = new URL("https://zoom.us/oauth/authorize");
  authorize.searchParams.set("response_type", "code");
  authorize.searchParams.set("client_id", CLIENT_ID);
  authorize.searchParams.set("redirect_uri", OAUTH_REDIRECT_URI);
  authorize.searchParams.set("state", pendingOAuthState);
  response.redirect(authorize.toString());
});

operatorApp.post("/operator/start", requireCsrf, async (request, response) => {
  try {
    const participantUserId = optionalParticipantUserId(request.body.participant_user_id);
    await updateRtmsStatus("start", request.body.meeting_id, participantUserId);
    activeMeetingId = request.body.meeting_id;
    activeParticipantUserId = participantUserId;
    clearTimeout(captureStopTimer);
    captureStopTimer = setTimeout(async () => {
      try {
        if (activeMeetingId) await updateRtmsStatus("stop", activeMeetingId, activeParticipantUserId);
      } catch {
        log("SAFETY", "Automatic RTMS stop failed; operator action is required.");
      } finally {
        activeMeetingId = null;
        activeParticipantUserId = null;
      }
    }, CAPTURE_WINDOW_SECONDS * 1000);
    response.redirect("/");
  } catch (error) {
    response.status(409).type("text/plain").send(error.message);
  }
});

operatorApp.post("/operator/stop", requireCsrf, async (_request, response) => {
  try {
    if (!activeMeetingId) throw new Error("No active meeting is held in operator memory.");
    await updateRtmsStatus("stop", activeMeetingId, activeParticipantUserId);
    activeMeetingId = null;
    activeParticipantUserId = null;
    clearTimeout(captureStopTimer);
    response.redirect("/");
  } catch (error) {
    response.status(409).type("text/plain").send(error.message);
  }
});

operatorApp.post("/operator/replay", requireCsrf, async (_request, response) => {
  try {
    if (!lastAuthenticatedWebhook) throw new Error("No authenticated webhook is available to replay.");
    await processWebhook(lastAuthenticatedWebhook.body, lastAuthenticatedWebhook.headers, { controlledReplay: true });
    response.redirect("/");
  } catch (error) {
    response.status(409).type("text/plain").send(error.message);
  }
});

const publicServer = http.createServer(publicApp);
const operatorServer = http.createServer(operatorApp);

function listenLoopback(server, port) {
  return new Promise((resolve, reject) => {
    const onError = () => {
      server.off("listening", onListening);
      reject(new Error("A loopback listener could not start."));
    };
    const onListening = () => {
      server.off("error", onError);
      resolve();
    };
    server.once("error", onError);
    server.once("listening", onListening);
    server.listen(port, "127.0.0.1");
  });
}

function closeServer(server) {
  if (!server.listening) return Promise.resolve();
  return new Promise((resolve) => server.close(() => resolve()));
}

async function shutdown() {
  clearTimeout(captureStopTimer);
  for (const stream of activeStreams.values()) {
    stream.stopped = true;
    closeSocket(stream.mediaSocket);
    closeSocket(stream.signalingSocket);
  }
  await Promise.all([
    closeServer(publicServer),
    closeServer(operatorServer),
  ]);
}

try {
  await Promise.all([
    listenLoopback(publicServer, PUBLIC_PORT),
    listenLoopback(operatorServer, OPERATOR_PORT),
  ]);
  recorder.record(
    "app_configuration_snapshot",
    { direction: "LOCAL_SNAPSHOT", source: "operator_harness" },
    configurationSnapshot,
  );
} catch {
  await shutdown();
  throw new Error("The operator harness failed before capture startup completed.");
}

log("READY", `Public callback receiver listening on loopback port ${PUBLIC_PORT}.`);
log("READY", `Operator console: http://127.0.0.1:${OPERATOR_PORT}/`);
log("PRIVACY", "Transcript content, OAuth tokens, meeting IDs, and provider secrets are not logged.");

process.once("SIGINT", async () => {
  await shutdown();
  process.exit(0);
});
process.once("SIGTERM", async () => {
  await shutdown();
  process.exit(0);
});
