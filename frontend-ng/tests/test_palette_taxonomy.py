"""The palette's shape is a teaching decision, not filing.

Two things it must get right:
  1. Everything that RUNS YOUR CODE lives in ONE section, ordered lightest-to-heaviest. That order
     IS the lesson — container → your-image container → cloud VM → real microVM → real kernel — and
     it used to be invisible because those five were scattered across three sections.
  2. An element's `cloud` flag (which the AI is told about) must NOT be a side-effect of which UI
     section it happens to sit in. Moving Message Queue next to Pub/Sub must not un-cloud it.
"""
from gini.domain import devices as D
from gini.domain.devices import Category, get


def test_every_machine_is_in_the_machines_section():
    machines = D.by_category()[Category.MACHINES]
    assert [d.key for d in machines] == ["host", "container", "instance", "kinstance", "xv6"]
    # …and nothing that ISN'T a machine snuck in
    for d in machines:
        assert d.backend_kind in ("vm", "xv6", None) or d.key in ("container", "instance",
                                                                  "kinstance")


def test_the_order_is_the_isolation_ladder():
    """Lightest first. A student reading top-to-bottom sees the tradeoff before reading a word:
    a container shares the host kernel; Kata brings its own; xv6 IS the kernel."""
    order = [d.key for d in D.by_category()[Category.MACHINES]]
    assert order.index("host") < order.index("kinstance")      # container before microVM
    assert order.index("kinstance") < order.index("xv6")       # microVM before a real kernel
    assert order.index("container") < order.index("instance")  # container before VM


def test_xv6_peripherals_stay_their_own_thing():
    per = D.by_category()[Category.XV6]
    assert [d.key for d in per] == ["terminal", "storage_volume"]
    assert Category.XV6.value == "xv6 Peripherals"     # meaningless on their own; named honestly


def test_the_three_misfilings_are_fixed():
    assert get("queue").category is Category.STREAMING        # a queue IS messaging
    assert get("region").category is Category.CLOUD_NETWORK   # a grouping box, not compute
    assert get("web_app").category is Category.CONTAINERS     # a managed service, not a machine


def test_moving_an_element_between_sections_cannot_change_what_it_IS():
    """`cloud` is stated on the element, not inferred from its palette section — otherwise a UI
    reorg silently rewrites what the AI is told. This is the regression guard for that."""
    assert get("queue").cloud is True        # moved sections, still a rented service
    assert get("container").cloud is True    # moved into Machines, still a cloud element
    assert get("instance").cloud is True and get("kinstance").cloud is True
    assert get("host").cloud is False        # a Machine is a primitive you build, not one you rent
    assert get("xv6").cloud is False
    # the elements whose flag USED to be wrong because their section wasn't in the cloud list
    for key in ("stream", "messaging", "metrics", "dashboard", "tracing", "load_generator"):
        assert get(key).cloud is True


def test_no_empty_sections_survive_the_reorg():
    for cat, items in D.by_category().items():
        assert items, f"{cat.value} is empty"
    assert not hasattr(Category, "COMPUTE")     # dissolved into Machines / Cloud Networking
