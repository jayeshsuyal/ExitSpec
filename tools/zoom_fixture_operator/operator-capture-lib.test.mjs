import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  ARTIFACT_FILES,
  assertCredentialRotationGate,
  assertFreshCaptureDirectory,
  assertPreparedCaptureWorkspace,
  computeEndpointValidationResponse,
  createArtifactRecorder,
  parseBoundedJsonObject,
  safePublicConfigurationSnapshot,
  signOAuthState,
  verifyOAuthState,
  verifyZoomWebhookSignature,
} from "./operator-capture-lib.mjs";

const SECRET = "synthetic-secret-token-for-tests";

function preparedWorkspace() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "zoom-preflight-test-"));
  const privateRoot = path.join(root, ".zoom-fixture-private");
  const captureId = "zoomcap_synthetic_operator_001";
  const workspace = path.join(privateRoot, captureId);
  const raw = path.join(workspace, "raw");
  for (const directory of [privateRoot, workspace, raw]) {
    fs.mkdirSync(directory, { mode: 0o700 });
    fs.chmodSync(directory, 0o700);
  }
  const planPath = path.join(workspace, "capture-plan.json");
  const receiptPath = path.join(workspace, "preflight-receipt.json");
  fs.writeFileSync(planPath, JSON.stringify({
    schema_version: "exitspec.zoom-golden-capture-plan.v1",
    capture_id: captureId,
    provider: "ZOOM_RTMS",
    requested_media: ["transcript"],
    capture_kit_grants_network_authority: false,
  }), { mode: 0o600 });
  fs.writeFileSync(receiptPath, JSON.stringify({
    schema_version: "exitspec.zoom-golden-preflight-receipt.v1",
    capture_id: captureId,
    state: "READY_FOR_OPERATOR_CONTROLLED_SYNTHETIC_CAPTURE",
    authority: "PRIVATE_SYNTHETIC_FIXTURE_CUSTODY_ONLY",
    provider_state_independently_verified: false,
    may_call_zoom: false,
    may_publish_fixture: false,
    may_define_mapper: false,
    may_assign_verdict: false,
  }), { mode: 0o600 });
  fs.chmodSync(planPath, 0o600);
  fs.chmodSync(receiptPath, 0o600);
  return { captureId, planPath, raw, receiptPath, root, workspace };
}

test("validates Zoom signatures over the exact raw body and timestamp", () => {
  const rawBody = Buffer.from('{"event":"meeting.rtms_started"}', "utf8");
  const timestamp = "1786400000";
  const signature = `v0=${crypto
    .createHmac("sha256", SECRET)
    .update(Buffer.concat([Buffer.from(`v0:${timestamp}:`), rawBody]))
    .digest("hex")}`;
  assert.equal(verifyZoomWebhookSignature({
    secretToken: SECRET,
    timestamp,
    signature,
    rawBody,
    nowMs: 1786400000 * 1000,
  }), true);
  assert.equal(verifyZoomWebhookSignature({
    secretToken: SECRET,
    timestamp,
    signature,
    rawBody: Buffer.from("{}"),
    nowMs: 1786400000 * 1000,
  }), false);
  assert.equal(verifyZoomWebhookSignature({
    secretToken: SECRET,
    timestamp,
    signature,
    rawBody,
    nowMs: (1786400000 + 301) * 1000,
  }), false);
});

test("computes the Zoom endpoint-validation response", () => {
  const result = computeEndpointValidationResponse(SECRET, "plain-token");
  assert.equal(result.plainToken, "plain-token");
  assert.equal(result.encryptedToken, crypto.createHmac("sha256", SECRET).update("plain-token").digest("hex"));
});

test("writes append-only native records and timestamp observations", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "zoom-capture-test-"));
  const raw = path.join(root, "raw");
  assertFreshCaptureDirectory(raw);
  const recorder = createArtifactRecorder(raw, { clock: () => new Date("2026-08-11T22:00:00Z") });
  recorder.record("transcript_packets", { direction: "INBOUND" }, Buffer.from("opaque-packet"));
  const transcript = fs.readFileSync(path.join(raw, ARTIFACT_FILES.transcript_packets));
  const timing = fs.readFileSync(path.join(raw, ARTIFACT_FILES.timestamp_observations));
  assert.match(transcript.toString("utf8"), /EXITSPEC_ZOOM_OPAQUE_OBSERVATION_V1/);
  assert.match(transcript.toString("utf8"), /opaque-packet/);
  assert.ok(timing.length > 0);
  assert.equal(fs.statSync(path.join(raw, ARTIFACT_FILES.transcript_packets)).mode & 0o777, 0o600);
  assert.throws(() => assertFreshCaptureDirectory(raw), /already contains evidence/);
});

test("requires an existing owner-only ExitSpec preflight workspace", () => {
  const prepared = preparedWorkspace();
  const result = assertPreparedCaptureWorkspace(prepared.raw, prepared.captureId);
  assert.equal(result.raw, prepared.raw);
  assert.equal(result.workspace, prepared.workspace);

  assert.throws(
    () => assertPreparedCaptureWorkspace(prepared.raw, "zoomcap_changed_operator_001"),
    /outside the ExitSpec private fixture workspace/,
  );
  fs.chmodSync(prepared.receiptPath, 0o644);
  assert.throws(
    () => assertPreparedCaptureWorkspace(prepared.raw, prepared.captureId),
    /control file is missing, unsafe, or oversized/,
  );
});

test("rejects preflight plans that grant network authority", () => {
  const prepared = preparedWorkspace();
  const plan = JSON.parse(fs.readFileSync(prepared.planPath, "utf8"));
  plan.capture_kit_grants_network_authority = true;
  fs.writeFileSync(prepared.planPath, JSON.stringify(plan), { mode: 0o600 });
  fs.chmodSync(prepared.planPath, 0o600);
  assert.throws(
    () => assertPreparedCaptureWorkspace(prepared.raw, prepared.captureId),
    /does not authorize this private transcript-only workspace/,
  );
});

test("rejects symlink artifacts", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "zoom-capture-link-test-"));
  const raw = path.join(root, "raw");
  fs.mkdirSync(raw, { mode: 0o700 });
  const outside = path.join(root, "outside");
  fs.writeFileSync(outside, "private");
  fs.symlinkSync(outside, path.join(raw, ARTIFACT_FILES.transcript_packets));
  assert.throws(() => assertFreshCaptureDirectory(raw), /safe regular file/);
});

test("rejects hard-linked artifacts before append", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "zoom-capture-hardlink-test-"));
  const raw = path.join(root, "raw");
  fs.mkdirSync(raw, { mode: 0o700 });
  const outside = path.join(root, "outside");
  fs.writeFileSync(outside, "private");
  fs.linkSync(outside, path.join(raw, ARTIFACT_FILES.transcript_packets));
  const recorder = createArtifactRecorder(raw);
  assert.throws(
    () => recorder.record("transcript_packets", { direction: "INBOUND" }, Buffer.from("packet")),
    /safe regular file/,
  );
});

test("enforces the ExitSpec per-artifact byte limit before writing", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "zoom-capture-size-test-"));
  const raw = path.join(root, "raw");
  fs.mkdirSync(raw, { mode: 0o700 });
  const target = path.join(raw, ARTIFACT_FILES.transcript_packets);
  fs.writeFileSync(target, "x", { mode: 0o600 });
  fs.truncateSync(target, 16 * 1024 * 1024);
  const recorder = createArtifactRecorder(raw);
  assert.throws(
    () => recorder.record("transcript_packets", { direction: "INBOUND" }, Buffer.from("packet")),
    /per-artifact bound/,
  );
});

test("bounds JSON and excludes secret fields from the app snapshot", () => {
  assert.deepEqual(parseBoundedJsonObject(Buffer.from('{"event":"ok"}')), { event: "ok" });
  assert.throws(() => parseBoundedJsonObject(Buffer.from("[]")), /JSON object/);
  assert.throws(
    () => parseBoundedJsonObject(Buffer.alloc(256 * 1024 + 1, 0x20)),
    /exceeds the capture bound/,
  );
  assert.throws(
    () => safePublicConfigurationSnapshot({ client_secret: "must-not-write" }),
    /forbidden secret field/,
  );
  const snapshot = safePublicConfigurationSnapshot({ media: ["transcript"], secrets_present: true });
  assert.match(snapshot.toString("utf8"), /transcript/);
});

test("OAuth state is signed, time-bounded, and tamper evident", () => {
  const createdAt = 1786400000000;
  const state = signOAuthState(SECRET, "a".repeat(32), createdAt);
  assert.equal(verifyOAuthState(SECRET, state, { nowMs: createdAt + 1000 }), true);
  assert.equal(verifyOAuthState(SECRET, `${state.slice(0, -1)}0`, { nowMs: createdAt + 1000 }), false);
  assert.equal(verifyOAuthState(SECRET, state, { nowMs: createdAt + 11 * 60 * 1000 }), false);
});

test("live calls require a content-free external credential rotation receipt", () => {
  assert.doesNotThrow(() => assertCredentialRotationGate({
    networkAuthorized: false,
    creditsConfirmed: true,
    syntheticCaptureAuthorized: true,
    rotationAttested: false,
    rotationReceiptId: "",
  }));
  assert.throws(
    () => assertCredentialRotationGate({
      networkAuthorized: true,
      creditsConfirmed: true,
      syntheticCaptureAuthorized: true,
      rotationAttested: false,
      rotationReceiptId: "",
    }),
    /credential rotation is attested/,
  );
  assert.doesNotThrow(() => assertCredentialRotationGate({
    networkAuthorized: true,
    creditsConfirmed: true,
    syntheticCaptureAuthorized: true,
    rotationAttested: true,
    rotationReceiptId: `zoomcredrot_${"a".repeat(64)}`,
  }));
});
