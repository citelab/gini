"""xv6 syscall builder — validation + codegen for the five real edit sites (pure)."""
from gini.domain.syscall_builder import (
    NEXT_FREE_NUMBER, STOCK_SYSCALLS, SyscallArg, SyscallSpec, generate, validate,
)


def _spec(**kw):
    base = dict(name="double", ret="uint64",
                args=[SyscallArg("n", "int")], body="  return n * 2;")
    base.update(kw)
    return SyscallSpec(**base)


# -- validation ------------------------------------------------------------- #
def test_valid_spec_passes():
    assert validate(_spec()) == []


def test_rejects_bad_name_collision_and_empty_body():
    assert any("identifier" in e for e in validate(_spec(name="2bad")))
    assert any("already an xv6" in e for e in validate(_spec(name="fork")))
    assert any("empty" in e for e in validate(_spec(body="   ")))


def test_rejects_too_many_args_and_dupes_and_missing_return():
    many = [SyscallArg(f"a{i}", "int") for i in range(7)]
    assert any("at most 6" in e for e in validate(_spec(args=many)))
    dup = [SyscallArg("x", "int"), SyscallArg("x", "int")]
    assert any("Duplicate" in e for e in validate(_spec(args=dup)))
    assert any("return" in e for e in validate(_spec(ret="uint64", body="  int x = 1;")))
    # void return with no `return` is fine
    assert validate(_spec(ret="void", body="  printf(\"hi\");")) == []


# -- codegen ---------------------------------------------------------------- #
def test_generate_produces_all_five_edit_sites():
    cg = generate(_spec(name="mycall",
                        args=[SyscallArg("n", "int"), SyscallArg("p", "addr")],
                        body="  return n;"), number=22)
    files = dict((path, snip) for path, _where, snip in cg.files())
    assert set(files) == {"kernel/syscall.h", "kernel/syscall.c", "kernel/sysproc.c",
                          "user/user.h", "user/usys.pl"}
    assert files["kernel/syscall.h"] == "#define SYS_mycall 22"
    assert "extern uint64 sys_mycall(void);" in files["kernel/syscall.c"]
    assert "[SYS_mycall] sys_mycall," in files["kernel/syscall.c"]
    assert 'entry("mycall");' in files["user/usys.pl"]


def test_impl_fetches_each_arg_by_kind_and_index():
    cg = generate(_spec(name="f",
                        args=[SyscallArg("n", "int"), SyscallArg("addr", "addr"),
                              SyscallArg("path", "str")], body="  return n;"))
    impl = cg.impl
    assert "argint(0, &n);" in impl
    assert "argaddr(1, &addr);" in impl
    assert "argstr(2, path, sizeof(path));" in impl
    assert impl.startswith("uint64\nsys_f(void)\n{")
    assert impl.rstrip().endswith("}")


def test_user_prototype_maps_types():
    cg = generate(_spec(name="w",
                        args=[SyscallArg("n", "int"), SyscallArg("buf", "addr"),
                              SyscallArg("s", "str")], body="  return 0;"))
    assert cg.user_h == "int w(int n, void* buf, const char* s);"
    # no args -> void
    cg2 = generate(_spec(name="tick", args=[], body="  return 0;"))
    assert cg2.user_h == "int tick(void);"


def test_next_free_number_after_stock_set():
    assert NEXT_FREE_NUMBER == len(STOCK_SYSCALLS) + 1
    assert "close" in STOCK_SYSCALLS and "fork" in STOCK_SYSCALLS
