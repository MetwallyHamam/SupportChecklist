import argparse

from modules.scheduler_manager import run_scheduled_checklist
from utils.config_manager import ConfigManager
from utils.logger import setup_logging
from utils.paths import ensure_app_directories


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--scheduled-run", action="store_true")
    parser.add_argument("--scheduled-test-run", action="store_true")
    args, _ = parser.parse_known_args()

    ensure_app_directories()
    setup_logging()

    config_manager = ConfigManager()
    config_manager.load_config()
    result = run_scheduled_checklist(config_manager, force_test_run=args.scheduled_test_run)
    return 0 if result is None or result.get("status") == "Completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
