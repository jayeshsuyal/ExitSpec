import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {createRtmsTransport,safeZoomWebSocketUrl,MAX_PACKET_BYTES} from './rtms-transport.mjs';
import {readFileSync} from 'node:fs';
class Socket extends EventEmitter {
  readyState=1; bufferedAmount=0; sent=[]; terminated=false;
  send(value) {this.sent.push(JSON.parse(value));}
  terminate() {this.terminated=true;this.readyState=3;}
  msg(value) {this.emit('message',Buffer.isBuffer(value)?value:Buffer.from(JSON.stringify(value)));}
}
function setup(extra={}) {
  const sockets=[], events=[], observations=[], jobs=new Map(); let serial=0;
  const timers={setTimeout(fn,ms){jobs.set(++serial,{fn,ms});return serial;},clearTimeout(id){jobs.delete(id);}};
  const stream=createRtmsTransport({clientId:'client-test',clientSecret:'secret-for-synthetic-tests',meetingUuid:'meeting-test',streamId:'stream-test',serverUrl:'wss://rtms.zoom.us',
    socketFactory:(url,options)=>{assert.equal(options.maxPayload,MAX_PACKET_BYTES);assert.equal(options.followRedirects,false);const socket=new Socket();sockets.push(socket);return socket;},
    onEvent:e=>events.push(e),timers,...extra});
  const tick=ms=>{for(const [id,job] of [...jobs])if(job.ms===ms){jobs.delete(id);job.fn();}};
  function start(){stream.start();sockets.at(-1).emit('open');sockets.at(-1).msg({msg_type:2,status_code:0,media_server:{server_urls:{transcript:'wss://media.zoom.us'}}});sockets.at(-1).emit('open');sockets.at(-1).msg({msg_type:4,status_code:0});}
  return {stream,sockets,events,observations,tick,start};
}
const packet=Buffer.from('{"msg_type":17,"content":{"user_id":17,"data":"Latency below 37 ms","start_time":1,"end_time":2,"timestamp":3,"language":9}}');
test('memory transport preserves exact native bytes; subscribes participants; never injects chaos',()=>{
  const f=setup();f.start();
  assert.deepEqual(f.sockets[0].sent[1],{msg_type:5,events:[{event_type:3,subscribe:true},{event_type:4,subscribe:true}]});
  f.sockets[0].msg({msg_type:6,event:{event_type:3,participants:[{user_id:17},{user_id:18}]}});
  f.sockets[1].msg(packet);f.tick(3000);
  assert.deepEqual(Buffer.from(f.events.at(-1).packet_base64,'base64'),packet);
  assert.equal(f.sockets[1].terminated,false);assert.equal(f.stream.chaosInjected,false);
  assert.equal(f.events.filter(e=>e.event==='participant').length,2);
  f.sockets[0].msg({msg_type:6,event:{event_type:4,participants:[{user_id:17}]}});
  assert.deepEqual(f.events.at(-1),{event:'participant_left',user_id:17});f.stream.revoke();
});
test('diagnostic hooks record opaque packets and opt-in chaos only; harness shares transport',()=>{
  const observations=[]; const f=setup({observe:(...args)=>observations.push(args),chaosDelayMs:3000});f.start();f.sockets[1].msg(packet);
  assert.deepEqual(observations.find(o=>o[0]==='transcript_packets')[2],packet);f.tick(3000);assert.equal(f.sockets[1].terminated,true);
  const harness=readFileSync(new URL('./capture-harness.mjs',import.meta.url),'utf8');
  assert.match(harness,/createRtmsTransport\(/);assert.doesNotMatch(harness,/new WebSocket\(/);f.stream.revoke();
});
test('stop requires matching provider ACK and normal media close; accepts pending media before drain',()=>{
  const f=setup();f.start();f.stream.stop();assert.deepEqual(f.sockets[0].sent.at(-1),{msg_type:21,rtms_stream_id:'stream-test'});
  f.sockets[0].msg({msg_type:22,rtms_stream_id:'stream-test',status_code:0});
  assert.equal(f.events.at(-1).event,'stop_ack');assert.equal(f.stream.stopped,false);
  f.sockets[1].msg(packet);f.sockets[1].emit('close',1000);
  assert.deepEqual(f.events.slice(-3).map(e=>e.event),['stop_ack','transcript','drained']);assert.equal(f.stream.stopped,true);
  f.sockets[1].msg(packet);assert.equal(f.events.at(-1).event,'drained');
});
test('normal media close before ACK remains pending until matching ACK',()=>{
  const f=setup();f.start();f.stream.stop();f.sockets[1].emit('close',1000);assert.equal(f.events.at(-1).event,'listening');
  f.sockets[0].msg({msg_type:22,rtms_stream_id:'stream-test',status_code:0});assert.equal(f.events.at(-1).event,'drained');
});
for(const [name,trigger,code] of [
  ['provider stop alone',f=>f.stream.providerStopped(),'stop_unacknowledged'],
  ['wrong stop stream',f=>{f.stream.stop();f.sockets[0].msg({msg_type:22,rtms_stream_id:'other',status_code:0});},'wrong_stream'],
  ['stop timeout',f=>{f.stream.stop();f.tick(15000);},'stop_timeout'],
  ['drain timeout',f=>{f.stream.stop();f.sockets[0].msg({msg_type:22,rtms_stream_id:'stream-test',status_code:0});f.tick(15000);},'drain_timeout'],
  ['abnormal drain',f=>{f.stream.stop();f.sockets[1].emit('close',1006);},'drain_uncertain'],
  ['packet overflow',f=>f.sockets[1].msg(Buffer.alloc(MAX_PACKET_BYTES+1)),'invalid_packet'],
  ['audio rejected',f=>f.sockets[1].msg({msg_type:14,content:{data:'opaque'}}),'unexpected_media'],
  ['third participant',f=>f.sockets[0].msg({msg_type:6,event:{event_type:3,participants:[{user_id:1},{user_id:2},{user_id:3}]}}),'invalid_participant'],
]) test(name+' fails closed',()=>{const f=setup();f.start();trigger(f);assert.deepEqual(f.events.at(-1),{event:'failed',code});assert.equal(f.stream.stopped,true);});
test('reconnect invalidates stale socket callbacks and has retry limit',()=>{
  const f=setup();f.start();const old=f.sockets[1];f.stream.reconnectMedia();old.msg(packet);assert.equal(f.stream.transcriptCount,0);
  f.tick(3000);const next=f.sockets.at(-1);next.emit('open');next.msg({msg_type:4,status_code:0});next.msg(packet);assert.equal(f.stream.transcriptCount,1);
  for(let i=0;i<3;i++){f.stream.reconnect();f.tick(3000);}
  assert.deepEqual(f.events.at(-1),{event:'failed',code:'reconnect_limit'});
});
test('revoke cancels scheduled reconnect and packets',()=>{const f=setup();f.start();f.stream.reconnectMedia();f.stream.revoke();f.tick(3000);f.sockets[1].msg(packet);assert.equal(f.sockets.length,2);assert.equal(f.stream.transcriptCount,0);});
test('endpoint pins reject credentials, custom ports, deceptive hosts and redirects',()=>{
  for(const url of ['ws://rtms.zoom.us','wss://zoom.us.evil.test','wss://user:secret@rtms.zoom.us','wss://rtms.zoom.us:444','wss://rtms.zoom.us/#fragment','wss://zoom.us']) assert.throws(()=>safeZoomWebSocketUrl(url));
  assert.equal(safeZoomWebSocketUrl('wss://rtms.zoom.us:443/path'),'wss://rtms.zoom.us/path');
});
test('full reconnect invalidates already scheduled media-only reconnect',()=>{
  const f=setup();f.start();f.stream.reconnectMedia();f.stream.reconnect();f.tick(3000);
  // Only a new signaling socket may be opened until its handshake succeeds.
  assert.equal(f.sockets.length,3);assert.equal(f.stream.mediaSocket,null);f.stream.revoke();
});
test('silent socket becomes interrupted after bounded 65 second liveness window',()=>{
  const f=setup();f.start();f.tick(65000);assert.deepEqual(f.events.slice(-2).map(e=>e.event),['interrupted','reconnecting']);assert.equal(f.stream.reconnectPending,true);f.stream.revoke();
});
