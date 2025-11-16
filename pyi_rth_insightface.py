"""
PyInstaller runtime hook for InsightFace model path resolution.

This hook ensures InsightFace finds the bundled buffalo_l models
when running from a PyInstaller-packaged executable.
"""

import os
import sys

# Detect if running in PyInstaller bundle
if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    # Running in PyInstaller bundle
    bundle_dir = sys._MEIPASS

    # Path to bundled InsightFace models
    bundled_models = os.path.join(bundle_dir, 'insightface', 'models')

    # Set environment variable to override default model location
    # InsightFace checks INSIGHTFACE_HOME environment variable
    os.environ['INSIGHTFACE_HOME'] = os.path.join(bundle_dir, 'insightface')

    print(f"[PyInstaller Hook] Set INSIGHTFACE_HOME to: {os.environ['INSIGHTFACE_HOME']}")
    print(f"[PyInstaller Hook] Models should be at: {bundled_models}")

    # Verify models exist
    if os.path.exists(bundled_models):
        print(f"[PyInstaller Hook] ✓ Found bundled models at {bundled_models}")
    else:
        print(f"[PyInstaller Hook] ⚠ WARNING: Models not found at {bundled_models}")
