"""Syscall builder — turn a student's syscall spec into the real xv6 (RISC-V) edits.

Adding a system call to xv6 touches five places; this module generates all of them from a
small spec (name, return type, args, C body). It is PURE text generation, so the codegen and
validation are unit-tested without a compiler — the actual write-patches-and-recompile step is
Mac-side (xv6 tree + `make`), exactly the same split as the rest of the Machine Lab.

The five edit sites (xv6-riscv, MIT 6.1810):
  1. kernel/syscall.h  — `#define SYS_<name> <n>`
  2. kernel/syscall.c  — `extern uint64 sys_<name>(void);` + `[SYS_<name>] sys_<name>,`
  3. kernel/sysproc.c  — the `uint64 sys_<name>(void){…}` implementation (with arg fetch)
  4. user/user.h       — the user-facing prototype
  5. user/usys.pl      — `entry("<name>");` (generates the usys.S trampoline)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# The stock xv6-riscv system calls (numbers 1..21), used to reject name collisions and to
# compute the next free syscall number.
# current xv6-riscv: fork=1 .. sync=22 (note `pause`, not `sleep`, and `sync` at 22)
STOCK_SYSCALLS = (
    "fork", "exit", "wait", "pipe", "read", "kill", "exec", "fstat", "chdir", "dup",
    "getpid", "sbrk", "pause", "uptime", "open", "write", "mknod", "unlink", "link",
    "mkdir", "close", "sync",
)
NEXT_FREE_NUMBER = len(STOCK_SYSCALLS) + 1        # 23

ARG_TYPES = ("int", "addr", "str")               # how the arg is fetched in the kernel
RET_TYPES = ("int", "uint64", "void")

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")
# user.h C type shown to the student, per kernel arg type
_USER_CTYPE = {"int": "int", "addr": "void*", "str": "const char*"}


@dataclass
class SyscallArg:
    name: str
    type: str = "int"        # one of ARG_TYPES


@dataclass
class SyscallSpec:
    name: str
    ret: str = "uint64"
    args: list = field(default_factory=list)      # [SyscallArg]
    body: str = "  return 0;"


def validate(spec: SyscallSpec, existing=STOCK_SYSCALLS) -> list[str]:
    """Return a list of human error strings ([] == valid)."""
    errs: list[str] = []
    if not spec.name or not _IDENT.match(spec.name):
        errs.append("Name must be a valid C identifier (lower-case letters, digits, '_').")
    elif spec.name in existing:
        errs.append(f"'{spec.name}' is already an xv6 system call — pick another name.")
    if spec.ret not in RET_TYPES:
        errs.append(f"Return type must be one of {', '.join(RET_TYPES)}.")
    if len(spec.args) > 6:
        errs.append("A system call can take at most 6 arguments (registers a0–a5).")
    seen = set()
    for a in spec.args:
        if not a.name or not _IDENT.match(a.name):
            errs.append(f"Argument name '{a.name}' is not a valid C identifier.")
        elif a.name in seen:
            errs.append(f"Duplicate argument name '{a.name}'.")
        seen.add(a.name)
        if a.type not in ARG_TYPES:
            errs.append(f"Argument '{a.name}' has an unknown type '{a.type}'.")
    if not (spec.body or "").strip():
        errs.append("The C body is empty — add what the system call should do.")
    elif spec.ret != "void" and "return" not in spec.body:
        errs.append("The body has no `return` but the return type isn't void.")
    return errs


def _fetch(arg: SyscallArg, i: int) -> str:
    if arg.type == "int":
        return f"  int {arg.name};\n  argint({i}, &{arg.name});"
    if arg.type == "addr":
        return f"  uint64 {arg.name};\n  argaddr({i}, &{arg.name});"
    return (f"  char {arg.name}[128];\n"
            f"  argstr({i}, {arg.name}, sizeof({arg.name}));")


@dataclass
class Codegen:
    number: int
    syscall_h: str          # #define line
    extern: str             # extern decl for syscall.c
    dispatch: str           # dispatch-table row for syscall.c
    impl: str               # the sys_<name> function for sysproc.c
    user_h: str             # user-space prototype
    usys_entry: str         # entry("<name>");

    def files(self) -> list[tuple[str, str, str]]:
        """(path, where, snippet) for each of the five edit sites."""
        return [
            ("kernel/syscall.h", "add the #define", self.syscall_h),
            ("kernel/syscall.c", "add extern + dispatch row",
             self.extern + "\n" + self.dispatch),
            ("kernel/sysproc.c", "append the implementation", self.impl),
            ("user/user.h", "add the prototype", self.user_h),
            ("user/usys.pl", "add the entry", self.usys_entry),
        ]

    def preview(self) -> str:
        return "\n\n".join(f"/* {path} — {where} */\n{snippet}"
                           for path, where, snippet in self.files())


def generate(spec: SyscallSpec, number: int = NEXT_FREE_NUMBER) -> Codegen:
    """Render the five edits for `spec`. Assumes `spec` already validated."""
    name = spec.name
    fetch = "\n".join(_fetch(a, i) for i, a in enumerate(spec.args))
    body = spec.body if spec.body.strip() else "  return 0;"
    impl_lines = [f"uint64", f"sys_{name}(void)", "{"]
    if fetch:
        impl_lines.append(fetch)
    impl_lines += ["  // ---- your code ----", body.rstrip(), "  // -------------------", "}"]
    impl = "\n".join(impl_lines)

    user_args = ", ".join(f"{_USER_CTYPE[a.type]} {a.name}" for a in spec.args) or "void"
    user_ret = "int" if spec.ret in ("int", "uint64") else "int"   # xv6 user.h convention

    return Codegen(
        number=number,
        syscall_h=f"#define SYS_{name} {number}",
        extern=f"extern uint64 sys_{name}(void);",
        dispatch=f"[SYS_{name}] sys_{name},",
        impl=impl,
        user_h=f"{user_ret} {name}({user_args});",
        usys_entry=f'entry("{name}");',
    )


def starter_body(spec_name: str = "") -> str:
    """A friendly starter the builder pre-fills the body editor with."""
    return ("  // read your args above, do the work, and return a value.\n"
            "  // e.g. print from the kernel:\n"
            '  printf("hello from sys_' + (spec_name or "mycall") + '\\n");\n'
            "  return 0;")
