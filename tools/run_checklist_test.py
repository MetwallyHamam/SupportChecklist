import sys
from pathlib import Path
# Ensure workspace root is on sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.checklist_runner import ChecklistRunner
from utils.config_manager import ConfigManager

if __name__ == '__main__':
    cm = ConfigManager()
    runner = ChecklistRunner(cm)
    report = runner.run()
    import json
    print(json.dumps(report, indent=2))
