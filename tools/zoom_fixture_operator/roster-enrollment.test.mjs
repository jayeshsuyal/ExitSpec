import test from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import {EventEmitter} from 'node:events';
import {createLiveChild} from './live-child.mjs';
import {createRtmsTransport} from './rtms-transport.mjs';

// Actual child + transport + enrollment state machine, with no real sockets.
function setup() {
  const start=1000000000000, events=[], sockets=[], jobs=new Map(), observations=[];
  let now=start, serial=0, listens=0, closes=0;
  const timers={setTimeout(fn,ms){jobs.set(++serial,{fn,at:now+ms});return serial;},clearTimeout(id){jobs.delete(id);}};
  const advance=ms=>{now+=ms;for(const [id,j] of [...jobs])if(j.at<=now){jobs.delete(id);j.fn();}};
  class Socket extends EventEmitter {
    readyState=1;bufferedAmount=0;sent=[];terminated=false;
    send(raw){this.sent.push(JSON.parse(raw));}
    terminate(){this.terminated=true;this.readyState=3;}
    msg(value){this.emit('message',Buffer.from(JSON.stringify(value)));}
  }
  const config={command:'init',generation:'a'.repeat(64),clientId:'synthetic-client',clientSecret:'synthetic-client-secret',
    webhookSecret:'synthetic-webhook-secret',expectedMeetingUuid:'synthetic-meeting',callbackPort:32145,
    callbackHost:'callback.example.test',callbackPath:'/zoom-webhook/'+'b'.repeat(24),
    enrollment:{deadlineMs:start+30000,runDeadlineMs:start+120000}};
  const server=new EventEmitter();server.listen=()=>listens++;server.close=()=>closes++;
  const child=createLiveChild({emit:e=>events.push(e),now:()=>now,timers,serverFactory:()=>server,
    transportFactory:options=>createRtmsTransport({...options,timers,observe:(...args)=>observations.push(args),
      socketFactory:()=>{const s=new Socket();sockets.push(s);return s;}})});
  const command=(command,extra={})=>child.command({command,generation:config.generation,stream_id:'synthetic-stream',...extra});
  function begin() {
    child.command(config);
    const body=Buffer.from(JSON.stringify({event:'meeting.rtms_started',payload:{meeting_uuid:config.expectedMeetingUuid,
      rtms_stream_id:'synthetic-stream',server_urls:'wss://rtms.zoom.us'}}));
    const ts=String(now/1000), signature='v0='+crypto.createHmac('sha256',config.webhookSecret).update('v0:'+ts+':'+body).digest('hex');
    assert.equal(child.webhook(body,{'x-zm-request-timestamp':ts,'x-zm-signature':signature}).status,200);
    command('bind');sockets[0].emit('open');
    sockets[0].msg({msg_type:2,status_code:0,media_server:{server_urls:{transcript:'wss://media.zoom.us'}}});
  }
  const speaker=(id, timestamp=now)=>sockets[0].msg({msg_type:6,event:{event_type:2,user_id:id,user_name:'PRIVATE-NAME-NEVER-LOG',timestamp}});
  function confirm(nonce,id) {
    command('enrollment_arm',{nonce});advance(1);sockets[0].msg({msg_type:12,timestamp:now});
    assert.equal(events.at(-1).event,'enrollment_armed');
    advance(1);speaker(id);assert.equal(events.at(-1).event,'enrollment_candidate');
    command('enrollment_confirm',{nonce,user_id:id});assert.equal(events.at(-1).event,'enrollment_confirmed');
  }
  function seal(){confirm('1'.repeat(64),42);confirm('2'.repeat(64),43);command('enrollment_seal',{participant_ids:[42,43]});}
  return {child,config,command,events,sockets,observations,advance,begin,speaker,confirm,seal,
    get now(){return now;},get listens(){return listens;},get closes(){return closes;}};
}

test('actual child holds media through enrollment and seal, then uses the same stream after capture command',()=>{
  const f=setup();f.begin();assert.equal(f.events.at(-1).event,'enrollment_ready');assert.equal(f.sockets.length,1);
  f.seal();assert.equal(f.sockets.length,1);assert.equal(f.sockets[0].sent.some(m=>m.msg_type===7),false);
  f.command('enrollment_capture');assert.equal(f.sockets.length,2);f.sockets[1].emit('open');
  assert.equal(f.sockets[1].sent[0].sequence,0);assert.equal(f.sockets[1].sent[0].rtms_stream_id,'synthetic-stream');
  f.sockets[1].msg({msg_type:4,status_code:0});assert.equal(f.events.at(-1).event,'listening');
  assert.equal(f.sockets[0].sent.at(-1).msg_type,7);
  assert.deepEqual(f.observations,[]);assert.equal(JSON.stringify(f.events).includes('PRIVATE-NAME'),false);f.child.close();
});
test('pre-arm queued events, old heartbeat and old slot timestamps cannot satisfy a new challenge',()=>{
  const f=setup();f.begin();f.speaker(42);f.advance(5);
  f.command('enrollment_arm',{nonce:'1'.repeat(64)});
  f.speaker(42,f.now-1);f.sockets[0].msg({msg_type:12,timestamp:f.now-1});
  assert.equal(f.events.some(e=>e.event==='enrollment_armed'),false);
  f.advance(1);f.sockets[0].msg({msg_type:12,timestamp:f.now});
  f.speaker(42,f.now-1);assert.equal(f.events.some(e=>e.event==='enrollment_candidate'),false);
  f.advance(1);f.speaker(42);f.command('enrollment_confirm',{nonce:'1'.repeat(64),user_id:42});
  const old=f.now;f.command('enrollment_arm',{nonce:'2'.repeat(64)});
  f.advance(1);f.sockets[0].msg({msg_type:12,timestamp:f.now});f.speaker(43,old);
  assert.equal(f.events.filter(e=>e.event==='enrollment_candidate').length,1);
  f.advance(1);f.speaker(43);assert.equal(f.events.filter(e=>e.event==='enrollment_candidate').length,2);f.child.close();
});
for(const [name,trigger] of [
  ['capture without seal',f=>f.command('enrollment_capture')],
  ['seal without confirmations',f=>f.command('enrollment_seal',{participant_ids:[42,43]})],
  ['unarmed confirmation',f=>f.command('enrollment_confirm',{nonce:'1'.repeat(64),user_id:42})],
  ['string participant',f=>f.speaker('42')],
  ['zero participant',f=>f.speaker(0)],
  ['third participant',f=>{f.speaker(42);f.speaker(43);f.speaker(44);}],
  ['leave during enrollment',f=>f.sockets[0].msg({msg_type:6,event:{event_type:4,participants:[{user_id:42}]}})],
  ['wrong stream',f=>f.sockets[0].msg({msg_type:6,rtms_stream_id:'other',event:{event_type:2,user_id:42,timestamp:f.now}})],
  ['signaling transcript',f=>f.sockets[0].msg({msg_type:17,content:{data:'PRIVATE-TRANSCRIPT'}})],
  ['disconnect without retry',f=>{f.sockets[0].readyState=3;f.sockets[0].emit('close',1006);}],
  ['media interrupted',f=>f.sockets[0].msg({msg_type:6,event:{event_type:7}})],
  ['future timestamp',f=>f.speaker(42,f.now+1)],
  ['replayed arm nonce',f=>{f.confirm('1'.repeat(64),42);f.command('enrollment_arm',{nonce:'1'.repeat(64)});}],
  ['wrong confirmation',f=>{f.command('enrollment_arm',{nonce:'1'.repeat(64)});f.advance(1);f.sockets[0].msg({msg_type:12,timestamp:f.now});f.advance(1);f.speaker(42);f.command('enrollment_confirm',{nonce:'1'.repeat(64),user_id:43});}],
  ['ambiguous pending observation',f=>{f.command('enrollment_arm',{nonce:'1'.repeat(64)});f.advance(1);f.sockets[0].msg({msg_type:12,timestamp:f.now});f.advance(1);f.speaker(42);f.advance(1);f.speaker(43);}],
]) test('enrollment refuses '+name+' without media or raw observations',()=>{
  const f=setup();f.begin();trigger(f);assert.equal(f.events.at(-1).event,'failed');f.advance(3000);
  assert.equal(f.sockets.length,1);assert.ok(f.sockets[0].terminated || f.sockets[0].readyState===3);assert.deepEqual(f.observations,[]);
  assert.equal(JSON.stringify(f.events).includes('PRIVATE-'),false);
});
test('silence refuses at the original 30-second deadline; stale callbacks stay inert',()=>{
  const f=setup();f.begin();f.advance(30000);assert.equal(f.events.at(-1).event,'failed');
  const n=f.events.length;f.speaker(42);f.command('enrollment_capture');assert.equal(f.events.length,n);assert.equal(f.sockets.length,1);
});
test('adoption and late browser waiting cannot renew the cumulative run deadline',()=>{
  const f=setup();f.begin();f.advance(20000);f.seal();f.advance(99996);
  assert.equal(f.events.at(-1).event,'failed');f.command('enrollment_capture');assert.equal(f.sockets.length,1);
});
test('unknown participants after seal and during capture still revoke',()=>{
  for(const active of [false,true]){
    const f=setup();f.begin();f.seal();if(active)f.command('enrollment_capture');
    f.speaker(99);assert.equal(f.events.at(-1).event,'failed');assert.ok(f.sockets.every(s=>s.terminated));
  }
});
test('invalid or already expired enrollment init cannot open a callback',()=>{
  for(const change of [{deadlineMs:0},{runDeadlineMs:1000000200000},{extra:true}]){
    const f=setup();f.child.command({...f.config,enrollment:{...f.config.enrollment,...change}});
    assert.equal(f.listens,0);assert.equal(f.sockets.length,0);
  }
});
