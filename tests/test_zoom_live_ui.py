"""Exercise the isolated live panel using the actual JS and a deterministic DOM/network."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "exitspec" / "static"
HARNESS = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const source = require('node:fs').readFileSync(process.argv[1], 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));
const session = 'zoomsess_' + 'a'.repeat(64);
function state(name = 'PAIRED', extra = {}) {
  return {schema_version:'exitspec.zoom-live/1.0', poc_id:'poc_demo',
    session_id:session, state:name, transport_mode:'LIVE_ZOOM_RTMS',
    provider_connected:name === 'LISTENING',
    source_content_classification:'SYNTHETIC_REQUIREMENTS_ONLY',
    capture_scope:'BOUNDED_WINDOW_NOT_COMPLETE_MEETING',
    segment_count:0, proposal_count:0, source_receipt_id:null,
    review_url:null, failure_code:null, ...extra};
}
function app(first, hidden = false, enabled = true) {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, {hidden:false, disabled:false, dataset:{},
      checked:false, textContent:'', attrs:{}, listeners:{},
      addEventListener(event, listener) { this.listeners[event] = listener; },
      removeAttribute(name) { delete this.attrs[name]; },
      setAttribute(name, value) { this.attrs[name] = value; }});
    return elements.get(id);
  }
  element('meeting-entry').hidden = hidden;
  element('zoom-live-panel').hidden = true;
  element('zoom-live-panel').dataset.zoomLiveEnabled = enabled ? 'true' : 'false';
  const timers = new Map(), calls = [], replies = [first], events = {};
  let sequence = 0, changed;
  const window = {location:{pathname:'/app/pocs/poc_demo/capture'},
    setTimeout(fn, delay) { const id = ++sequence; timers.set(id, {fn, delay}); return id; },
    clearTimeout(id) { timers.delete(id); },
    addEventListener(event, handler) { events[event] = handler; }};
  const context = {window, document:{getElementById:element},
    crypto:require('node:crypto').webcrypto, AbortController,
    MutationObserver:class { constructor(fn) { changed = fn; } observe() {} },
    fetch:async (url, options) => {
      calls.push({url, options});
      let reply = replies.shift();
      if (typeof reply === 'function') reply = await reply();
      if (reply instanceof Error) throw reply;
      return {ok:true, headers:{get:()=>'application/json'},
        text:async()=>JSON.stringify(reply)};
    }};
  vm.runInNewContext(source, context);
  return {element:(name)=>element('zoom-live-'+name), calls, replies, timers,
    poll:()=>[...timers.values()].filter(t=>t.delay===1000),
    changed, events, entry:element('meeting-entry'),
    click:async(name)=>{ element('zoom-live-'+name).listeners.click(); await flush(); },
    consent:()=>{ element('zoom-live-consent').checked=true;
      element('zoom-live-consent').listeners.change(); }};
}
(async () => {
"""


def _run(scenario: str) -> None:
    node = shutil.which("node")
    assert node is not None, "Live UI contract tests require Node.js (no skip)."
    result = subprocess.run(
        [node, "-e", HARNESS + scenario + "\n})().catch(e=>{console.error(e);process.exit(1)});", str(STATIC / "zoom_live.js")],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_panel_is_independent_of_synthetic_modes_and_discloses_scope():
    html = (STATIC / "source_intake.html").read_text()
    section = html.split('id="meeting-entry"', 1)[1].split('id="meeting-mode-chooser"', 1)[0]
    assert 'id="zoom-live-panel"' in section
    assert 'data-zoom-live-enabled="false" hidden' in section
    assert 'id="zoom-live-consent" type="checkbox" disabled' in section
    assert "not a complete meeting" in section
    assert "fresh synthetic requirements" in section
    assert "no Fireworks" in section
    assert '<script src="/zoom_live.js" defer></script>' in html
    javascript = (STATIC / "zoom_live.js").read_text()
    for forbidden in ("innerHTML", "localStorage", "sessionStorage", "console.", "/pair", "authorization", "bearer"):
        assert forbidden not in javascript


def test_disabled_server_capability_keeps_panel_hidden_and_never_fetches():
    _run(r"""
const a = app(state(), false, false); await flush();
assert.equal(a.element('panel').hidden, true);
assert.equal(a.calls.length, 0);
assert.equal(a.timers.size, 0);
assert.equal(a.changed, undefined);
assert.equal(a.element('start').listeners.click, undefined);
""")


def test_unpaired_requires_operator_and_does_not_poll_or_allow_actions():
    _run(r"""
const a = app(state('UNPAIRED', {session_id:null, transport_mode:'DISABLED'}));
await flush();
assert.equal(a.element('panel').hidden, false);
assert.match(a.element('status').textContent, /local operator/);
assert.equal(a.poll().length, 0);
for (const name of ['start','stop','process','reset']) {
  assert.equal(a.element(name).disabled, true);
  await a.click(name);
}
assert.equal(a.calls.length, 1);
a.replies.push(state()); await a.click('refresh');
assert.equal(a.poll().length, 1);
""")


def test_start_requires_consent_and_waiting_does_not_claim_listening():
    _run(r"""
const a = app(state()); await flush();
await a.click('start'); assert.equal(a.calls.length, 1);
a.consent(); assert.equal(a.element('start').disabled, false);
a.replies.push(state('WAITING')); await a.click('start');
const call = a.calls[1], body = JSON.parse(call.options.body);
assert.equal(call.url, '/api/pocs/poc_demo/zoom-live');
assert.equal(call.options.credentials, 'same-origin');
assert.equal(call.options.redirect, 'error');
assert.deepEqual(Object.keys(body).sort(), ['action','consent_acknowledged','idempotency_key','session_id']);
assert.equal(body.session_id, session);
assert.equal(body.consent_acknowledged, true);
assert.match(body.idempotency_key, /^[A-Za-z0-9_-]{8,128}$/);
assert.match(a.element('status').textContent, /Waiting for Zoom transport/);
assert.match(a.element('mode').textContent, /disconnected/);
assert.equal(a.element('stop').disabled, true);
assert.equal(a.element('consent').checked, false);
""")


def test_stop_acknowledgement_and_drain_precede_review_processing():
    _run(r"""
const a = app(state('LISTENING', {segment_count:2})); await flush();
assert.equal(a.element('stop').disabled, false);
assert.equal(a.element('process').disabled, true);
a.replies.push(state('STOP_REQUESTED', {segment_count:2})); await a.click('stop');
assert.equal(a.element('process').disabled, true);
a.replies.push(state('DRAINING', {segment_count:2})); await a.click('refresh');
assert.equal(a.element('process').disabled, true);
a.replies.push(state('CAPTURE_READY', {segment_count:2})); await a.click('refresh');
assert.equal(a.element('process').disabled, false);
assert.equal(a.poll().length, 0);
a.replies.push(state('DRAFT_READY', {segment_count:2, proposal_count:2,
  source_receipt_id:'srcpt_abcdefgh', review_url:'/app/pocs/poc_demo/review'}));
await a.click('process');
assert.equal(a.element('review').attrs.href, '/app/pocs/poc_demo/review');
assert.equal(a.element('review').hidden, false);
assert.equal(a.poll().length, 0);
""")


@pytest.mark.parametrize("mutation", [
    "poc_id:'poc_other'", "session_id:'browser-selected'", "extra:'unexpected'",
    "source_content_classification:'CUSTOMER_DATA'", "capture_scope:'COMPLETE_MEETING'",
    "segment_count:257", "segment_count:0.5", "provider_connected:'true'",
    "provider_connected:true", "state:'CAPTURE_READY'", "proposal_count:65",
    "failure_code:'<script>'", "state:'LIVE_SUCCESS'",
    "review_url:'https://attacker.example/review'",
])
def test_untrusted_snapshots_fail_closed(mutation):
    _run("const a = app(state('PAIRED', {" + mutation + "})); await flush();" + r"""
assert.match(a.element('mode').textContent, /unverified/);
assert.equal(a.element('start').disabled, true);
assert.equal(a.element('review').hidden, true);
assert.equal(a.element('review').attrs.href, undefined);
assert.equal(a.poll().length, 0);
""")


def test_fake_transport_never_uses_live_success_label_and_errors_clear_success():
    _run(r"""
const a = app(state('LISTENING', {transport_mode:'FAKE_ZOOM_RTMS'})); await flush();
assert.equal(a.element('mode').textContent, 'Simulated transport · no live Zoom connection');
a.replies.push(new Error('raw provider credential must not render'));
a.poll()[0].fn(); await flush();
assert.match(a.element('status').textContent, /No action will be retried/);
assert.doesNotMatch(a.element('status').textContent, /credential/);
assert.equal(a.element('stop').disabled, true);
assert.equal(a.poll().length, 0);
""")


def test_replaced_session_clears_consent_and_stale_mutation_reply_is_refused():
    _run(r"""
const a = app(state()); await flush(); a.consent();
a.replies.push(state('PAIRED', {session_id:'zoomsess_'+'b'.repeat(64)}));
await a.click('refresh'); assert.equal(a.element('consent').checked, false);
a.consent(); a.replies.push(state('WAITING'));
await a.click('start');
assert.match(a.element('mode').textContent, /unverified/);
assert.equal(a.poll().length, 0);
""")


def test_hidden_panel_and_page_exit_cancel_polling_and_ignore_late_responses():
    _run(r"""
const a = app(state(), true); await flush(); assert.equal(a.calls.length, 0);
a.entry.hidden=false; a.changed(); await flush(); assert.equal(a.calls.length, 1);
let resolve; a.replies.push(()=>new Promise(r=>{resolve=r}));
a.poll()[0].fn(); await flush();
a.entry.hidden=true; a.changed();
resolve(state('LISTENING')); await flush();
assert.equal(a.element('stop').disabled, true);
assert.equal(a.poll().length, 0);
a.entry.hidden=false; a.replies.push(state()); a.changed(); await flush();
a.events.pagehide(); assert.equal(a.poll().length, 0);
""")
