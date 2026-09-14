"""Put `reason/` on the path. These tests need no model, no GPU and no network."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
