#!/usr/bin/env python3
"""Is this model usable for drafting observation plans, and where does its time go?

    AI_MODEL=granite4.2:8b python3 teaching-center/probe_model.py
    AI_MODEL=granite4.2:8b python3 teaching-center/probe_model.py --raw     # also time it WITHOUT
                                                                           # the mitigations
Streams the reply and prints it as it arrives, because the single most useful number is not the
total — it is **time to first token**. That is what separates the two failure modes a single
timeout cannot:

  * long silence, then a fast answer   -> the model is thinking before it speaks. Raise the total
                                          budget (AI_TIMEOUT), or turn thinking off.
  * instant start, then a long crawl   -> it is generating a lot. Cap it (num_predict) or use a
                                          smaller/faster model.
  * silence forever                    -> nothing is listening, or the model is wedged. That is the
                                          idle limit's job (AI_IDLE_TIMEOUT), and it should fail in
                                          seconds rather than minutes.

Everything here calls the same client the console uses, with the same settings, so a pass here
means the Activities tab will work.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "frontend-ng" / "src"))

import ai as _ai                                                    # noqa: E402

from gini.agent import aop_selector as _sel                         # noqa: E402

DEFAULT_INTENT = ("Build a few LANs joined by routers and convince me a machine on one LAN can "
                  "talk to a machine on another.")


class Watch:
    """Times a streamed reply: first token, last token, and how much arrived."""

    def __init__(self, echo: bool = True, width: int = 78) -> None:
        self.echo, self.width = echo, width
        self.first: float | None = None
        self.chars = 0
        self.col = 0
        self.t0 = time.perf_counter()

    def __call__(self, chunk: str) -> None:
        if self.first is None:
            self.first = time.perf_counter() - self.t0
            if self.echo:
                print(f"   first token after {self.first:5.1f}s\n   ", end="", flush=True)
        self.chars += len(chunk)
        if not self.echo:
            return
        # Echo compactly: the shape of the reply matters (is it JSON? is it prose?), the exact
        # wrapping does not.
        for ch in chunk:
            if ch == "\n":
                continue
            print(ch, end="", flush=True)
            self.col += 1
            if self.col >= self.width:
                print("\n   ", end="", flush=True)
                self.col = 0

    @property
    def elapsed(self) -> float:
        return time.perf_counter() - self.t0

    def report(self, label: str) -> None:
        gen = self.elapsed - (self.first or 0)
        print(f"\n\n   {label}")
        print(f"     time to first token   {self.first:5.1f}s" if self.first is not None
              else "     time to first token     never")
        print(f"     generating            {gen:5.1f}s")
        print(f"     total                 {self.elapsed:5.1f}s   ({self.chars} chars)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--intent", default=DEFAULT_INTENT)
    ap.add_argument("--model", default=_ai.MODEL)
    ap.add_argument("--url", default=_ai.OLLAMA_URL)
    ap.add_argument("--raw", action="store_true",
                    help="also run WITHOUT think:false/format:json, to show what they are worth")
    ap.add_argument("--quiet", action="store_true", help="do not echo the reply as it streams")
    args = ap.parse_args()

    llm = _ai.Ollama(args.url, args.model)
    print(f"model    {args.model}   at {args.url}")
    print(f"limits   idle {llm.idle_s:.0f}s (AI_IDLE_TIMEOUT) · total {llm.timeout:.0f}s "
          f"(AI_TIMEOUT)\n")

    if not llm.available():
        print("FAILED: no Ollama at that URL. Is `ollama serve` running?")
        return 1

    prompt = _sel._prompt() + f"\n\nTEACHER'S ACTIVITY:\n{args.intent}\n"
    print(f"prompt   {len(prompt)} chars (~{len(prompt) // 4} tokens)")

    # 1 — exactly what the console sends -------------------------------------- #
    print("\n" + "=" * 78)
    print("1. AS THE CONSOLE CALLS IT   (think:false, format:json, num_predict=800)")
    w = Watch(echo=not args.quiet)
    try:
        out = llm.chat("", prompt, json_mode=True, num_predict=800, on_chunk=w)
    except _ai.ModelTooSlow as e:
        w.report("gave up")
        print(f"\n   TOO SLOW: {e}")
        return 1
    except TimeoutError:
        print(f"\n   SILENT for {llm.idle_s:.0f}s — nothing arrived at all.")
        print("   That is not slowness; the model host is not producing. Check `ollama ps`.")
        return 1
    except Exception as e:                                          # noqa: BLE001
        print(f"\n   FAILED: {e}")
        return 1
    w.report("timing")

    obj = _sel._first_json(out)
    print(f"     parses as JSON        {'yes' if isinstance(obj, dict) else 'NO'}")
    if isinstance(obj, dict):
        print(f"     keys                  {sorted(obj)}")
        if "coverage" not in obj:
            print("     coverage              NOT reported — the Reasoning Twin will fall back to")
            print("                           objecting only about urgent concerns.")
        else:
            print("     coverage              reported (the Twin can audit fully)")

    # 2 — what the mitigations are worth -------------------------------------- #
    if args.raw:
        print("\n" + "=" * 78)
        print("2. WITHOUT THE MITIGATIONS   (what you were hitting before)")
        w2 = Watch(echo=not args.quiet)
        try:
            raw = llm.chat("", prompt, on_chunk=w2)
            w2.report("timing")
            thinky = any(t in raw for t in ("<think>", "</think>", "<|think|>"))
            print(f"     emitted reasoning     {'YES' if thinky else 'no'}")
            if w2.elapsed > w.elapsed * 1.3:
                print(f"     => the flags save ~{w2.elapsed - w.elapsed:.0f}s per call here")
        except _ai.ModelTooSlow:
            w2.report("gave up")
            print("     => this model NEEDS the mitigations. The console applies them.")
        except Exception as e:                                      # noqa: BLE001
            print(f"     failed: {e}")

    # 3 — does it survive the whole selector? --------------------------------- #
    print("\n" + "=" * 78)
    print("3. FULL DRAFT   (selector + validator + Reasoning Twin)")
    d = _sel.draft(args.intent, lambda p: llm.chat("", p, json_mode=True, num_predict=800))
    if not d.ok:
        print(f"   FAILED: {d.error}")
        print("\n   The model answered but its choice did not validate — usually an invented")
        print("   pattern key or parameter. The selector already retries once, so this means")
        print("   it did it twice.")
        return 1
    print(f"   patterns chosen        {[p.key for p in d.selection.patterns]}")
    print(f"   questions asked        {len(d.questions)}")
    print(f"   coverage reported      {'no (silent)' if d.coverage_silent else 'yes'}")
    print(f"   twin objections        {len(d.objections)}")
    for o in d.objections:
        print(f"     - {o.question[:96]}")
    print("\n=> usable with this model." + ("" if not d.coverage_silent else
          "\n   Note: coverage-silent, so the Twin only raises urgent concerns."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
