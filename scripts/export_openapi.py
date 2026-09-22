import json
from pathlib import Path

from app.main import app

destination = Path(__file__).resolve().parents[1] / "web" / "openapi.json"
destination.write_text(json.dumps(app.openapi(), indent=2) + "\n")
print(f"Wrote {destination}")
