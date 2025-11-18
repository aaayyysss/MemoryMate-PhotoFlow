# main_qt.py
# Version 09.15.01.02 dated 20251102
# Added centralized logging initialization

import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QTimer
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
        # DON'T close splash yet - MainWindow creation still needs to happen
        if not ok:
            splash.close()
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(None, "Startup Error", "Failed to initialize the app.")
            sys.exit(1)

        # Keep splash visible while creating MainWindow (heavy initialization)
        splash.update_progress(85, "Building user interface…")
        QApplication.processEvents()

        # Launch main window after worker completes
        win = MainWindow()

        # Update progress while MainWindow initializes
        splash.update_progress(95, "Finalizing…")
        QApplication.processEvents()

        # Show window and close splash
        win.show()
        splash.update_progress(100, "Ready!")
        QApplication.processEvents()

        # Close splash after a brief delay
        QTimer.singleShot(300, splash.close)

        # Check FFmpeg availability and notify user if needed
        try:
            from utils.ffmpeg_check import show_ffmpeg_status_once
            ffmpeg_message = show_ffmpeg_status_once()
            if ffmpeg_message and "⚠️" in ffmpeg_message:
                # Only show warning if FFmpeg/FFprobe are missing or misconfigured
                print(ffmpeg_message)
                from PySide6.QtWidgets import QMessageBox
                msg_box = QMessageBox(win)
                msg_box.setIcon(QMessageBox.Warning)

                # Check if it's a configuration issue
                if "configured at" in ffmpeg_message and "not working" in ffmpeg_message:
                    msg_box.setWindowTitle("Video Support - FFprobe Configuration Issue")
                    msg_box.setText("The configured FFprobe path is not working.")
                    msg_box.setInformativeText(
                        "Please verify the path in Preferences:\n"
                        "  1. Press Ctrl+, to open Preferences\n"
                        "  2. Go to '🎬 Video Settings'\n"
                        "  3. Use 'Browse' to select ffprobe.exe (not ffmpeg.exe)\n"
                        "  4. Click 'Test' to verify it works\n"
                        "  5. Click OK and restart the app"
                    )
                else:
                    msg_box.setWindowTitle("Video Support - FFmpeg Not Found")
                    msg_box.setText("FFmpeg and/or FFprobe are not installed on your system.")
                    msg_box.setInformativeText(
                        "Video features will be limited:\n"
                        "  • Videos can be indexed and played\n"
                        "  • Video thumbnails won't be generated\n"
                        "  • Duration/resolution won't be extracted\n\n"
                        "Options:\n"
                        "  1. Install FFmpeg system-wide (requires admin)\n"
                        "  2. Configure custom path in Preferences (Ctrl+,)"
                    )

                msg_box.setDetailedText(ffmpeg_message)
                msg_box.setStandardButtons(QMessageBox.Ok)
                msg_box.exec()
            elif ffmpeg_message:
                # FFmpeg is available, just log it
                print(ffmpeg_message)
        except Exception as e:
            logger.warning(f"Failed to check FFmpeg availability: {e}")

        # Check InsightFace models availability and notify user if needed
        try:
            from utils.insightface_check import show_insightface_status_once
            insightface_message = show_insightface_status_once()
            if insightface_message and "⚠️" in insightface_message:
                # Only show warning if InsightFace or models are missing
                print(insightface_message)
                from PySide6.QtWidgets import QMessageBox
                msg_box = QMessageBox(win)
                msg_box.setIcon(QMessageBox.Warning)

                if "Library Not Found" in insightface_message:
                    msg_box.setWindowTitle("Face Detection - InsightFace Not Found")
                    msg_box.setText("InsightFace library is not installed.")
                    msg_box.setInformativeText(
                        "Face detection features will be disabled:\n"
                        "  • Face detection won't work\n"
                        "  • People sidebar will be empty\n"
                        "  • Cannot group photos by faces\n\n"
                        "To enable face detection:\n"
                        "  1. Install InsightFace: pip install insightface onnxruntime\n"
                        "  2. Restart the application\n"
                        "  3. Go to Preferences (Ctrl+,) → 🧑 Face Detection\n"
                        "  4. Click 'Download Models' to get face detection models"
                    )
                else:
                    msg_box.setWindowTitle("Face Detection - Models Not Found")
                    msg_box.setText("InsightFace models (buffalo_l) are not installed.")
                    msg_box.setInformativeText(
                        "Face detection is ready but needs models:\n"
                        "  • InsightFace library is installed ✅\n"
                        "  • Models need to be downloaded (~200MB)\n\n"
                        "To download models:\n"
                        "  1. Go to Preferences (Ctrl+,)\n"
                        "  2. Navigate to '🧑 Face Detection Models'\n"
                        "  3. Click 'Download Models'\n\n"
                        "Or run: python download_face_models.py"
                    )

                msg_box.setDetailedText(insightface_message)
                msg_box.setStandardButtons(QMessageBox.Ok)
                msg_box.exec()
            elif insightface_message:
                # InsightFace is available, just log it
                print(insightface_message)
        except Exception as e:
            logger.warning(f"Failed to check InsightFace availability: {e}")

    worker.finished.connect(on_finished)
    
    # 5️: Start the background initialization thread
    worker.start()
    
    # 6️: Run the app
    sys.exit(app.exec())
