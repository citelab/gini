"""Registry of Diagnose games, so the Games hub card and each Lab can list/open them uniformly.

An entry knows how to BUILD its DiagnoseGameWidget given (theme, machine_state, live). Games that read
current state cheaply (policy/fault/trap) build live cases per draw; the process game needs
accumulation over time, so its catalog entry uses the demo deck (the live version lives in the
Fingerprint Lab, which owns the accumulator)."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import QWidget


@dataclass
class GameEntry:
    id: str
    title: str
    subtitle: str
    build: object                     # (theme, state, live) -> QWidget


_ENTRIES: dict = {}


def register(entry: GameEntry) -> None:
    _ENTRIES[entry.id] = entry


def catalog() -> list:
    return list(_ENTRIES.values())


def get(game_id: str):
    return _ENTRIES.get(game_id)


def _build_process(theme, state, live) -> QWidget:
    from .diagnose_game import DiagnoseGameWidget
    from .game_renderers import RadarChart
    from ..domain.games.process_game import PROCESS_SPEC, demo_cases
    return DiagnoseGameWidget(theme, PROCESS_SPEC, demo_cases, RadarChart(theme), live=False)


register(GameEntry("process-classify", "Classify the process",
                   "Name a process from its behavioral signature.", _build_process))


def _build_policy(theme, state, live) -> QWidget:
    from .diagnose_game import DiagnoseGameWidget
    from .game_renderers import GanttSnippet
    from ..domain.games.policy_game import POLICY_SPEC, demo_cases, live_cases
    from ..domain.xv6 import POLICY_NAMES

    def source():
        if live and state is not None and state.latest is not None:
            pids = [getattr(s, "pid", None) for s in state.timeline.recent()]
            pol = getattr(state.provider, "kernel_policy", None)
            name = POLICY_NAMES.get(pol) if pol is not None else getattr(state, "policy", None)
            cases = live_cases(pids, name)
            if cases:
                return cases
        return demo_cases()

    return DiagnoseGameWidget(theme, POLICY_SPEC, source, GanttSnippet(theme), live=live)


register(GameEntry("guess-policy", "Guess the scheduler",
                   "Read a Gantt timeline and name the scheduling policy.", _build_policy))


def _read_traps(state) -> str:
    fn = getattr(getattr(state, "provider", None), "traps", None)
    try:
        return fn() if callable(fn) else ""
    except Exception:
        return ""


def _build_trap(theme, state, live) -> QWidget:
    from .diagnose_game import DiagnoseGameWidget
    from .game_renderers import EventCard
    from ..domain.games.trap_game import TRAP_SPEC, trap_demo_cases, trap_live_cases
    from ..domain.xv6 import parse_traptrace

    def source():
        if live and state is not None:
            cases = trap_live_cases(parse_traptrace(_read_traps(state)))
            if cases:
                return cases
        return trap_demo_cases()

    return DiagnoseGameWidget(theme, TRAP_SPEC, source, EventCard(theme), live=live)


def _build_fault(theme, state, live) -> QWidget:
    from .diagnose_game import DiagnoseGameWidget
    from .game_renderers import EventCard
    from ..domain.games.trap_game import FAULT_SPEC, fault_demo_cases, fault_live_cases
    from ..domain.xv6 import parse_traptrace

    def source():
        if live and state is not None:
            cases = fault_live_cases(parse_traptrace(_read_traps(state)))
            if cases:
                return cases
        return fault_demo_cases()

    return DiagnoseGameWidget(theme, FAULT_SPEC, source, EventCard(theme), live=live)


register(GameEntry("trap-cause", "Decode the trap",
                   "Read a raw scause and name the trap kind.", _build_trap))
register(GameEntry("fault-type", "Decode the page fault",
                   "Read a page-fault scause and name the access.", _build_fault))


def _build_thrash(theme, state, live) -> QWidget:
    # simulator-backed (real FIFO/LRU/OPT over reference strings): offline deck. A live xv6
    # page-replacement mechanism would add real cases later.
    from .diagnose_game import DiagnoseGameWidget
    from .game_renderers import PagingCard
    from ..domain.games.thrash_game import THRASH_SPEC, demo_cases
    return DiagnoseGameWidget(theme, THRASH_SPEC, demo_cases, PagingCard(theme), live=False)


register(GameEntry("thrash-diagnose", "Diagnose the thrashing",
                   "Read a paging run's signature and name the cause.", _build_thrash))


def _paging_game(spec, cases):
    def build(theme, state, live) -> QWidget:
        from .diagnose_game import DiagnoseGameWidget
        from .game_renderers import RefStringCard
        return DiagnoseGameWidget(theme, spec, cases, RefStringCard(theme), live=False)
    return build


def _register_paging_cluster() -> None:
    from ..domain.games.paging_games import (
        BELADY_SPEC, FAULTCOUNT_SPEC, NEXTEVICT_SPEC, POLICYRANK_SPEC, SHOWDOWN_SPEC,
        belady_cases, faultcount_cases, nextevict_cases, policyrank_cases, showdown_cases,
    )
    register(GameEntry("faults-estimate", "Count the page faults",
                       "Estimate how many faults a run takes.",
                       _paging_game(FAULTCOUNT_SPEC, faultcount_cases)))
    register(GameEntry("belady-spot", "Belady spotter",
                       "Predict whether one more frame helps FIFO.",
                       _paging_game(BELADY_SPEC, belady_cases)))
    register(GameEntry("policy-showdown", "Policy showdown",
                       "FIFO or LRU — which faults fewer on this string?",
                       _paging_game(SHOWDOWN_SPEC, showdown_cases)))
    register(GameEntry("next-evict", "Spot the next eviction",
                       "Pick which resident page is evicted next.",
                       _paging_game(NEXTEVICT_SPEC, nextevict_cases)))
    register(GameEntry("policy-rank", "Rank the policies",
                       "Order FIFO / LRU / OPT by faults.",
                       _paging_game(POLICYRANK_SPEC, policyrank_cases)))


_register_paging_cluster()


def _build_translate(theme, state, live) -> QWidget:
    from .diagnose_game import DiagnoseGameWidget
    from .game_renderers import TranslateCard
    from ..domain.games.translate_game import TRANSLATE_SPEC, demo_cases, live_cases

    def source():
        if live and state is not None and getattr(state, "vm", None) is not None:
            try:
                snap = state.vm.snapshot()
                cases = live_cases(getattr(snap, "leaves", []))
                if cases:
                    return cases
            except Exception:
                pass
        return demo_cases()

    return DiagnoseGameWidget(theme, TRANSLATE_SPEC, source, TranslateCard(theme), live=live)


register(GameEntry("addr-translate", "Translate the address",
                   "Compute a physical address from a page table (plays live page tables).",
                   _build_translate))
