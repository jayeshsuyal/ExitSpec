import crypto from 'node:crypto';
import WebSocket from 'ws';

// Wire fields: https://developers.zoom.us/docs/rtms/event-reference/
// No transcript normalization here. The authenticated parent decodes exact bytes.
export const MAX_PACKET_BYTES = 64 * 1024;
export function safeZoomWebSocketUrl(value) {
  if (typeof value !== 'string' || value.length > 2048) throw new Error('endpoint');
  const url = new URL(value);
  if (url.protocol !== 'wss:' || url.username || url.password || url.hash ||
      (url.port && url.port !== '443') ||
      !['.zoom.us', '.zoomgov.com'].some(suffix => url.hostname.endsWith(suffix))) {
    throw new Error('endpoint');
  }
  return url.toString();
}
export function exactFrame(value) {
  if (Buffer.isBuffer(value)) return value;
  if (value instanceof ArrayBuffer) return Buffer.from(value);
  if (Array.isArray(value)) {
    if (value.reduce((n, part) => n + part.byteLength, 0) > MAX_PACKET_BYTES) throw new Error('packet');
    return Buffer.concat(value.map(part => Buffer.from(part)));
  }
  return Buffer.from(value);
}
export function createRtmsTransport({clientId, clientSecret, meetingUuid, streamId, serverUrl,
  onEvent = () => {}, observe = () => {}, socketFactory = (url, options) => new WebSocket(url, options),
  timers = {setTimeout, clearTimeout}, chaosDelayMs = null}) {
  const stream = {meetingUuid, streamId, serverUrl: safeZoomWebSocketUrl(serverUrl), mediaUrl: null,
    signalingSocket: null, mediaSocket: null, transcriptCount: 0, stopped: false,
    reconnectPending: false, chaosInjected: false};
  let epoch = 0, mediaEpoch = 0, retries = 0, packets = 0, bytes = 0;
  let stopping = false, acknowledged = false, listening = false, closedNormally = false;
  const pendingTimers = new Set();
  const arm = (fn, ms) => { const timer = timers.setTimeout(() => { pendingTimers.delete(timer); fn(); }, ms); pendingTimers.add(timer); return timer; };
  const cancel = timer => { timers.clearTimeout(timer); pendingTimers.delete(timer); };
  const event = (name, detail = {}) => onEvent({event: name, ...detail});
  const trace = (name, channel, detail = {}) => observe('disconnect_reconnect_trace',
    {direction: 'LOCAL_EVENT', channel}, Buffer.from(JSON.stringify({schema_version:'exitspec.zoom-operator-event.v1',event:name,observed_at:new Date().toISOString(),...detail})));
  const close = socket => { try { socket?.terminate(); } catch {} };
  function revoke() {
    if (stream.stopped) return;
    stream.stopped = true; epoch++; mediaEpoch++;
    for (const timer of pendingTimers) timers.clearTimeout(timer);
    pendingTimers.clear(); close(stream.mediaSocket); close(stream.signalingSocket);
    stream.mediaSocket = stream.signalingSocket = null;
  }
  function fail(code) { if (stream.stopped) return; revoke(); event('failed', {code}); }
  function send(socket, payload) {
    if (!socket || socket.readyState !== 1 || socket.bufferedAmount > MAX_PACKET_BYTES) { fail('socket_write'); return false; }
    try { socket.send(JSON.stringify(payload)); return true; } catch { fail('socket_write'); return false; }
  }
  const signature = () => crypto.createHmac('sha256', clientSecret).update(`${clientId},${meetingUuid},${streamId}`).digest('hex');
  function parse(data, channel) {
    try {
      const raw = exactFrame(data);
      if (raw.length > MAX_PACKET_BYTES || ++packets > 4096 || (bytes += raw.length) > 8 * 1024 * 1024) throw new Error();
      const value = JSON.parse(new TextDecoder('utf-8', {fatal:true}).decode(raw));
      if (!value || Array.isArray(value) || typeof value !== 'object' || !Number.isInteger(value.msg_type)) throw new Error();
      return {raw, value};
    } catch { trace('PACKET_REJECTED', channel); fail('invalid_packet'); return null; }
  }
  function finishDrain() {
    if (!acknowledged || !closedNormally || stream.stopped) return;
    revoke(); event('drained'); // Local socket drain only; never a completeness assertion.
  }
  function connectSignaling() {
    if (stream.stopped || stopping) return;
    const ownEpoch = ++epoch;
    let socket;
    try { socket = socketFactory(stream.serverUrl, {maxPayload: MAX_PACKET_BYTES, perMessageDeflate: false, handshakeTimeout: 10000, followRedirects:false}); }
    catch { fail('socket_connect'); return; }
    stream.signalingSocket = socket;
    const current = () => !stream.stopped && epoch === ownEpoch && stream.signalingSocket === socket;
    let accepted = false;
    let idleTimer;
    const touch = () => { if (idleTimer) cancel(idleTimer); idleTimer = arm(() => { if (current()) reconnect(); },65000); };
    touch();
    const deadline = arm(() => { if (current() && !accepted) fail('handshake_timeout'); }, 15000);
    trace('SIGNALING_CONNECT_ATTEMPT', 'signaling');
    socket.on('open', () => {
      if (!current()) return;
      const request = {msg_type:1, protocol_version:1, meeting_uuid:meetingUuid, rtms_stream_id:streamId,
        sequence:crypto.randomInt(1, 1000000000), signature:signature(), buffer_data:false};
      observe('signaling_websocket_handshake', {direction:'OUTBOUND', channel:'signaling'}, Buffer.from(JSON.stringify(request)));
      send(socket, request);
    });
    socket.on('message', data => {
      if (!current()) return;
      touch();
      const parsed = parse(data, 'signaling'); if (!parsed) return;
      const {raw, value:m} = parsed;
      if ((m.rtms_stream_id !== undefined && m.rtms_stream_id !== streamId) ||
          (m.meeting_uuid !== undefined && m.meeting_uuid !== meetingUuid)) return fail('wrong_stream');
      if (m.msg_type === 2) {
        observe('signaling_websocket_handshake', {direction:'INBOUND',channel:'signaling'}, raw);
        if (accepted || m.status_code !== 0) return fail('handshake_rejected');
        try { stream.mediaUrl = safeZoomWebSocketUrl(m.media_server?.server_urls?.transcript ?? m.media_server?.server_urls?.all); }
        catch { return fail('invalid_endpoint'); }
        accepted = true; cancel(deadline);
        send(socket, {msg_type:5, events:[{event_type:3,subscribe:true},{event_type:4,subscribe:true}]});
        connectMedia();
      } else if (!accepted) { fail('handshake_required'); }
      else if (m.msg_type === 12) send(socket,{msg_type:13,timestamp:m.timestamp});
      else if (m.msg_type === 22) {
        if (!stopping || acknowledged || m.rtms_stream_id !== streamId || m.status_code !== 0) return fail('stop_rejected');
        acknowledged = true; event('stop_ack'); finishDrain();
      } else if (m.msg_type === 6) {
        const e = m.event;
        if (e?.event_type === 3 || e?.event_type === 4) {
          observe('participant_lifecycle_events',{direction:'INBOUND',channel:'signaling'},raw);
          if (!Array.isArray(e.participants) || e.participants.length > 2) return fail('invalid_participant');
          for (const p of e.participants) {
            if (!Number.isInteger(p.user_id) || p.user_id < 1 || p.user_id > 0xffffffff) return fail('invalid_participant');
            event(e.event_type === 3 ? 'participant' : 'participant_left',{user_id:p.user_id});
          }
        } else if (e?.event_type === 7) reconnectMedia();
      } else if (m.msg_type === 8 && m.state === 2) {
        if (m.reason === 14) reconnectMedia(); else fail('provider_interrupted');
      } else if (m.msg_type === 9) observe('participant_lifecycle_events',{direction:'INBOUND',channel:'signaling'},raw);
    });
    socket.on('close', code => {
      if (!current()) return;
      cancel(idleTimer); trace('SIGNALING_CLOSED','signaling',{close_code:code});
      stream.signalingSocket = null;
      if (!stopping) reconnect(); else if (!acknowledged) fail('stop_unacknowledged');
    });
    socket.on('error', () => { if (current()) fail('socket_error'); });
  }
  function connectMedia() {
    if (stream.stopped || stopping || !stream.mediaUrl || stream.mediaSocket) return;
    const ownEpoch = epoch, ownMediaEpoch = ++mediaEpoch;
    let socket;
    try { socket = socketFactory(stream.mediaUrl, {maxPayload:MAX_PACKET_BYTES,perMessageDeflate:false,handshakeTimeout:10000,followRedirects:false}); }
    catch { fail('socket_connect'); return; }
    stream.mediaSocket = socket; closedNormally = false; listening = false;
    const current = () => !stream.stopped && epoch === ownEpoch && mediaEpoch === ownMediaEpoch && stream.mediaSocket === socket;
    let accepted = false;
    let idleTimer;
    const touch = () => { if (idleTimer) cancel(idleTimer); idleTimer = arm(() => { if (current()) reconnectMedia(); },65000); };
    touch();
    const deadline = arm(() => { if (current() && !accepted) fail('handshake_timeout'); },15000);
    socket.on('open', () => {
      if (!current()) return;
      const request = {msg_type:3,protocol_version:1,meeting_uuid:meetingUuid,rtms_stream_id:streamId,
        signature:signature(),media_type:8,payload_encryption:false};
      observe('transcript_websocket_handshake',{direction:'OUTBOUND',channel:'transcript'},Buffer.from(JSON.stringify(request)));
      send(socket,request);
    });
    socket.on('message', data => {
      if (!current()) return;
      touch();
      const parsed = parse(data,'transcript'); if (!parsed) return;
      const {raw,value:m} = parsed;
      if ((m.rtms_stream_id !== undefined && m.rtms_stream_id !== streamId) ||
          (m.meeting_uuid !== undefined && m.meeting_uuid !== meetingUuid)) return fail('wrong_stream');
      if (m.msg_type === 4) {
        observe('transcript_websocket_handshake',{direction:'INBOUND',channel:'transcript'},raw);
        if (accepted || stopping || m.status_code !== 0) return fail('handshake_rejected');
        accepted = true; cancel(deadline);
        if (!send(stream.signalingSocket,{msg_type:7,rtms_stream_id:streamId})) return;
        stream.reconnectPending = false; listening = true; event('listening');
      } else if (!accepted) fail('handshake_required');
      else if (m.msg_type === 12) send(socket,{msg_type:13,timestamp:m.timestamp});
      else if (m.msg_type === 17) {
        if (++stream.transcriptCount > 256) return fail('capture_limit');
        observe('transcript_packets',{direction:'INBOUND',channel:'transcript',ordinal:stream.transcriptCount},raw);
        event('transcript',{packet_base64:raw.toString('base64')});
        if (chaosDelayMs !== null && !stream.chaosInjected) {
          stream.chaosInjected = true;
          arm(() => { if (current() && !stopping) { trace('CONTROLLED_MEDIA_DISCONNECT_AFTER_FIRST_TRANSCRIPT','transcript'); close(socket); } },chaosDelayMs);
        }
      } else fail('unexpected_media');
    });
    socket.on('close', code => {
      if (!current()) return;
      cancel(idleTimer); trace('TRANSCRIPT_SOCKET_CLOSED','transcript',{close_code:code}); stream.mediaSocket = null;
      if (stopping) { if (code !== 1000) return fail('drain_uncertain'); closedNormally = true; finishDrain(); }
      else reconnectMedia();
    });
    socket.on('error', () => { if (current()) fail('socket_error'); });
  }
  function reconnectMedia() {
    if (stream.stopped || stream.reconnectPending) return;
    if (stopping) return fail('drain_uncertain');
    if (++retries > 3) return fail('reconnect_limit');
    stream.reconnectPending = true; listening = false; mediaEpoch++;
    close(stream.mediaSocket); stream.mediaSocket = null;
    event('interrupted'); event('reconnecting');
    const scheduledEpoch = epoch, scheduledMediaEpoch = mediaEpoch;
    arm(() => { if (!stream.stopped && !stopping && epoch === scheduledEpoch && mediaEpoch === scheduledMediaEpoch) connectMedia(); },3000);
  }
  function reconnect(url = stream.serverUrl) {
    if (stream.stopped) return;
    if (stopping) return fail('drain_uncertain');
    if (++retries > 3) return fail('reconnect_limit');
    try { stream.serverUrl = safeZoomWebSocketUrl(url); } catch { return fail('invalid_endpoint'); }
    epoch++; mediaEpoch++; close(stream.mediaSocket); close(stream.signalingSocket);
    stream.mediaSocket = stream.signalingSocket = null; stream.reconnectPending = true; listening = false;
    event('interrupted'); event('reconnecting');
    const scheduledEpoch = epoch;
    arm(() => { if (epoch === scheduledEpoch) connectSignaling(); },3000);
  }
  function stop() {
    if (stream.stopped || stopping) return;
    if (!listening || stream.reconnectPending) return fail('stop_unavailable');
    stopping = true;
    if (!send(stream.signalingSocket,{msg_type:21,rtms_stream_id:streamId})) return;
    arm(() => fail(acknowledged ? 'drain_timeout' : 'stop_timeout'),15000);
  }
  function providerStopped() { if (!acknowledged) fail('stop_unacknowledged'); }
  arm(() => fail('capture_timeout'),15 * 60 * 1000);
  return Object.assign(stream,{start:connectSignaling,reconnect,reconnectMedia,stop,revoke,providerStopped});
}
