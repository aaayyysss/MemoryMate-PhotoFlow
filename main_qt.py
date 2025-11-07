# main_qt.py
# Version 09.15.01.02 dated 20251102
# Added centralized logging initialization

import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from main_window_qt import MainWindow

# ✅ Logging setup (must be first!)
from logging_config import setup_logging, get_logger, disable_external_logging
from settings_manager_qt import SettingsManager

# Initialize settings to get log level
settings = SettingsManager()
log_level = settings.get("log_level", "INFO")
log_to_console = settings.get("log_to_console", True)
log_colored = settings.get("log_colored_output", True)

# Setup logging before any other imports that might log
setup_logging(
    log_level=log_level,
    console=log_to_console,
    use_colors=log_colored
)
disable_external_logging()  # Reduce Qt/PIL noise

logger = get_logger(__name__)

# ✅ Other imports
from splash_qt import SplashScreen, StartupWorker

# ✅ Global exception hook to catch unhandled exceptions
import traceback

def exception_hook(exctype, value, tb):
    """Global exception handler to catch and log unhandled exceptions"""
    print("=" * 80)
    print("UNHANDLED EXCEPTION CAUGHT:")
    print("=" * 80)
    traceback.print_exception(exctype, value, tb)
    logger.error("Unhandled exception", exc_info=(exctype, value, tb))
    print("=" * 80)
    sys.__excepthook__(exctype, value, tb)

sys.excepthook = exception_hook


#if __name__ == "__main__":
#    # HiDPI/Retina pixmaps
#    
#    app = QApplication(sys.argv)
#    app.setApplicationName("Memory Mate - Photo Flow")
#    win = MainWindow()
#    win.show()
#    sys.exit(app.exec())


if __name__ == "__main__":
    # Install global exception handler to catch crashes
    import traceback
    def exception_hook(exctype, value, tb):
        print("=" * 80)
        print("UNHANDLED EXCEPTION CAUGHT:")
        print("=" * 80)
        traceback.print_exception(exctype, value, tb)
        print("=" * 80)
        logger.error("Unhandled exception", exc_info=(exctype, value, tb))
        sys.__excepthook__(exctype, value, tb)

    sys.excepthook = exception_hook

    # Qt app
    app = QApplication(sys.argv)
    app.setApplicationName("Memory Mate - Photo Flow")

    # Install Qt message handler IMMEDIATELY after QApplication creation
    # This must happen before any image loading to suppress TIFF warnings
    from services import install_qt_message_handler
    install_qt_message_handler()
    logger.info("Qt message handler installed to suppress TIFF warnings")

    # 1️: Show splash screen immediately
    splash = SplashScreen()
    splash.show()

    # 2️: Initialize settings and startup worker
    settings = SettingsManager()

    worker = StartupWorker(settings)
    worker.progress.connect(splash.update_progress)

    # 3️: Handle cancel button gracefully
    def on_cancel():
        logger.info("Startup cancelled by user")
        worker.cancel()
        splash.close()
        sys.exit(0)

    splash.cancel_btn.clicked.connect(on_cancel)    
    
    # 4️: When startup finishes
    def on_finished(ok: bool):
        splash.close()
        if not ok:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(None, "Startup Error", "Failed to initialize the app.")
            sys.exit(1)
        # Launch main window after worker completes
        win = MainWindow()
        win.show()

    worker.finished.connect(on_finished)
    
    # 5️: Start the background initialization thread
    worker.start()
    
    # 6️: Run the app
    sys.exit(app.exec())
