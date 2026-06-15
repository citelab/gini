# GINI gBuilder — next generation

A ground-up rewrite of the GINI gBuilder frontend on **PySide6 (Qt 6) / Python 3**, expanding
GINI from a computer-networks lab into an **ultimate experimentation toolkit** spanning both
**computer networks** and **cloud computing**, with a first-class **AI agent layer** so agents can
build, inspect, and explain topologies for students.

## What's here

```
frontend-ng/
├── pyproject.toml
├── src/gini/
│   ├── __main__.py          launch the app  (python -m gini)
│   ├── domain/              pure-Python model — NO Qt
│   │   ├── devices.py       device/element registry (networking + cloud)
│   │   └── topology.py      Topology, DeviceInstance, Link
│   ├── app/
│   │   └── context.py       AppContext + typed event bus (replaces global singletons)
│   ├── ui/
│   │   ├── main_window.py   shell: toolbar, palette, canvas, inspector, console, assistant
│   │   ├── canvas.py        QGraphicsView scene + node/edge items
│   │   ├── palette.py       searchable, categorized device palette
│   │   ├── inspector.py     tabbed property inspector
│   │   ├── assistant.py     in-app "Ask GINI" AI assistant panel
│   │   └── theme/
│   │       ├── tokens.py    themeable design tokens (dark / light / brand)
│   │       ├── manager.py   token → QSS generator + ThemeManager
│   │       └── icons.py     license-safe flat SVG icon set, recolored per category
│   └── agent/
│       ├── api.py           programmatic GiniAPI: build / inspect / explain / run
│       └── mcp_server.py    MCP server exposing GiniAPI to external agents
└── tests/
```

## Run it

```bash
cd frontend-ng
pip install -e .
gbuilder                 # or:  python -m gini
```

Headless smoke test (no display needed):

```bash
QT_QPA_PLATFORM=offscreen python -m gini --selftest
```

## Status

Early scaffold (Phase 2 of the evolution plan). The UI shell, themeable design system, icon
system, the expanded device taxonomy, and the agent API/MCP scaffold are in place and runnable.
The legacy `.gsav`/compiler/protocol services are being ported behind `services/` next.

See `../GINI Project/GINI_Frontend_Evolution_Plan.md` for the full roadmap.
