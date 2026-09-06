import crypto from 'node:crypto';
import http from 'node:http';
import {pathToFileURL} from 'node:url';
import {createRtmsTransport, safeZoomWebSocketUrl} from './rtms-transport.mjs';
import {computeEndpointValidationResponse, verifyZoomWebhookSignature} from './operator-capture-lib.mjs';

export const MAX_FRAME_BYTES = 128 * 1024;
const MAX_WEBHOOK_BYTES = 64 * 1024;
export function encodeFrame(value) {
  const body = Buffer.from(JSON.stringify(value));
  if (!body.length || body.length > MAX_FRAME_BYTES) throw new Error('frame');
  const header = Buffer.alloc(4); header.writeUInt32BE(body.length);
  return Buffer.concat([header,body]);
}
// Allocate only after the four-byte length has been checked, even for fragmented input.
export class FrameReader {
  constructor(onFrame, onError) { this.onFrame=onFrame; this.onError=onError; this.header=Buffer.alloc(4); this.used=0; this.body=null; this.dead=false; }
  push(chunk) {
    if (this.dead) return;
    let offset=0;
    try {
      while (offset<chunk.length && !this.dead) {
        const target=this.body ?? this.header;
        const count=Math.min(target.length-this.used,chunk.length-offset);
        chunk.copy(target,this.used,offset); offset+=count; this.used+=count;
        if (this.used!==target.length) continue;
        this.used=0;
        if (!this.body) {
          const length=this.header.readUInt32BE();
          if (!length || length>MAX_FRAME_BYTES) throw new Error();
          this.body=Buffer.alloc(length);
        } else {
          const value=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(this.body)); this.body=null;
          if (!value || typeof value!=='object' || Array.isArray(value)) throw new Error();
          this.onFrame(value);
        }
      }
    } catch { this.dead=true; this.body=null; this.onError(); }
  }
  end() { if (!this.dead) {this.dead=true;this.body=null;this.onError();} }
}
const boundedString=(value,min,max) => typeof value==='string' && value.length>=min && value.length<=max && !/[\u0000\r\n]/.test(value);
function validInit(m) {
  return m.command==='init' && /^[a-f0-9]{64}$/.test(m.generation) &&
    boundedString(m.clientId,8,512) && boundedString(m.clientSecret,16,1024) &&
    boundedString(m.webhookSecret,16,1024) && boundedString(m.expectedMeetingUuid,1,256) &&
    Number.isInteger(m.callbackPort) && m.callbackPort>=1024 && m.callbackPort<=65535 &&
    boundedString(m.callbackHost,1,255) && /^[a-z0-9.-]+(?::[0-9]{1,5})?$/.test(m.callbackHost) &&
    typeof m.callbackPath==='string' && /^\/zoom-webhook\/[a-z0-9_-]{24,96}$/.test(m.callbackPath);
}
export function createLiveChild({emit, transportFactory=createRtmsTransport, serverFactory=http.createServer,
  now=Date.now, timers={setTimeout,clearTimeout}, onClose=()=>{}}) {
  let config=null, offer=null, transport=null, server=null, terminal=false, seq=0, requests=0;
  const seen=new Set(); let expiry=null;
  function close() {
    if (terminal) return;
    terminal=true; if (expiry) timers.clearTimeout(expiry);
    transport?.revoke(); seen.clear(); config=null; offer=null;
    server?.closeAllConnections?.(); server?.close(); onClose();
  }
  function event(e) {
    if (terminal || !config) return;
    if (++seq>1024) { fail('event_limit'); return; }
    const envelope={...e,generation:config.generation,seq,stream_id:offer?.streamId??null};
    if (offer) envelope.stream_id=offer.streamId;
    emit(envelope);
    if (e.event==='failed' || e.event==='drained') close();
  }
  function fail(code) {
    if (terminal) return;
    if (config) emit({event:'failed',code,generation:config.generation,seq:++seq,stream_id:offer?.streamId??null});
    close();
  }
  function command(m) {
    if (terminal) return;
    if (!config) {
      if (!validInit(m)) return fail('invalid_init');
      config={...m};
      expiry=timers.setTimeout(()=>fail('capture_timeout'),15*60*1000);
      try {
        server=serverFactory(request);
        server.requestTimeout=5000; server.headersTimeout=5000; server.keepAliveTimeout=1000; server.maxHeadersCount=24; server.maxConnections=4;
        server.on('error',()=>fail('callback_unavailable'));
        server.listen(config.callbackPort,'127.0.0.1');
      } catch { fail('callback_unavailable'); }
      return;
    }
    if (m.generation!==config.generation) return fail('invalid_command');
    if (m.command==='revoke') return close();
    if (!offer || m.stream_id!==offer.streamId) return fail('invalid_command');
    if (m.command==='bind' && !transport) {
      try {
        transport=transportFactory({clientId:config.clientId,clientSecret:config.clientSecret,networkAuthorized:true,
          meetingUuid:config.expectedMeetingUuid,streamId:offer.streamId,serverUrl:offer.serverUrl,onEvent:event});
        transport.start();
      } catch { fail('transport_failed'); }
    } else if (m.command==='stop' && transport) transport.stop();
    else fail('invalid_command');
  }
  function webhook(body,headers) {
    if (terminal || !config) return {status:410};
    if (body.length>MAX_WEBHOOK_BYTES) return {status:413};
    if (!verifyZoomWebhookSignature({secretToken:config.webhookSecret,timestamp:String(headers['x-zm-request-timestamp']??''),
      signature:String(headers['x-zm-signature']??''),rawBody:body,nowMs:now()})) return {status:401};
    let payload;
    try { payload=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(body)); } catch { return {status:400}; }
    const digest=crypto.createHash('sha256').update(body).digest('hex');
    if (seen.has(digest)) return {status:200};
    if (seen.size>=256) { fail('webhook_limit'); return {status:429}; }
    seen.add(digest);
    if (payload?.event==='endpoint.url_validation') {
      try { return {status:200,body:computeEndpointValidationResponse(config.webhookSecret,payload.payload?.plainToken)}; }
      catch { return {status:400}; }
    }
    const p=payload?.payload;
    if (!p || p.meeting_uuid!==config.expectedMeetingUuid || !boundedString(p.rtms_stream_id,1,256)) return {status:403};
    if (payload.event==='meeting.rtms_started') {
      if (offer) {
        if (p.rtms_stream_id!==offer.streamId) {fail('replaced_stream');return {status:409};}
        return {status:200};
      }
      try { offer={streamId:p.rtms_stream_id,serverUrl:safeZoomWebSocketUrl(p.server_urls)}; } catch { return {status:422}; }
      event({event:'offer'});
    } else if (offer && p.rtms_stream_id===offer.streamId) {
      if (!transport) {
        if (payload.event==='meeting.rtms_stopped' || payload.event==='meeting.rtms_interrupted') fail('offer_ended');
        return {status:409};
      }
      if (payload.event==='meeting.rtms_stopped') transport.providerStopped();
      else if (payload.event==='meeting.rtms_interrupted') transport.reconnect(p.server_urls??offer.serverUrl);
      else return {status:422};
    } else return {status:409};
    return {status:200};
  }
  function request(req,res) {
    const respond=(status,body={ok:status===200}) => {res.writeHead(status,{'Content-Type':'application/json','Connection':'close'});res.end(JSON.stringify(body));};
    if (terminal || !config) return respond(410);
    if (++requests>512) {respond(429); fail('webhook_limit'); return;}
    if (req.method!=='POST' || req.url!==config.callbackPath || req.headers.host!==config.callbackHost || req.headers.origin!==undefined) return respond(403);
    if (req.headers['content-type']!=='application/json' ||
      (req.headers['content-length']!==undefined && (!/^\d+$/.test(req.headers['content-length']) || Number(req.headers['content-length'])>MAX_WEBHOOK_BYTES))) return respond(413);
    let size=0; const chunks=[]; let rejected=false;
    const deadline=timers.setTimeout(()=>{rejected=true;req.destroy();},5000);
    req.on('data',chunk=>{
      if (rejected) return;
      size+=chunk.length;
      if (size>MAX_WEBHOOK_BYTES) {rejected=true;timers.clearTimeout(deadline);chunks.length=0;respond(413);req.destroy();return;}
      chunks.push(chunk);
    });
    req.on('end',()=>{
      timers.clearTimeout(deadline); if (rejected) return;
      try {const result=webhook(Buffer.concat(chunks),req.headers);respond(result.status,result.body);} catch {respond(400);}
    });
    req.on('error',()=>{timers.clearTimeout(deadline);chunks.length=0;});
    req.on('close',()=>timers.clearTimeout(deadline));
  }
  return {command,webhook,request,fail,close};
}
export function run(input=process.stdin,output=process.stdout) {
  let runtime;
  runtime=createLiveChild({emit:value=>{
    const frame=encodeFrame(value);
    // Never grow a queue behind a slow/dead parent. Failure is observable as EOF.
    if (output.writableLength+frame.length>256*1024) {runtime.close();output.destroy();return;}
    output.write(frame);
  },onClose:()=>{input.destroy();output.end();}});
  const reader=new FrameReader(value=>runtime.command(value),()=>runtime.fail('invalid_frame'));
  input.on('data',chunk=>reader.push(chunk)); input.on('end',()=>reader.end());
  input.on('error',()=>runtime.close()); output.on('error',()=>runtime.close());
  // Intentionally no stderr diagnostics: exceptions can contain provider content.
  process.on('uncaughtException',()=>runtime.fail('runtime_failed'));
  process.on('unhandledRejection',()=>runtime.fail('runtime_failed'));
  process.on('SIGTERM',()=>runtime.close());
}
if (process.argv[1] && import.meta.url===pathToFileURL(process.argv[1]).href) run();
