"""OS Zoo catalog — the historical/classic operating systems students can boot in GINI.

Pure data (no Qt, no Docker), so the palette, the compiler emission, and the Zoo Lab all read one
source of truth and it's unit-testable. Two tiers:

  • FULL      — freely redistributable; GINI builds/ships the image, boots out of the box.
  • SKELETAL  — proprietary (Windows, classic Mac); GINI ships only a shell with the right emulator
                and a REFERENCE link. The student obtains the image/ROM legally and supplies its
                path. GINI hosts nothing copyrighted (see OS_ZOO_DESIGN.md §8).
"""
from __future__ import annotations

from dataclasses import dataclass

FULL = "full"
SKELETAL = "skeletal"

# emulator backends
QEMU = "qemu"
DOSBOX = "dosbox-x"
BASILISK = "basilisk"      # 68k classic Mac (needs a Mac ROM -> always skeletal)


@dataclass(frozen=True)
class ZooOS:
    id: str                 # catalog id / ZOO_OS env value
    label: str              # palette + Zoo Lab title
    tier: str               # FULL | SKELETAL
    emulator: str           # QEMU | DOSBOX | BASILISK
    arch: str               # x86 | x86_64 | 68k
    year: str               # era, for the palette blurb
    blurb: str              # one line
    source: str = ""        # SKELETAL: where the student can legally obtain the image/ROM
    nic: str = "e1000"      # v2 networking default (per-OS; old guests need old NICs)

    @property
    def full(self) -> bool:
        return self.tier == FULL

    @property
    def byo(self) -> bool:
        """Skeletal = 'bring your own image': the student must supply the disk/ROM."""
        return self.tier == SKELETAL


CATALOG: tuple[ZooOS, ...] = (
    ZooOS("freedos", "FreeDOS", FULL, QEMU, "x86", "1998→",
          "An open, still-maintained MS-DOS-compatible OS — the command-line PC of the DOS era.",
          nic="ne2k_pci"),
    ZooOS("kolibri", "KolibriOS", FULL, QEMU, "x86", "2004→",
          "A tiny, blazing-fast GUI OS written entirely in assembly — the whole system boots from "
          "a single 1.44 MB floppy in seconds.",
          nic="rtl8139"),
    ZooOS("menuet", "MenuetOS", FULL, QEMU, "x86", "2000→",
          "The assembly GUI OS that KolibriOS forked from — the whole desktop, apps and all, on a "
          "single 1.44 MB floppy. The 32-bit build is GPL. Boots in seconds.",
          nic="rtl8139"),
    ZooOS("win95", "Windows 95", SKELETAL, QEMU, "x86", "1995",
          "The OS that put a Start button on the world. Bring your own installation media.",
          source="https://archive.org (search Windows 95 — you must own a license)",
          nic="ne2k_pci"),
    ZooOS("mac7", "Mac System 7", SKELETAL, BASILISK, "68k", "1991",
          "Classic Mac OS on a Motorola 68k. Needs a Mac disk image AND a Macintosh ROM you own.",
          source="https://archive.org / macintoshgarden.org (image) + a Mac ROM you own"),
)

_BY_ID = {o.id: o for o in CATALOG}


def get(os_id: str) -> ZooOS | None:
    return _BY_ID.get(os_id)


def full_set() -> list[ZooOS]:
    return [o for o in CATALOG if o.full]


def skeletal_set() -> list[ZooOS]:
    return [o for o in CATALOG if o.byo]
