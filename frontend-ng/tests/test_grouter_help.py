"""`help <command>` in the gRouter console must print the command's page.

Reported: "the help option does not work in the gRouter terminal". `help` alone worked; `help
route` printed NOTHING, and `route help` gave the route handler's "missing action" error. Most
commands keep their long help in a man page (backend/include/helpdefs/<cmd>.hlp), and helpCmd ran
`man $GINI_SHARE/grouter/helpdefs/<cmd>.hlp` — but in the container GINI_SHARE was unset, man was
not installed and the pages were not in the image, so the shell died on `man (null)/...` and the
error went to the container log, not the console. Nobody saw it fail; it just said nothing.

The router now renders the pages itself (cli.c: CLIPrintCommandHelp) and the image ships them.
Driving that needs a built router; these tests pin the three things that silently broke it: every
page a command names exists, the image installs the pages where the router looks, and no `man`
call came back.
"""
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "backend"
HELPDEFS_H = BACKEND / "include" / "helpdefs.h"
if not HELPDEFS_H.exists():
    pytest.skip("backend checkout not present", allow_module_level=True)

PAGES = BACKEND / "include" / "helpdefs"
CLI_C = BACKEND / "src" / "grouter" / "cli.c"
DOCKERFILE = BACKEND / "grouter-build" / "Dockerfile"


def test_every_page_a_command_names_exists():
    # helpdefs.h for the classic commands; cli.c for the newer ones registered with literals
    named = set(re.findall(r'"([a-z_]+\.hlp)"', HELPDEFS_H.read_text() + CLI_C.read_text()))
    assert len(named) > 15, f"too few pages found ({sorted(named)}) — has the pattern drifted?"
    missing = sorted(p for p in named if not (PAGES / p).exists())
    assert not missing, f"`help <cmd>` names pages that do not exist: {missing}"


def test_the_image_installs_the_pages_where_the_router_looks():
    """cli.c looks in $GINI_SHARE/grouter/helpdefs, then /usr/local/share/gini/grouter/helpdefs.
    Without the COPY the router says the page is not installed instead of showing it."""
    df = DOCKERFILE.read_text()
    assert "/usr/local/share/gini/grouter/helpdefs/" in df, "the image does not ship the pages"
    assert "ENV GINI_SHARE=/usr/local/share/gini" in df
    assert "/usr/local/share/gini/grouter/helpdefs/" in CLI_C.read_text(), \
        "cli.c's fallback no longer matches where the image puts the pages"


def test_help_does_not_shell_out_to_man():
    """man is not in the image, and a pager is wrong on a console captured into a socket."""
    src = CLI_C.read_text()
    assert not re.search(r'"man\s', src), "helpCmd runs man again; it printed nothing in a container"


def test_command_help_is_recognised_before_the_handler_runs():
    """`route help` is answered in parseACLICmd, so no handler needs to learn the word."""
    body = CLI_C.read_text().split("void parseACLICmd", 1)[1].split("\n}\n", 1)[0]
    assert '"help"' in body and "CLIPrintCommandHelp" in body
    assert body.index("CLIPrintCommandHelp") < body.index("clie->handler"), \
        "the handler runs before `<cmd> help` is checked"


def test_pages_hold_only_troff_the_router_can_render():
    """CLIPrintCommandHelp understands .TH .SH, the font macros, .br, .RS/.RE and .nf/.fi, and
    prints anything else as text. A new macro in a page would show up as `.XX` in the
    console, so adding one means teaching hlp_render() about it first."""
    known = {".TH", ".SH", ".B", ".I", ".BR", ".IR", ".RB", ".RI", ".BI", ".IB", ".br",
             ".nf", ".fi", ".RS", ".RE"}
    seen = set()
    for page in PAGES.glob("*.hlp"):
        seen |= set(re.findall(r"^(\.[A-Za-z]+)", page.read_text(), re.M))
    assert not seen - known, f"pages use macros the router does not render: {sorted(seen - known)}"


def test_every_real_command_has_a_full_page():
    """Reported: "some commands have detailed help and others" a single line. gpipe -- the
    pipeline the Router Lab is built on -- was one sentence. Every command that does something
    beyond a single action gets a page; only the four whose whole story fits a line are exempt."""
    one_liners = {"help", "version", "halt", "exit"}
    src = CLI_C.read_text()
    sparse = []
    for name, body in re.findall(r'registerCLI\("([a-z_]+)",(.*?)\);', src, re.S):
        if name in one_liners:
            continue
        if ".hlp" not in body and "LHELP_" not in body:
            sparse.append(name)
        m = re.search(r"LHELP_[A-Z]+", body)
        if m and ".hlp" not in re.search(rf"#define {m.group(0)}\s+(.*)", HELPDEFS_H.read_text()).group(1):
            sparse.append(name)
    assert not sparse, f"commands whose help is a single line, not a page: {sparse}"


def test_the_pages_talk_about_tun_devices():
    """GINI routers have tun1, tun2, ...; the 2009 pages taught ethX and tap0, so a student copying
    `route add -dev eth1 ...` got a silent failure. Only ifconfig may mention the old types, in
    the paragraph that says they are old."""
    for page in PAGES.glob("*.hlp"):
        text = page.read_text()
        if page.name == "ifconfig.hlp":
            text = re.sub(r"The router also knows three older interface types.*?only tun\.", "",
                          text, flags=re.S)
        old = re.findall(r"\b(eth[0-9X]|tap0)\b", text)
        assert not old, f"{page.name} still teaches {sorted(set(old))}"


def test_the_default_route_example_is_one_the_router_accepts():
    """route's parser needs -net and -netmask, in that order; the old example
    `route add -dev eth0 -gw X` failed silently (the error goes to the container log)."""
    examples = (PAGES / "route.hlp").read_text().split(".SH EXAMPLES", 1)[1]
    cmds = re.findall(r"^route add .*$", examples, re.M)
    assert cmds, "route.hlp has no route add examples"
    for cmd in cmds:
        assert re.match(r"route add -dev tun\d+ -net \S+ -netmask \S+( -gw \S+)?$", cmd), cmd


def test_exit_says_it_halts_the_router():
    """exit is registered to haltCmd. Its help said it left the shell WITHOUT halting -- the
    opposite -- so a student typing it to leave the console stopped their router."""
    assert re.search(r'registerCLI\("exit", haltCmd', CLI_C.read_text())
    lhelp = re.search(r'#define LHELP_EXIT\s+"(.*?)"\s*$', HELPDEFS_H.read_text(), re.S | re.M)
    assert lhelp and "Halts the router" in lhelp.group(1) and "quit" in lhelp.group(1)
