from __future__ import annotations

import logging
import sys
import threading

try:
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil
except ImportError:  # pragma: no cover - exercised only on Windows service hosts
    servicemanager = win32event = win32service = win32serviceutil = None

from core.service_runtime import ServiceRuntime
from utils.logger import get_logger, setup_logging
from utils.paths import ensure_app_directories


if win32serviceutil:
    class MiChecklistService(win32serviceutil.ServiceFramework):
        _svc_name_ = "MiChecklistService"
        _svc_display_name_ = "MiChecklist Service"
        _svc_description_ = "Runs persisted MiChecklist checklist and backup jobs without an interactive login."

        def __init__(self, args):
            super().__init__(args)
            self.stop_event = win32event.CreateEvent(None, 0, 0, None)
            self.stopping = threading.Event()

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            get_logger("MiChecklistService").info("Windows service stop requested")
            self.stopping.set()
            win32event.SetEvent(self.stop_event)

        def SvcDoRun(self):
            ensure_app_directories()
            setup_logging(filename_prefix="michecklist_service", include_memory=False)
            logger = get_logger("MiChecklistService")
            servicemanager.LogInfoMsg("MiChecklist service started")
            logger.info("Windows service started")
            try:
                ServiceRuntime(stop_requested=self.stopping.is_set).run()
            except Exception:
                logger.exception("Windows service terminated unexpectedly")
                servicemanager.LogErrorMsg("MiChecklist service terminated unexpectedly; see service log")
                raise
            finally:
                logger.info("Windows service stopped")
                logging.shutdown()
                servicemanager.LogInfoMsg("MiChecklist service stopped")
else:
    MiChecklistService = None


def main():
    if not win32serviceutil:
        raise RuntimeError("pywin32 is required to install or run the Windows service")
    if len(sys.argv) == 1:
        # SCM starts a frozen service without management arguments. A packaged
        # executable must explicitly enter the service control dispatcher;
        # HandleCommandLine is only for install/start/stop/debug commands.
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(MiChecklistService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(MiChecklistService)


if __name__ == "__main__":
    main()
