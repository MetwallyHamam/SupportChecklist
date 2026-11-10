import argparse
import sys

from utils.config_manager import ConfigManager
from utils.logger import setup_logging
from utils.paths import ensure_app_directories, get_app_dir, get_resource_path
from utils.single_instance import SingleInstanceGuard, show_already_running_message
from modules.scheduler_manager import enqueue_default_checklist_if_due
from core.job_queue import PersistentJobQueue
from version import __version__


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--scheduled-run", action="store_true")
    parser.add_argument("--scheduled-test-run", action="store_true")
    parser.add_argument("--tray", action="store_true")
    args, _ = parser.parse_known_args()

    ensure_app_directories()
    setup_logging()
    unattended_run = args.scheduled_run or args.scheduled_test_run
    if not unattended_run:
        single_instance = SingleInstanceGuard(get_app_dir())
        if not single_instance.acquire():
            show_already_running_message()
            return 0

    config_manager = ConfigManager()
    config_manager.load_config()
    if unattended_run:
        enqueue_default_checklist_if_due(
            config_manager, PersistentJobQueue(), force_full=args.scheduled_test_run,
        )
        return 0

    from PySide6.QtGui import QAction, QIcon
    from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

    from gui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("MiChecklist")
    app.setApplicationDisplayName("MiChecklist")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("Millensys")
    app.setWindowIcon(QIcon(str(get_resource_path("michecklist.ico"))))

    window = MainWindow(config_manager=config_manager)
    icon = QIcon(str(get_resource_path("michecklist.ico")))
    tray = QSystemTrayIcon(icon, app)
    tray_menu = QMenu()
    show_action = QAction("Show MiChecklist", tray_menu)
    exit_action = QAction("Exit", tray_menu)
    tray_menu.addAction(show_action)
    tray_menu.addSeparator()
    tray_menu.addAction(exit_action)
    tray.setContextMenu(tray_menu)
    show_action.triggered.connect(window.show_from_tray)
    tray.activated.connect(lambda reason: window.show_from_tray() if reason == QSystemTrayIcon.DoubleClick else None)
    exit_action.triggered.connect(window.request_exit)
    app.aboutToQuit.connect(window.shutdown)
    tray.show()
    window.tray_icon = tray
    if not args.tray:
        window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
