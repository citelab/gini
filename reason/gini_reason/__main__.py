"""    python -m gini_reason              # serve on 127.0.0.1:8765
    python -m gini_reason ask "why is my process stuck"    # one question, straight to stdout

`ask` is how step 2 is meant to be used at first: read what it would say, against real questions
out of the observation log, before any student can reach it.
"""
from __future__ import annotations

import sys


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "ask":
        from . import audit
        from .ladder import answer
        from .service import _llm
        q = " ".join(args[1:]).strip()
        if not q:
            print('usage: python -m gini_reason ask "the question"')
            return 2
        d = answer(q, llm=_llm(), audit=lambda qq, g, t: audit.flags(audit.review(qq, g, t)))
        print(f"[{d.rung}] strength={d.strength} model={'yes' if d.used_model else 'no'}\n")
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
