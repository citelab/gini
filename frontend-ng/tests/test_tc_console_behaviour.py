"""The console's JavaScript must not just parse — it must DO something when you press a button.

Written after "I click Save and nothing happens." `node --check` passed, the server saved correctly
over curl, and the page was still dead in the browser. Neither guard could see it, because the
defect lived in the gap between them: `api()` did `return r.json()`, so a 500 with an empty body
rejected the promise, the caller's `await` never returned, and the click produced no request, no
error and no change. A console that fails silently is worse than one that crashes — the teacher
concludes the button missed and clicks harder.

So this drives the real page in a real DOM (jsdom), with `fetch` stubbed, and asserts on what a
teacher would actually see. The three cases are the three things a server can do: succeed, refuse,
or fall over.

Skipped when node or jsdom is unavailable — but `test_tc_console_js.py` still parses the page, so
the cheap guard never depends on this one being runnable.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_TC = Path(__file__).resolve().parents[2] / "teaching-center" / "src"
_CONSOLE = _TC / "gini_teaching_center" / "static" / "console.html"

pytestmark = pytest.mark.skipif(not _CONSOLE.exists(), reason="teaching-center not checked out")


def _jsdom_root() -> str | None:
    """jsdom is optional. Look where a developer would plausibly have installed it."""
    for base in (_TC.parent.parent / "node_modules", Path("/tmp/jsdom-dyc/node_modules")):
        if (base / "jsdom").exists():
            return str(base)
    return None


needs_jsdom = pytest.mark.skipif(
    not shutil.which("node") or _jsdom_root() is None,
    reason="needs node + jsdom (npm install jsdom)")


HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require(process.env.JSDOM_ROOT + '/jsdom');
const html = fs.readFileSync(process.env.CONSOLE, 'utf8');
const SAVE = JSON.parse(process.env.SAVE_RESPONSE);

const calls = [], errors = [];
const body = (status, o) => ({          // a faithful-enough Response: both readers, like the real one
  status, ok: status < 400,
  text: async () => (typeof o === 'string' ? o : JSON.stringify(o)),
  json: async () => (typeof o === 'string' ? JSON.parse(o) : o),
});
const ok = o => body(200, o);

const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  url: 'http://localhost:8080/',
  beforeParse(w) {                       // must be installed BEFORE the page's script runs
    w.localStorage.setItem('gini_tc_session', 'tok');
    w.localStorage.setItem('gini_tc_course', 'comp535');
    w.fetch = async (p, opt) => {
      calls.push({p, method: (opt && opt.method) || 'GET', body: opt && opt.body});
      if (p === '/auth/whoami') return ok({who: 'mahesh', role: 'teacher'});
      if (p.startsWith('/api/courses'))
        return ok([{id: 'comp535', title: 'Networks', staff: ['mahesh'], activities: 0,
                    archived: 0}]);
      if (p.startsWith('/api/activities?'))
        return ok(SAVE.labs ? [{id:'comp535/lab1', course:'comp535', lab:'lab1', title:'test',
                                status:'released', vended:2, submitted:SAVE.submitted||0,
                                vend_until:0, session_minutes:60}] : []);
      if (p === '/api/activities/delete') return ok({ok:true, lab:'lab1', codes:2});
      if (p.startsWith('/api/submissions')) return ok([]);
      if (p === '/api/activities/save') {
        if (SAVE.mode === 'ok') return ok({ok: true, activity: 'comp535/lab1', status: 'draft'});
        if (SAVE.mode === 'refused')
          return body(403, {error: 'That is not your course.'});
        if (SAVE.mode === 'broken')      // handler raised; the server names the cause
          return body(500, {ok: false, error: 'The server hit an error handling that request: '
                                              + 'RuntimeError: the disk is gone'});
        return body(502, '');            // nothing readable at all
      }
      return ok({ok: true});
    };
    w.addEventListener('error', e => errors.push('error: ' + e.message));
    w.addEventListener('unhandledrejection', e =>
      errors.push('unhandled rejection: ' + ((e.reason && e.reason.message) || e.reason)));
  }
});
const w = dom.window;
process.on('unhandledRejection', r => errors.push('unhandled: ' + ((r && r.message) || r)));

setTimeout(async () => {
  const $ = s => w.document.querySelector(s);
  w.show('acts');
  await new Promise(r => setTimeout(r, 100));

  $('#a-lab').value = 'lab1';
  $('#a-title').value = 'multi-lan';
  $('#a-brief').value = 'Build a network with multiple LANs connected by a router.';
  if (SAVE.blank) { $('#a-vend').value = ''; $('#a-mins').value = ''; }
  else { $('#a-vend').value = '2026-08-29T11:11'; $('#a-mins').value = '60'; }

  calls.length = 0;
  const click = t => {
    const b = [...w.document.querySelectorAll('button')].find(x => x.textContent.trim() === t);
    if (b) b.click();
    return !!b;
  };

  let delete_offered = null, confirm_hidden = null;
  if (SAVE.deleting) {
    delete_offered = click('Delete');
    if (delete_offered) {
      confirm_hidden = w.document.querySelector('#del-lab1').hidden;
      if (SAVE.typed !== undefined) w.document.querySelector('#delc-lab1').value = SAVE.typed;
      click('Delete it');
      await new Promise(r => setTimeout(r, 200));
    }
  } else {
    click('Save');
    await new Promise(r => setTimeout(r, 200));
  }

  console.log(JSON.stringify({
    signed_in: !$('#app').hidden,
    on_activities: !$('#acts').hidden,
    calls: calls.map(c => c.method + ' ' + c.p),
    save_body: (calls.find(c => c.p === '/api/activities/save') || {}).body || null,
    errors,
    banner: $('#banner').hidden ? null : $('#banner').textContent,
    banner_is_good: $('#banner').className.includes('good'),
    a_err: $('#a-err').textContent,
    delete_offered, confirm_hidden,
    delete_body: (calls.find(c => c.p === '/api/activities/delete') || {}).body || null,
  }));
  process.exit(0);
}, 400);
"""


def _drive(mode: str, *, blank: bool = False, **extra) -> dict:
    out = subprocess.run(
        ["node", "-e", HARNESS], capture_output=True, text=True, timeout=120,
        env={"PATH": "/usr/bin:/bin", "CONSOLE": str(_CONSOLE), "JSDOM_ROOT": _jsdom_root(),
             "SAVE_RESPONSE": json.dumps({"mode": mode, "blank": blank, **extra})})
    assert out.returncode == 0, f"harness failed:\n{out.stderr}"
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_jsdom
def test_the_console_signs_in_and_reaches_activities():
    r = _drive("ok")
    assert r["signed_in"] and r["on_activities"]


@needs_jsdom
def test_pressing_save_actually_sends_the_request():
    """The literal complaint: press Save, nothing happens."""
    r = _drive("ok")
    assert "POST /api/activities/save" in r["calls"]


@needs_jsdom
def test_a_saved_lab_reloads_the_list_so_it_appears_on_the_right():
    r = _drive("ok")
    assert "GET /api/activities?course=comp535" in r["calls"]


@needs_jsdom
def test_a_save_says_so_and_says_what_to_do_next():
    """A draft that vends nothing looks identical to a failed save unless the console says
    otherwise."""
    r = _drive("ok")
    assert r["banner_is_good"]
    assert "Release" in r["banner"]


@needs_jsdom
def test_a_refusal_is_shown_where_the_teacher_is_looking():
    r = _drive("refused")
    assert r["banner"] == "That is not your course."
    assert r["errors"] == []


@needs_jsdom
def test_a_server_error_reaches_the_teacher_with_its_reason_intact():
    """THE regression, in its second form. The first was silence; the second was a generic
    "Could not save that lab" that REPLACED the specific reason `api()` had just displayed. A
    caller must never overwrite the one useful sentence on the screen."""
    r = _drive("broken")
    assert r["banner"], "the console said nothing at all about a failed save"
    assert "the disk is gone" in r["banner"], f"the reason was lost: {r['banner']!r}"
    assert r["errors"] == [], f"unhandled rejection leaked: {r['errors']}"


@needs_jsdom
def test_a_reply_with_no_readable_body_still_names_the_status():
    """Nothing to quote is not nothing to say."""
    r = _drive("unreadable")
    assert r["banner"] and "502" in r["banner"]
    assert r["errors"] == []


@needs_jsdom
def test_a_blank_deadline_is_sent_as_null_not_as_zero():
    """The data-loss guard. `0` reads as a real value and wipes the saved deadline — which is the
    only thing stopping late submissions, so a teacher fixing a typo in a title could silently
    reopen a closed lab."""
    body = json.loads(_drive("ok", blank=True)["save_body"])
    assert body["vend_until"] is None, body
    assert body["session_minutes"] is None, body


@needs_jsdom
def test_a_filled_deadline_is_sent_as_a_number():
    body = json.loads(_drive("ok")["save_body"])
    assert isinstance(body["vend_until"], (int, float)) and body["vend_until"] > 0
    assert body["session_minutes"] == 60


# -- deleting a lab ------------------------------------------------------------ #
@needs_jsdom
def test_delete_opens_a_confirm_rather_than_deleting_on_the_spot():
    """One click must never destroy a lab. The row expands and asks for the id."""
    r = _drive("ok", labs=True, deleting=True, typed="")
    assert r["delete_offered"], "no Delete button on the row"
    assert r["confirm_hidden"] is False, "the confirm did not open"
    assert r["delete_body"] is None or json.loads(r["delete_body"])["confirm"] == ""


@needs_jsdom
def test_typing_the_lab_id_sends_it_for_confirmation():
    r = _drive("ok", labs=True, deleting=True, typed="lab1")
    body = json.loads(r["delete_body"])
    assert body["lab"] == "lab1" and body["confirm"] == "lab1"


@needs_jsdom
def test_a_lab_with_submissions_offers_no_delete_button_at_all():
    """The server refuses it anyway, but a button that always fails is a button that teaches a
    teacher to distrust the console."""
    r = _drive("ok", labs=True, submitted=3, deleting=True)
    assert r["delete_offered"] is False
