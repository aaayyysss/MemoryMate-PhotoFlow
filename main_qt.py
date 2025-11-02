# main_qt.py
# Version 09.15.01.01 dated 20251018

import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from main_window_qt import MainWindow

# ✅ NEW IMPORTS
from splash_qt import SplashScreen, StartupWorker
from settings_manager_qt import SettingsManager


#if __name__ == "__main__":
#    # HiDPI/Retina pixmaps
#    
#    app = QApplication(sys.argv)
#    app.setApplicationName("Memory Mate - Photo Flow")
#    win = MainWindow()
#    win.show()
#    sys.exit(app.exec())


if __name__ == "__main__":
    # Qt app
    app = QApplication(sys.argv)
    app.setApplicationName("Memory Mate - Photo Flow")

    # 1️: Show splash screen immediately
    splash = SplashScreen()
    splash.show()

    # 2️: Initialize settings and startup worker
    settings = SettingsManager()

    worker = StartupWorker(settings)
    worker.progress.connect(splash.update_progress)

    # 3️: Handle cancel button gracefully
    def on_cancel():
        print("[Startup] Cancel requested by user.")
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
