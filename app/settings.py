import json
import os
from pathlib import Path

DEFAULTS = {
    "enabled": True,
    "language": "ru",
    "punctuation": True,
    "autostart": False,
}


class Settings:
    def __init__(self):
        base = Path(os.environ.get("APPDATA", Path.home())) / "Golos"
        base.mkdir(parents=True, exist_ok=True)
        self.folder = base
        self.path = base / "settings.json"
        self.first_run = not self.path.exists()
        self.data = dict(DEFAULTS)
        if self.path.exists():
            try:
                self.data.update(json.loads(self.path.read_text(encoding="utf-8")))
            except Exception:
                pass

    def get(self, key, default=None):
        return self.data.get(key, DEFAULTS.get(key, default))

    def set(self, key, value):
        self.data[key] = value
        self.save()

    def save(self):
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)
