"""What a student sees when their kernel does not compile (backend/xv6/gini_agent.py).

The logs below are REAL `make` output, captured from a gini-xv6 container while building a
syscall lab — not hand-written approximations. That matters, because the bug this module guards
against was invisible to a plausible-looking fixture: the giveaway was a 580-character `ld`
command line that happened to contain the word "gini_sched".

Pure-Python, no container.
"""
import importlib.util
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[2] / "backend" / "xv6" / "gini_agent.py"

# gcc, `-Werror`: an undeclared identifier in the student's sys_ function.
COMPILE_LOG = """\
riscv64-unknown-elf-gcc -Wall -Werror -Wno-unknown-attributes -O -fno-omit-frame-pointer -ggdb -gdwarf-2 -march=rv64gc -std=gnu99 -MD -mcmodel=medany -ffreestanding -fno-common -nostdlib -fno-builtin-strncpy -fno-builtin-memset -I. -fno-stack-protector -fno-pie -no-pie   -c -o kernel/sysproc.o kernel/sysproc.c
kernel/sysproc.c: In function 'sys_sysinfo':
kernel/sysproc.c:124:10: error: 'notdeclared' undeclared (first use in this function)
  124 |   return notdeclared + mask;
      |          ^~~~~~~~~~~
kernel/sysproc.c:126:1: error: control reaches end of non-void function [-Werror=return-type]
  126 | }
      | ^
At top level:
cc1: note: unrecognized command-line option '-Wno-unknown-attributes' may have been intended to silence earlier diagnostics
cc1: all warnings being treated as errors
make: *** [<builtin>: kernel/sysproc.o] Error 1
"""

# ld: the student added the syscall everywhere EXCEPT `entry("sysinfo");` in user/usys.pl — the
# most common mistake in this lab. Note the kernel link command naming gini_sched.o.
LINK_LOG = """\
riscv64-unknown-elf-ld -z max-page-size=4096 -T kernel/kernel.ld -o kernel/kernel kernel/shadows/gini_fs.o kernel/shadows/gini_vm.o kernel/shadows/gini_sched.o kernel/entry.o kernel/start.o kernel/console.o kernel/syscall.o kernel/sysproc.o kernel/virtio_disk.o 
riscv64-unknown-elf-ld: warning: kernel/kernel has a LOAD segment with RWX permissions
perl user/usys.pl > user/usys.S
riscv64-unknown-elf-ld -z max-page-size=4096 -T user/user.ld -o user/_sysinfotest user/sysinfotest.o user/ulib.o user/usys.o user/printf.o user/umalloc.o
riscv64-unknown-elf-ld: user/sysinfotest.o: in function `main':
./user/sysinfotest.c:9: undefined reference to `sysinfo'
make: *** [Makefile:110: user/_sysinfotest] Error 1
"""

# The student deleted (or renamed) a file the lab links into the tree.
MISSING_LOG = ("make: *** No rule to make target 'kernel/sysproc.c', "
               "needed by 'kernel/sysproc.o'.  Stop.\n")


@pytest.fixture(scope="module")
def ga():
    if not AGENT.exists():
        pytest.skip("backend/xv6/gini_agent.py not present")
    spec = importlib.util.spec_from_file_location("gini_agent", AGENT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_compile_error_keeps_the_source_line_and_the_caret(ga):
    out = ga._scope_errors(COMPILE_LOG)
    assert "error: 'notdeclared' undeclared" in out
    assert "return notdeclared + mask;" in out      # gcc's echo of the offending line
    assert "^~~~~~~~~~~" in out                     # and the caret under it
    assert "sysproc.c:124:10" in out


def test_a_linker_error_survives_even_though_it_never_says_error(ga):
    """The whole message is `undefined reference`. A filter keyed on "error:" loses it."""
    out = ga._scope_errors(LINK_LOG)
    assert "undefined reference to `sysinfo'" in out
    assert "sysinfotest.c:9" in out


def test_a_link_failure_does_not_blame_a_file_the_student_never_opened(ga):
    """The kernel's link COMMAND lists kernel/shadows/gini_sched.o.

    The previous filter matched that line on the substring "gini_sched" and, with nothing else
    kept, reported the 580-character `ld` invocation as the entire error — pointing a syscall
    student at the scheduler shadow. Whatever is shown must not be a command line.
    """
    out = ga._scope_errors(LINK_LOG)
    assert "gini_sched.o" not in out
    assert "max-page-size" not in out
    assert "-T kernel/kernel.ld" not in out


def test_toolchain_noise_that_appears_in_every_failure_is_dropped(ga):
    """Both lines are properties of the Makefile and the xv6 link, not of the student's code."""
    for log in (COMPILE_LOG, LINK_LOG):
        out = ga._scope_errors(log)
        assert "RWX permissions" not in out
        assert "-Wno-unknown-attributes" not in out


def test_a_missing_lab_file_names_the_file(ga):
    """A lab links host files into the tree; deleting one leaves make with no rule for it."""
    out = ga._scope_errors(MISSING_LOG)
    assert "No rule to make target 'kernel/sysproc.c'" in out


def test_make_reports_which_target_failed(ga):
    assert "Error 1" in ga._scope_errors(COMPILE_LOG)
    assert "user/_sysinfotest" in ga._scope_errors(LINK_LOG)


def test_the_report_is_short_enough_to_read_in_a_panel(ga):
    """This is inline UI, not a terminal. The raw logs are dominated by command echo."""
    for log in (COMPILE_LOG, LINK_LOG):
        out = ga._scope_errors(log)
        assert len(out) < len(log)
        assert all(len(ln) < 200 for ln in out.splitlines())


def test_an_unrecognised_log_still_shows_something(ga):
    """Never return empty: an unparsed failure must still reach the student."""
    out = ga._scope_errors("something went sideways and nothing matched\n")
    assert "sideways" in out


def test_a_shadow_build_error_still_reads_correctly(ga):
    """The filter names no lab file, so the shadow lab it was written for still works."""
    log = ("riscv64-unknown-elf-gcc -Wall -Werror -c -o kernel/shadows/gini_sched.o "
           "kernel/shadows/gini_sched.c\n"
           "kernel/shadows/gini_sched.c:41:3: error: 'p' undeclared (first use in this function)\n"
           "make: *** [<builtin>: kernel/shadows/gini_sched.o] Error 1\n")
    out = ga._scope_errors(log)
    assert "gini_sched.c:41:3: error: 'p' undeclared" in out
    assert "-Wall -Werror -c -o" not in out          # the command, not the diagnostic
