"""
Test InsightFace model loading and initialization.

Provides comprehensive testing of model paths and InsightFace initialization
without requiring app restart.
"""

import os
import sys
import logging
from pathlib import Path
from typing import Tuple, Dict

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def test_model_path(model_path: str) -> Tuple[bool, str]:
    """
    Test if InsightFace models can be loaded from the specified path.

    This performs a complete initialization test including:
    1. Directory structure validation
    2. Model file verification
    3. InsightFace initialization
    4. Provider detection

    Args:
        model_path: Path to test (either buffalo_l directory or parent)

    Returns:
        Tuple[bool, str]: (success, message)
    """
    try:
        logger.info("="  * 70)
        logger.info("InsightFace Model Path Test")
        logger.info("=" * 70)
        logger.info(f"Testing path: {model_path}\n")

        # Step 1: Validate path exists
        if not os.path.exists(model_path):
            return False, f"Path does not exist: {model_path}"

        # Step 2: Detect structure
        test_file = os.path.join(model_path, 'det_10g.onnx')
        is_buffalo_dir = os.path.exists(test_file)

        if is_buffalo_dir:
            logger.info("✓ Detected: buffalo_l directory")
            buffalo_dir = model_path
            parent_dir = os.path.dirname(model_path)
        else:
            # Check if it's parent with models/buffalo_l/
            buffalo_subdir = os.path.join(model_path, 'models', 'buffalo_l')
            if os.path.exists(os.path.join(buffalo_subdir, 'det_10g.onnx')):
                logger.info("✓ Detected: Parent directory with models/buffalo_l/ structure")
                buffalo_dir = buffalo_subdir
                parent_dir = model_path
            else:
                return False, (
                    f"No valid buffalo_l models found at {model_path}\n\n"
                    f"Expected either:\n"
                    f"  1. {model_path}/det_10g.onnx (buffalo_l directory)\n"
                    f"  2. {model_path}/models/buffalo_l/det_10g.onnx (parent directory)"
                )

        # Step 3: Verify essential files
        logger.info("\nVerifying model files...")
        essential_files = ['det_10g.onnx', 'w600k_r50.onnx']
        missing_files = []

        for filename in essential_files:
            filepath = os.path.join(buffalo_dir, filename)
            if os.path.exists(filepath):
                size_mb = os.path.getsize(filepath) / (1024 * 1024)
                logger.info(f"  ✓ {filename} ({size_mb:.1f} MB)")
            else:
                missing_files.append(filename)
                logger.error(f"  ✗ {filename} MISSING")

        if missing_files:
            return False, f"Missing essential files: {', '.join(missing_files)}"

        # Step 4: Check directory structure for InsightFace
        logger.info("\nChecking directory structure...")
        standard_path = os.path.join(parent_dir, 'models', 'buffalo_l')

        if os.path.exists(standard_path):
            if os.path.samefile(buffalo_dir, standard_path):
                logger.info(f"  ✓ Standard structure: {parent_dir}/models/buffalo_l/")
                root_for_insightface = parent_dir
            else:
                logger.warning(f"  ⚠ Multiple buffalo_l directories detected")
                logger.warning(f"    Using: {buffalo_dir}")
                root_for_insightface = parent_dir
        else:
            logger.info(f"  ⚠ Non-standard structure: {buffalo_dir}")
            logger.info(f"    Expected: {standard_path}")
            logger.info(f"    Will create models/ symlink for compatibility")

            # Create models/ directory structure
            models_dir = os.path.join(parent_dir, 'models')
            models_buffalo_link = os.path.join(models_dir, 'buffalo_l')

            try:
                if not os.path.exists(models_dir):
                    os.makedirs(models_dir, exist_ok=True)
                    logger.info(f"  ✓ Created: {models_dir}")

                if not os.path.exists(models_buffalo_link):
                    # Try creating symlink/junction
                    if os.name == 'nt':
                        # Windows: try junction first
                        try:
                            import _winapi
                            _winapi.CreateJunction(buffalo_dir, models_buffalo_link)
                            logger.info(f"  ✓ Created junction: {models_buffalo_link}")
                        except Exception:
                            os.symlink(buffalo_dir, models_buffalo_link, target_is_directory=True)
                            logger.info(f"  ✓ Created symlink: {models_buffalo_link}")
                    else:
                        os.symlink(buffalo_dir, models_buffalo_link, target_is_directory=True)
                        logger.info(f"  ✓ Created symlink: {models_buffalo_link}")

                root_for_insightface = parent_dir
            except Exception as e:
                logger.error(f"  ✗ Failed to create structure: {e}")
                return False, f"Cannot create models/ structure: {e}"

        # Step 5: Test InsightFace initialization
        logger.info("\nTesting InsightFace initialization...")

        try:
            from insightface.app import FaceAnalysis
            import onnxruntime as ort
            import inspect

            # Detect providers
            available_providers = ort.get_available_providers()
            if 'CUDAExecutionProvider' in available_providers:
                providers = ['CUDAExecutionProvider', 'CPUExecutionProvider']
                logger.info("  ✓ Detected: GPU (CUDA) available")
            else:
                providers = ['CPUExecutionProvider']
                logger.info("  ✓ Detected: CPU only")

            # Check version compatibility
            sig = inspect.signature(FaceAnalysis.__init__)
            init_params = {'name': 'buffalo_l', 'root': root_for_insightface}

            if 'providers' in sig.parameters:
                init_params['providers'] = providers
                logger.info("  ✓ InsightFace supports 'providers' parameter")
            else:
                logger.info("  ⚠ InsightFace version doesn't support 'providers' in __init__")

            if 'allowed_modules' in sig.parameters:
                init_params['allowed_modules'] = ['detection', 'recognition']

            # Initialize
            logger.info(f"\n  Initializing with root: {root_for_insightface}")
            app = FaceAnalysis(**init_params)

            # Prepare
            ctx_id = 0 if 'CUDAExecutionProvider' in providers else -1
            prepare_params = {'ctx_id': ctx_id, 'det_size': (640, 640)}

            if 'providers' not in sig.parameters:
                # Try passing to prepare for older versions
                prepare_sig = inspect.signature(app.prepare)
                if 'providers' in prepare_sig.parameters:
                    prepare_params['providers'] = providers

            app.prepare(**prepare_params)

            logger.info("\n✅ SUCCESS! InsightFace initialized successfully")
            logger.info("=" * 70)

            success_msg = (
                f"✓ InsightFace models loaded successfully!\n\n"
                f"Configuration:\n"
                f"  • Model Path: {buffalo_dir}\n"
                f"  • Root for InsightFace: {root_for_insightface}\n"
                f"  • Providers: {', '.join(providers)}\n"
                f"  • Model Files: {', '.join(essential_files)}\n\n"
                f"Face detection is ready to use!"
            )

            return True, success_msg

        except ImportError as e:
            logger.error(f"\n✗ FAILED: InsightFace library not installed")
            logger.error(f"  Error: {e}")
            return False, f"InsightFace library not installed: {e}\n\nInstall with: pip install insightface onnxruntime"

        except Exception as e:
            logger.error(f"\n✗ FAILED: InsightFace initialization error")
            logger.error(f"  Error: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return False, f"InsightFace initialization failed: {e}\n\nCheck that model files are not corrupted and match your InsightFace version."

    except Exception as e:
        logger.error(f"\nUnexpected error during test: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        return False, f"Test failed with error: {e}"


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Test InsightFace model loading')
    parser.add_argument('path', help='Path to test (buffalo_l directory or parent)')
    args = parser.parse_args()

    success, message = test_model_path(args.path)

    print("\n" + "=" * 70)
    print("TEST RESULT")
    print("=" * 70)
    print(message)
    print("=" * 70 + "\n")

    sys.exit(0 if success else 1)
