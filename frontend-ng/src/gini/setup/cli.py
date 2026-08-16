"""`gini-setup` — one-time bootstrap of the container runtime + images.

    gini-setup            # detect/guide the runtime, then pull the images
    gini-setup --check    # report status and exit
    gini-setup --update   # re-pull images (after `pip install -U gini-toolkit`)
    gini-setup --yes      # run auto-install steps without prompting
"""
from __future__ import annotations

import argparse
import sys

from . import images, marker, runtime


def _confirm(question: str) -> bool:
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _app_version() -> str:
    from .. import __version__
    return __version__


def _do_check(os_name: str) -> int:
    print(f"gini-toolkit {_app_version()}  ·  {os_name}")
    print("  runtime:", "available" if runtime.docker_available() else "NOT found")
    if marker.is_setup_done():
        print(f"  setup:   done (version {marker.setup_version()})")
        if marker.needs_update(_app_version()):
            print("  note:    app was upgraded — run `gini-setup --update` to refresh images.")
    else:
        print("  setup:   not run yet")
    return 0


def _ensure_runtime(os_name: str, assume_yes: bool) -> bool:
    if runtime.docker_available():
        return True
    plan = runtime.runtime_plan(os_name)
    print(f"No container runtime found. GINI uses {plan['runtime']} on {os_name}.")
    if plan["auto"]:
        print("These commands can set it up:")
        for c in plan["auto"]:
            print("   ", c)
        if assume_yes or _confirm("Run them now?"):
            for c in plan["auto"]:
                if runtime.run_shell(c) != 0:
                    print("Step failed:", c, "\n\n", plan["manual"])
                    return False
        else:
            print("\n" + plan["manual"])
            return False
    else:
        print("\n" + plan["manual"])
        return False
    if not runtime.docker_available():
        print("Runtime installed but not reachable yet — start it and re-run `gini-setup`.")
        return False
    return True


def _pull(os_name: str) -> int:
    refs = images.image_refs(_app_version())
    print("Pulling images:")
    for r in refs:
        print("   ", r)
    results = images.pull_images(refs)
    ok = [r for r, s in results if s]
    bad = [r for r, s in results if not s]
    for r, s in results:
        print("  ok  " if s else "  FAIL", r)
    marker.write_marker({"version": _app_version(), "os": os_name,
                         "tag": images.image_tag(_app_version()), "images": ok})
    print("\nRecorded", marker.marker_path())
    if bad:
        print("\nSome images could not be pulled — they may not be published yet, or the registry "
              "is unreachable. Live Run will be limited until they're available.")
        return 2
    print("\nSetup complete. Launch the app with:  gbuilder")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gini-setup",
                                 description="Bring in the GINI container runtime + images.")
    ap.add_argument("--check", action="store_true", help="report status and exit")
    ap.add_argument("--update", action="store_true", help="re-pull images for the current version")
    ap.add_argument("--yes", "-y", action="store_true", help="run auto-install steps without asking")
    args = ap.parse_args(argv)
    os_name = runtime.detect_os()

    if args.check:
        return _do_check(os_name)
    if args.update:
        if not runtime.docker_available():
            print("No runtime — run `gini-setup` first.")
            return 1
        return _pull(os_name)
    if not _ensure_runtime(os_name, args.yes):
        return 1
    return _pull(os_name)


if __name__ == "__main__":
    sys.exit(main())
