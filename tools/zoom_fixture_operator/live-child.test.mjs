import test from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {EventEmitter} from 'node:events';
import {createLiveChild,FrameReader,encodeFrame,MAX_FRAME_BYTES} from './live-child.mjs';
const init={command:'init',generation:'a'.repeat(64),clientId:'synthetic-client',clientSecret:'synthetic-client-secret',webhookSecret:'synthetic-webhook-secret',expectedMeetingUuid:'meeting-test',callbackPort:32145,callbackHost:'callback.example.test',callbackPath:'/zoom-webhook/'+'b'.repeat(24)};
function fixture(){
  const emitted=[],calls=[],jobs=new Map();let options,closed=false;
  const server=new EventEmitter();server.listen=(port,host)=>calls.push(['listen',port,host]);server.close=()=>{};
  const transport={start:()=>calls.push('start'),stop:()=>calls.push('stop'),revoke:()=>calls.push('revoke'),reconnect:url=>calls.push(['reconnect',url]),providerStopped:()=>calls.push('providerStopped')};
  const child=createLiveChild({emit:e=>emitted.push(e),serverFactory:()=>server,transportFactory:o=>{options=o;return transport;},now:()=>1000000000000,
    timers:{setTimeout(fn,ms){jobs.set(ms,fn);return ms;},clearTimeout(ms){jobs.delete(ms);}},onClose:()=>{closed=true;}});
  function webhook(event='meeting.rtms_started',extra={}){
    const body=Buffer.from(JSON.stringify({event,payload:{meeting_uuid:'meeting-test',rtms_stream_id:'stream-test',server_urls:'wss://rtms.zoom.us',...extra}}));
    const timestamp='1000000000', signature='v0='+crypto.createHmac('sha256',init.webhookSecret).update(`v0:${timestamp}:${body.toString()}`).digest('hex');
    return {body,headers:{'x-zm-request-timestamp':timestamp,'x-zm-signature':signature}};
  }
  const deliver=(...args)=>{const {body,headers}=webhook(...args);return child.webhook(body,headers);};
  const bind=()=>child.command({command:'bind',generation:init.generation,stream_id:'stream-test'});
  return {child,emitted,calls,jobs,webhook,deliver,bind,get options(){return options;},get closed(){return closed;}};
}
test('trusted init alone listens loopback; signed exact offer still requires parent bind',()=>{
  const f=fixture();f.child.command(init);assert.deepEqual(f.calls,[['listen',32145,'127.0.0.1']]);
  assert.equal(f.deliver().status,200);assert.equal(f.calls.length,1);assert.deepEqual(f.emitted[0],{event:'offer',generation:init.generation,seq:1,stream_id:'stream-test'});
  f.bind();assert.equal(f.calls.at(-1),'start');assert.equal(f.options.observe,undefined);assert.equal(f.options.chaosDelayMs,undefined);
  f.options.onEvent({event:'listening'});assert.equal(f.emitted.at(-1).seq,2);assert.equal(f.emitted.at(-1).stream_id,'stream-test');
  f.child.command({command:'stop',generation:init.generation,stream_id:'stream-test'});assert.equal(f.calls.at(-1),'stop');f.child.close();
});
test('signature, wrong meeting, wrong generation and replay do not start transport',()=>{
  const f=fixture();f.child.command(init);const {body,headers}=f.webhook();
  assert.equal(f.child.webhook(body,{...headers,'x-zm-signature':'v0='+'0'.repeat(64)}).status,401);
  assert.equal(f.deliver('meeting.rtms_started',{meeting_uuid:'other'}).status,403);
  f.deliver();f.deliver();assert.equal(f.emitted.length,1);
  f.child.command({command:'bind',generation:'c'.repeat(64),stream_id:'stream-test'});assert.equal(f.closed,true);assert.equal(f.calls.includes('start'),false);
});
test('second stream revokes bound offer',()=>{
  const f=fixture();f.child.command(init);f.deliver();f.bind();assert.equal(f.deliver('meeting.rtms_started',{rtms_stream_id:'other'}).status,409);
  assert.equal(f.emitted.at(-1).code,'replaced_stream');assert.equal(f.closed,true);
});
test('stop webhook carries no drain inference; ended unbound offer cannot be bound',()=>{
  const f=fixture();f.child.command(init);f.deliver();f.bind();f.deliver('meeting.rtms_stopped');assert.equal(f.calls.at(-1),'providerStopped');assert.equal(f.emitted.at(-1).event,'offer');f.child.close();
  const g=fixture();g.child.command(init);g.deliver();g.deliver('meeting.rtms_stopped');g.bind();assert.equal(g.closed,true);assert.equal(g.calls.includes('start'),false);
});
test('terminal event revokes runtime and stale callback cannot emit',()=>{
  const f=fixture();f.child.command(init);f.deliver();f.bind();f.options.onEvent({event:'failed',code:'stop_timeout'});
  const count=f.emitted.length;f.options.onEvent({event:'transcript',packet_base64:'secret'});assert.equal(f.emitted.length,count);assert.equal(f.closed,true);
});
test('bounded frame reader supports fragmented headers and joined frames',()=>{
  const received=[];let errors=0;const reader=new FrameReader(v=>received.push(v),()=>errors++);
  const buffer=Buffer.concat([encodeFrame({command:'one'}),encodeFrame({command:'two'})]);
  for(const byte of buffer) reader.push(Buffer.from([byte]));assert.deepEqual(received,[{command:'one'},{command:'two'}]);assert.equal(errors,0);
});
test('frame reader rejects oversized length before allocating body and ignores further input',()=>{
  let errors=0;const reader=new FrameReader(()=>assert.fail(),()=>errors++);const header=Buffer.alloc(4);header.writeUInt32BE(MAX_FRAME_BYTES+1);reader.push(header);
  assert.equal(reader.body,null);reader.push(Buffer.alloc(100));assert.equal(errors,1);
});
test('frame reader rejects malformed JSON, non-object and invalid UTF-8',()=>{
  for(const body of [Buffer.from('['),Buffer.from('[]'),Buffer.from([0xff])]) {
    let errors=0;const reader=new FrameReader(()=>assert.fail(),()=>errors++);const header=Buffer.alloc(4);header.writeUInt32BE(body.length);reader.push(Buffer.concat([header,body]));assert.equal(errors,1);
  }
});
test('HTTP rejects Host, Origin, oversized declared content before consuming body',()=>{
  const f=fixture();f.child.command(init);
  for(const headers of [{host:'evil.test'}, {host:init.callbackHost,origin:'https://evil.test'},{host:init.callbackHost,'content-type':'application/json','content-length':'999999'}]){
    const req=new EventEmitter();req.method='POST';req.url=init.callbackPath;req.headers=headers;
    let status;f.child.request(req,{writeHead(code){status=code;},end(){}});assert.ok([403,413].includes(status));assert.equal(req.listenerCount('data'),0);
  }f.child.close();
});
test('HTTP streamed overflow bounded and no webhook processed',()=>{
  const f=fixture();f.child.command(init);const req=new EventEmitter();Object.assign(req,{method:'POST',url:init.callbackPath,headers:{host:init.callbackHost,'content-type':'application/json'},destroy(){this.destroyed=true;}});
  let status;f.child.request(req,{writeHead(code){status=code;},end(){}});req.emit('data',Buffer.alloc(65536));req.emit('data',Buffer.alloc(1));assert.equal(status,413);assert.equal(req.destroyed,true);assert.equal(f.emitted.length,0);f.child.close();
});
test('webhook replay store bounded and expiry revokes binding',()=>{
  const f=fixture();f.child.command(init);
  for(let i=0;i<256;i++)f.deliver('meeting.rtms_started',{meeting_uuid:'wrong'+i});
  assert.equal(f.deliver().status,429);assert.equal(f.closed,true);
  const g=fixture();g.child.command(init);g.jobs.get(900000)();assert.equal(g.emitted.at(-1).code,'capture_timeout');assert.equal(g.closed,true);
});
test('untrusted invalid pipe init exits without network or content on stdout/stderr',async()=>{
  const {spawn}=await import('node:child_process');
  const child=spawn(process.execPath,[fileURLToPath(new URL('./live-child.mjs',import.meta.url))],{env:{PATH:process.env.PATH},stdio:['pipe','pipe','pipe']});
  const stdout=[],stderr=[];child.stdout.on('data',b=>stdout.push(b));child.stderr.on('data',b=>stderr.push(b));
  child.stdin.end(encodeFrame({command:'init',clientSecret:'MUST-NOT-LOG',callbackHost:'evil'}));
  const code=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>{child.kill();reject(new Error('exit timeout'));},3000);child.on('exit',code=>{clearTimeout(timer);resolve(code);});child.on('error',reject);});
  assert.equal(code,0);assert.equal(Buffer.concat(stdout).length,0);assert.equal(Buffer.concat(stderr).length,0);
});
test('callback error before offer emits explicitly null stream with safe code',()=>{
  const emitted=[];const server=new EventEmitter();server.listen=()=>server.emit('error',new Error('DO-NOT-LOG-CREDENTIAL'));server.close=()=>{};
  const child=createLiveChild({emit:e=>emitted.push(e),serverFactory:()=>server});child.command(init);
  assert.deepEqual(emitted,[{event:'failed',code:'callback_unavailable',generation:init.generation,seq:1,stream_id:null}]);
});
test('SIGTERM shuts idle runner without logging framed native content',async()=>{
  const {spawn}=await import('node:child_process');
  const child=spawn(process.execPath,[fileURLToPath(new URL('./live-child.mjs',import.meta.url))],{env:{PATH:process.env.PATH},stdio:['pipe','pipe','pipe']});
  const out=[],err=[];child.stdout.on('data',b=>out.push(b));child.stderr.on('data',b=>err.push(b));
  // A partial frame contains opaque content but cannot initialize any networking.
  const frame=encodeFrame({private_transcript:'SYNTHETIC-PRIVATE-CONTENT',clientSecret:'DO-NOT-LOG'});child.stdin.write(frame.subarray(0,frame.length-1));
  const exit=new Promise((resolve,reject)=>{const deadline=setTimeout(()=>{child.kill('SIGKILL');reject(new Error('exit timeout'));},3000);child.on('exit',()=>{clearTimeout(deadline);resolve();});child.on('error',reject);});
  child.kill('SIGTERM');await exit;assert.equal(Buffer.concat(out).length,0);assert.equal(Buffer.concat(err).length,0);
});
