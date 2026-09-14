"""    python -m gini_reason              # serve on 127.0.0.1:8765
    python -m gini_reason ask "why is my process stuck"    # one question, straight to stdout
    python -m gini_reason batch [N]    # the log's top N questions, and where each one landed

`ask` is how step 2 is meant to be used at first: read what it would say, against real questions
out of the observation log, before any student can reach it.
"""
from __future__ import annotations

import sys


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "batch":
        from . import batch
        from .service import llm_with_reason
        be, why = llm_with_reason()
        print(f"model: {why}\n" if be else f"model: none — {why}\n")
        limit = int(args[1]) if len(args) > 1 and args[1].isdigit() else 40
        print(batch.report(batch.run(limit=limit, llm=be), verbose="-v" in args))
        return 0

    if args and args[0] == "ask":
        from . import audit
        from .ladder import answer
        from .service import llm_with_reason
        q = " ".join(args[1:]).strip()
        if not q:
            print('usage: python -m gini_reason ask "the question"')
            return 2
        be, why = llm_with_reason()
        d = answer(q, llm=be, audit=lambda qq, g, t: audit.flags(audit.review(qq, g, t)))
        # Two different questions, and the first version ran them together into `model=no`:
        # is there a model, and was it USED. A live tunnel plus thin grounding answers yes to the
        # first and no to the second, which reads as a broken connection and is not one.
        print(f"[{d.rung}] strength={d.strength} ({d.score:.2f})  "
              f"model {'used' if d.used_model else 'not used'}")
        print(f"      model: {why}" if be else f"      model: none — {why}")
        print(f"      {d.why}\n")
        print(d.text or "(nothing)")
        if d.citations:
            print("\nfrom:")
            for c in d.citations:
                print("  ·", c)
        if d.flags:
            print("\nthe audit objected:")
            for f in d.flags:
                print("  !", f)
        return 0

    from .service import serve
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
