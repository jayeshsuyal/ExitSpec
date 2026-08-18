import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";

export const ARTIFACT_FILES = Object.freeze({
  app_configuration_snapshot: "app-configuration-snapshot.bin",
  endpoint_validation_request: "endpoint-validation-request.bin",
  endpoint_validation_response: "endpoint-validation-response.bin",
  rtms_started_webhook: "rtms-started-webhook.bin",
  rtms_stopped_webhook: "rtms-stopped-webhook.bin",
  signaling_websocket_handshake: "signaling-websocket-handshake.bin",
  transcript_websocket_handshake: "transcript-websocket-handshake.bin",
  participant_lifecycle_events: "participant-lifecycle-events.bin",
  transcript_packets: "transcript-packets.bin",
  disconnect_reconnect_trace: "disconnect-reconnect-trace.bin",
  duplicate_delivery_trace: "duplicate-delivery-trace.bin",
  timestamp_observations: "timestamp-observations.bin",
});

const RECORD_MAGIC = Buffer.from("EXITSPEC_ZOOM_OPAQUE_OBSERVATION_V1\n", "utf8");
const MAX_BODY_BYTES = 256 * 1024;
const MAX_METADATA_BYTES = 16 * 1024;
const MAX_ARTIFACT_BYTES = 16 * 1024 * 1024;
const MAX_CAPTURE_BYTES = 64 * 1024 * 1024;
const MAX_CONTROL_FILE_BYTES = 512 * 1024;

const CAPTURE_PLAN_VERSION = "exitspec.zoom-golden-capture-plan.v1";
const PREFLIGHT_RECEIPT_VERSION = "exitspec.zoom-golden-preflight-receipt.v1";
const CAPTURE_AUTHORITY = "PRIVATE_SYNTHETIC_FIXTURE_CUSTODY_ONLY";
const READY_STATE = "READY_FOR_OPERATOR_CONTROLLED_SYNTHETIC_CAPTURE";

export function sha256Hex(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

export function computeEndpointValidationResponse(secretToken, plainToken) {
  requireNonEmptySecret(secretToken, "ZOOM_SECRET_TOKEN");
  if (typeof plainToken !== "string" || plainToken.length < 1 || plainToken.length > 4096) {
    throw new Error("Invalid endpoint validation token.");
  }
  return {
    plainToken,
    encryptedToken: crypto
      .createHmac("sha256", secretToken)
      .update(plainToken)
      .digest("hex"),
  };
}

export function verifyZoomWebhookSignature({
  secretToken,
  timestamp,
  signature,
  rawBody,
  nowMs = Date.now(),
  toleranceSeconds = 300,
}) {
  requireNonEmptySecret(secretToken, "ZOOM_SECRET_TOKEN");
  const body = toBoundedBuffer(rawBody);
  if (!/^\d{10,13}$/.test(String(timestamp ?? ""))) {
    return false;
  }
  if (!/^v0=[a-f0-9]{64}$/i.test(String(signature ?? ""))) {
    return false;
  }
  const timestampNumber = Number(timestamp);
  const timestampMs = String(timestamp).length === 10 ? timestampNumber * 1000 : timestampNumber;
  if (!Number.isSafeInteger(timestampMs)) {
    return false;
  }
  if (Math.abs(nowMs - timestampMs) > toleranceSeconds * 1000) {
    return false;
  }
  const message = Buffer.concat([
    Buffer.from(`v0:${timestamp}:`, "utf8"),
    body,
  ]);
  const expected = `v0=${crypto
    .createHmac("sha256", secretToken)
    .update(message)
    .digest("hex")}`;
  const suppliedBuffer = Buffer.from(String(signature), "utf8");
  const expectedBuffer = Buffer.from(expected, "utf8");
  return suppliedBuffer.length === expectedBuffer.length
    && crypto.timingSafeEqual(suppliedBuffer, expectedBuffer);
}

export function parseBoundedJsonObject(rawBody) {
  const body = toBoundedBuffer(rawBody);
  let value;
  try {
    value = JSON.parse(body.toString("utf8"));
  } catch {
    throw new Error("Webhook body is not valid JSON.");
  }
  if (value === null || Array.isArray(value) || typeof value !== "object") {
    throw new Error("Webhook body must be a JSON object.");
  }
  return value;
}

export function createArtifactRecorder(rawDirectory, { clock = () => new Date() } = {}) {
  const root = path.resolve(rawDirectory);
  ensurePrivateDirectory(root);

  function record(role, metadata, payload) {
    const filename = ARTIFACT_FILES[role];
    if (!filename) {
      throw new Error("Unknown capture artifact role.");
    }
    const body = toBoundedBuffer(payload);
    const observedAt = clock().toISOString();
    const entryMetadata = {
      schema_version: "exitspec.zoom-opaque-observation.v1",
      role,
      observed_at: observedAt,
      byte_count: body.length,
      sha256: sha256Hex(body),
      ...metadata,
    };
    appendNativeRecord(path.join(root, filename), entryMetadata, body);
    if (role !== "timestamp_observations") {
      const timingPayload = Buffer.from(JSON.stringify({
        observed_at: observedAt,
        role,
        payload_sha256: entryMetadata.sha256,
      }), "utf8");
      appendNativeRecord(
        path.join(root, ARTIFACT_FILES.timestamp_observations),
        {
          schema_version: "exitspec.zoom-timestamp-observation.v1",
          role: "timestamp_observations",
          observed_at: observedAt,
          source_role: role,
          byte_count: timingPayload.length,
          sha256: sha256Hex(timingPayload),
        },
        timingPayload,
      );
    }
    return { observedAt, byteCount: body.length, sha256: entryMetadata.sha256 };
  }

  function inventory() {
    return Object.entries(ARTIFACT_FILES).map(([role, filename]) => {
      const target = path.join(root, filename);
      if (!fs.existsSync(target)) {
        return { role, filename, byte_count: 0, present: false };
      }
      const stat = safeArtifactStat(target);
      return { role, filename, byte_count: stat.size, present: true };
    });
  }

  return Object.freeze({ root, record, inventory });
}

export function assertFreshCaptureDirectory(rawDirectory) {
  const root = path.resolve(rawDirectory);
  ensurePrivateDirectory(root);
  for (const filename of Object.values(ARTIFACT_FILES)) {
    const target = path.join(root, filename);
    if (!fs.existsSync(target)) {
      continue;
    }
    const stat = safeArtifactStat(target);
    if (stat.size > 0) {
      throw new Error("Capture directory already contains evidence; use a new capture ID.");
    }
  }
}

export function assertPreparedCaptureWorkspace(rawDirectory, captureId) {
  if (!/^zoomcap_[a-z0-9][a-z0-9_-]{2,95}$/.test(String(captureId ?? ""))) {
    throw new Error("Capture ID does not match the ExitSpec capture contract.");
  }
  const raw = path.resolve(rawDirectory);
  const workspace = path.dirname(raw);
  const privateRoot = path.dirname(workspace);
  if (path.basename(raw) !== "raw"
    || path.basename(workspace) !== captureId
    || path.basename(privateRoot) !== ".zoom-fixture-private") {
    throw new Error("Capture path is outside the ExitSpec private fixture workspace.");
  }

  requireExistingPrivateDirectory(privateRoot);
  requireExistingPrivateDirectory(workspace);
  requireExistingPrivateDirectory(raw);
  const expectedRealRaw = path.join(fs.realpathSync(privateRoot), captureId, "raw");
  if (fs.realpathSync(raw) !== expectedRealRaw) {
    throw new Error("Capture workspace may not contain a symlinked path.");
  }

  const plan = readPrivateControlJson(path.join(workspace, "capture-plan.json"));
  const receipt = readPrivateControlJson(path.join(workspace, "preflight-receipt.json"));
  if (plan.schema_version !== CAPTURE_PLAN_VERSION
    || plan.capture_id !== captureId
    || plan.provider !== "ZOOM_RTMS"
    || JSON.stringify(plan.requested_media) !== '["transcript"]'
    || plan.capture_kit_grants_network_authority !== false) {
    throw new Error("Capture plan does not authorize this private transcript-only workspace.");
  }
  if (receipt.schema_version !== PREFLIGHT_RECEIPT_VERSION
    || receipt.capture_id !== captureId
    || receipt.state !== READY_STATE
    || receipt.authority !== CAPTURE_AUTHORITY
    || receipt.provider_state_independently_verified !== false
    || receipt.may_call_zoom !== false
    || receipt.may_publish_fixture !== false
    || receipt.may_define_mapper !== false
    || receipt.may_assign_verdict !== false) {
    throw new Error("Capture preflight receipt is missing or incompatible.");
  }
  return Object.freeze({
    raw,
    workspace,
    privateRoot,
    repositoryRoot: path.dirname(privateRoot),
  });
}

export function safePublicConfigurationSnapshot(configuration) {
  const forbiddenKeys = new Set([
    "client_secret",
    "clientSecret",
    "secret_token",
    "secretToken",
    "access_token",
    "refresh_token",
  ]);
  const visit = (value) => {
    if (Array.isArray(value)) {
      return value.map(visit);
    }
    if (value && typeof value === "object") {
      const result = {};
      for (const [key, child] of Object.entries(value)) {
        if (forbiddenKeys.has(key)) {
          throw new Error("Configuration snapshot contains a forbidden secret field.");
        }
        result[key] = visit(child);
      }
      return result;
    }
    return value;
  };
  return Buffer.from(JSON.stringify(visit(configuration)), "utf8");
}

export function signOAuthState(secret, nonce, createdAtMs) {
  requireNonEmptySecret(secret, "OPERATOR_STATE_SECRET");
  const payload = `${nonce}.${createdAtMs}`;
  const signature = crypto.createHmac("sha256", secret).update(payload).digest("hex");
  return `${payload}.${signature}`;
}

export function verifyOAuthState(secret, state, { nowMs = Date.now(), maxAgeMs = 10 * 60 * 1000 } = {}) {
  requireNonEmptySecret(secret, "OPERATOR_STATE_SECRET");
  const match = /^([a-f0-9]{32})\.(\d{13})\.([a-f0-9]{64})$/.exec(String(state ?? ""));
  if (!match) {
    return false;
  }
  const [, nonce, createdAtRaw, supplied] = match;
  const createdAt = Number(createdAtRaw);
  if (!Number.isSafeInteger(createdAt) || createdAt > nowMs || nowMs - createdAt > maxAgeMs) {
    return false;
  }
  const expected = crypto
    .createHmac("sha256", secret)
    .update(`${nonce}.${createdAtRaw}`)
    .digest("hex");
  return crypto.timingSafeEqual(Buffer.from(supplied), Buffer.from(expected));
}

function toBoundedBuffer(value) {
  const body = Buffer.isBuffer(value) ? value : Buffer.from(value ?? "");
  if (body.length < 1 || body.length > MAX_BODY_BYTES) {
    throw new Error("Observation payload is empty or exceeds the capture bound.");
  }
  return body;
}

function requireNonEmptySecret(value, name) {
  if (typeof value !== "string" || value.length < 16) {
    throw new Error(`${name} is missing or too short.`);
  }
}

function ensurePrivateDirectory(directory) {
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  const stat = fs.lstatSync(directory);
  if (!stat.isDirectory() || stat.isSymbolicLink()) {
    throw new Error("Capture path is not a safe directory.");
  }
  fs.chmodSync(directory, 0o700);
}

function requireExistingPrivateDirectory(directory) {
  const stat = fs.lstatSync(directory);
  if (!stat.isDirectory() || stat.isSymbolicLink() || (stat.mode & 0o077) !== 0) {
    throw new Error("Capture workspace is not an existing owner-only directory.");
  }
  if (typeof process.getuid === "function" && stat.uid !== process.getuid()) {
    throw new Error("Capture workspace is not owned by the operator.");
  }
}

function readPrivateControlJson(target) {
  const stat = safeArtifactStat(target);
  if (stat.size < 1 || stat.size > MAX_CONTROL_FILE_BYTES || (stat.mode & 0o077) !== 0) {
    throw new Error("Capture control file is missing, unsafe, or oversized.");
  }
  let value;
  try {
    value = JSON.parse(fs.readFileSync(target, "utf8"));
  } catch {
    throw new Error("Capture control file is not valid JSON.");
  }
  if (value === null || Array.isArray(value) || typeof value !== "object") {
    throw new Error("Capture control file must contain a JSON object.");
  }
  return value;
}

function safeArtifactStat(target) {
  const stat = fs.lstatSync(target);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink !== 1) {
    throw new Error("Capture artifact is not a safe regular file.");
  }
  return stat;
}

function appendNativeRecord(target, metadata, payload) {
  const metadataBytes = Buffer.from(JSON.stringify(metadata), "utf8");
  if (metadataBytes.length > MAX_METADATA_BYTES) {
    throw new Error("Observation metadata exceeds the capture bound.");
  }
  const metadataLength = Buffer.alloc(4);
  metadataLength.writeUInt32BE(metadataBytes.length);
  const payloadLength = Buffer.alloc(8);
  payloadLength.writeBigUInt64BE(BigInt(payload.length));
  const recordBytes = RECORD_MAGIC.length + metadataLength.length
    + metadataBytes.length + payloadLength.length + payload.length;
  const existing = fs.existsSync(target) ? safeArtifactStat(target).size : 0;
  if (existing + recordBytes > MAX_ARTIFACT_BYTES) {
    throw new Error("Capture artifact exceeds the ExitSpec per-artifact bound.");
  }
  const captureBytes = Object.values(ARTIFACT_FILES).reduce((total, filename) => {
    const candidate = path.join(path.dirname(target), filename);
    return total + (fs.existsSync(candidate) ? safeArtifactStat(candidate).size : 0);
  }, 0);
  if (captureBytes + recordBytes > MAX_CAPTURE_BYTES) {
    throw new Error("Capture inventory exceeds the ExitSpec total bound.");
  }

  const flags = fs.constants.O_APPEND
    | fs.constants.O_CREAT
    | fs.constants.O_WRONLY
    | fs.constants.O_NOFOLLOW;
  const fd = fs.openSync(target, flags, 0o600);
  try {
    const opened = fs.fstatSync(fd);
    if (!opened.isFile() || opened.nlink !== 1 || opened.size !== existing) {
      throw new Error("Capture artifact changed before append.");
    }
    fs.writeSync(fd, RECORD_MAGIC);
    fs.writeSync(fd, metadataLength);
    fs.writeSync(fd, metadataBytes);
    fs.writeSync(fd, payloadLength);
    fs.writeSync(fd, payload);
    fs.fsyncSync(fd);
  } finally {
    fs.closeSync(fd);
  }
  fs.chmodSync(target, 0o600);
}
