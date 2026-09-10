// Private signaling-only enrollment. Names never leave this boundary.
// Provider timestamps are a conservative freshness filter, not identity proof.
export function validEnrollment(value, now) {
  return value && typeof value === 'object' && !Array.isArray(value) &&
    Object.keys(value).sort().join(',') === 'deadlineMs,runDeadlineMs' &&
    Number.isSafeInteger(value.deadlineMs) && Number.isSafeInteger(value.runDeadlineMs) &&
    now < value.deadlineMs && value.deadlineMs <= now + 30000 &&
    value.deadlineMs <= value.runDeadlineMs && value.runDeadlineMs <= now + 120000;
}

export function createRosterEnrollment({limits, now, timers, emit, fail}) {
  if (!validEnrollment(limits, now())) throw new Error('enrollment');
  let phase = 'learning', slot = null;
  const confirmed = [], seen = new Set(), nonces = new Set();
  const phaseTimer = timers.setTimeout(() => fail('enrollment_timeout'), limits.deadlineMs - now());
  const runTimer = timers.setTimeout(() => fail('capture_timeout'), limits.runDeadlineMs - now());
  function close() {
    phase = 'closed'; slot = null; confirmed.length = 0; seen.clear(); nonces.clear();
    timers.clearTimeout(phaseTimer); timers.clearTimeout(runTimer);
  }
  function check() {
    if (phase === 'closed') return false;
    if (now() >= limits.runDeadlineMs || (phase === 'learning' && now() >= limits.deadlineMs)) {
      fail('enrollment_expired'); return false;
    }
    return true;
  }
  function see(id) {
    if (!check()) return false;
    if (!Number.isInteger(id) || id < 1 || id > 0xffffffff ||
        (phase !== 'learning' && !confirmed.includes(id))) {
      fail('invalid_participant'); return false;
    }
    seen.add(id);
    if (seen.size > 2) { fail('invalid_participant'); return false; }
    return true;
  }
  function arm(nonce) {
    if (!check()) return;
    if (phase !== 'learning' || slot || confirmed.length >= 2 ||
        typeof nonce !== 'string' || !/^[a-f0-9]{64}$/.test(nonce) || nonces.has(nonce)) {
      fail('enrollment_command'); return;
    }
    nonces.add(nonce);
    slot = {nonce, after: now(), barrier: null, candidate: null};
    // Wait for a later provider keep-alive on this ordered signaling connection.
    // Until then, all queued speaker events are ineligible for this slot.
  }
  function heartbeat(timestamp) {
    if (!check() || phase !== 'learning' || !slot || slot.barrier !== null) return;
    if (!Number.isSafeInteger(timestamp) || timestamp > now()) {
      fail('enrollment_timestamp'); return;
    }
    if (timestamp <= slot.after) return;
    slot.barrier = timestamp;
    emit({event:'enrollment_armed', nonce:slot.nonce});
  }
  function speaker(value) {
    if (!see(value?.user_id)) return;
    if (phase !== 'learning') return;
    const timestamp = value.timestamp;
    if (!Number.isSafeInteger(timestamp) || timestamp < 1 || timestamp > now()) {
      fail('enrollment_timestamp'); return;
    }
    if (!slot || slot.barrier === null || timestamp <= slot.barrier) return;
    if (slot.candidate !== null) {
      // A second eligible event makes the operator's pending observation ambiguous.
      fail('enrollment_ambiguous'); return;
    }
    if (confirmed.includes(value.user_id)) { fail('enrollment_duplicate'); return; }
    slot.candidate = value.user_id;
    emit({event:'enrollment_candidate', nonce:slot.nonce, user_id:value.user_id});
  }
  function confirm(nonce, id) {
    if (!check()) return;
    if (phase !== 'learning' || !slot || slot.nonce !== nonce ||
        slot.candidate !== id || !Number.isInteger(id) || confirmed.includes(id)) {
      fail('enrollment_confirmation'); return;
    }
    confirmed.push(id); slot = null;
    emit({event:'enrollment_confirmed', nonce, user_id:id});
  }
  function seal(ids) {
    if (!check()) return;
    if (phase !== 'learning' || slot || confirmed.length !== 2 ||
        !Array.isArray(ids) || ids.length !== 2 || ids.some((id,i) => id !== confirmed[i])) {
      fail('enrollment_seal'); return;
    }
    phase = 'sealed'; timers.clearTimeout(phaseTimer); nonces.clear();
  }
  function activate() {
    if (!check()) return false;
    if (phase !== 'sealed') { fail('enrollment_activation'); return false; }
    phase = 'active'; return true;
  }
  return {check, see, arm, heartbeat, speaker, confirm, seal, activate, close,
    get mediaAllowed() { return phase === 'active' && check(); }};
}
