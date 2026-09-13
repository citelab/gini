"""Put `bot/` on the path. These tests live outside frontend-ng, so the suite's Qt fixtures do not
apply to them and nothing here needs a display."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
