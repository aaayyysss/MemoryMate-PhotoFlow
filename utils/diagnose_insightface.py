#!/usr/bin/env python3
"""
Comprehensive InsightFace Model Diagnostic Tool

Performs detailed diagnostics on InsightFace models to identify issues:
- File existence and permissions
- File size and integrity
- Directory structure
- InsightFace version compatibility
- Actual model loading test
"""

import os
import sys
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def diagnose_models(path: str = None) -> dict:
    """
    Comprehensive diagnostic of InsightFace models.

    Args:
        path: Path to diagnose (auto-detects if None)

    Returns:
        dict: Diagnostic results
    """
    results = {
        'success': False,
        'issues': [],
        'warnings': [],
        'info': []
    }

    logger.info("=" * 80)
    logger.info("INSIGHTFACE MODEL DIAGNOSTIC TOOL")
    logger.info("=" * 80)
    logger.info("")

    # Step 1: Determine path to check
    if path:
        check_paths = [path]
        logger.info(f"Checking specified path: {path}")
    else:
        logger.info("Auto-detecting model paths...")
        check_paths = []

        # Check custom path from settings
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            custom_path = settings.get_setting('insightface_model_path', '')
            if custom_path:
                check_paths.append(custom_path)
                logger.info(f"  • Custom path from settings: {custom_path}")
        except Exception as e:
            logger.debug(f"Could not read settings: {e}")

        # Check app directory
        try:
            app_root = Path(__file__).parent.parent
            app_models = app_root / 'models' / 'buffalo_l'
            check_paths.append(str(app_models))
            logger.info(f"  • App directory: {app_models}")
        except Exception as e:
            logger.debug(f"Could not determine app root: {e}")

        # Check user home
        user_home = Path.home() / '.insightface' / 'models' / 'buffalo_l'
        check_paths.append(str(user_home))
        logger.info(f"  • User home: {user_home}")

    logger.info("")

    # Step 2: Check each path
    found_path = None
    for test_path in check_paths:
        logger.info(f"Checking: {test_path}")

        if not os.path.exists(test_path):
            logger.warning(f"  ✗ Path does not exist")
            continue

        # Check if it's buffalo_l directory or parent
        det_file = os.path.join(test_path, 'det_10g.onnx')
        if os.path.exists(det_file):
            found_path = test_path
            logger.info(f"  ✓ Found buffalo_l directory")
            break
        else:
            # Check for models/buffalo_l subdirectory
            buffalo_sub = os.path.join(test_path, 'models', 'buffalo_l')
            det_file_sub = os.path.join(buffalo_sub, 'det_10g.onnx')
            if os.path.exists(det_file_sub):
                found_path = buffalo_sub
                logger.info(f"  ✓ Found buffalo_l at: {buffalo_sub}")
                break
            else:
                logger.warning(f"  ✗ No buffalo_l models found")

    if not found_path:
        results['issues'].append("No buffalo_l models found at any checked location")
        logger.error("\n❌ CRITICAL: No buffalo_l models found!")
        logger.info("\nExpected structure:")
        logger.info("  path/to/buffalo_l/")
        logger.info("    ├── det_10g.onnx")
        logger.info("    ├── w600k_r50.onnx")
        logger.info("    └── ... (other model files)")
        return results

    logger.info(f"\n✓ Using buffalo_l directory: {found_path}")
    results['info'].append(f"Buffalo_l directory: {found_path}")

    # Step 3: Check required files
    logger.info("\n" + "=" * 80)
    logger.info("STEP 1: Checking Model Files")
    logger.info("=" * 80)

    required_files = {
        'det_10g.onnx': {'min_size_mb': 100, 'max_size_mb': 200, 'description': 'Detection model (RetinaFace)'},
        'w600k_r50.onnx': {'min_size_mb': 30, 'max_size_mb': 100, 'description': 'Recognition model (ArcFace)'}
    }

    all_files_ok = True
    for filename, specs in required_files.items():
        filepath = os.path.join(found_path, filename)

        if not os.path.exists(filepath):
            logger.error(f"✗ MISSING: {filename} - {specs['description']}")
            results['issues'].append(f"Missing required file: {filename}")
            all_files_ok = False
            continue

        # Check file size
        size_bytes = os.path.getsize(filepath)
        size_mb = size_bytes / (1024 * 1024)

        if size_mb < specs['min_size_mb']:
            logger.error(f"✗ CORRUPTED: {filename} ({size_mb:.1f} MB) - Too small, expected {specs['min_size_mb']}-{specs['max_size_mb']} MB")
            results['issues'].append(f"File too small (possibly corrupted): {filename}")
            all_files_ok = False
        elif size_mb > specs['max_size_mb']:
            logger.warning(f"⚠ UNUSUAL: {filename} ({size_mb:.1f} MB) - Larger than expected {specs['max_size_mb']} MB")
            results['warnings'].append(f"File larger than expected: {filename}")
            logger.info(f"  ✓ {filename} ({size_mb:.1f} MB) - {specs['description']}")
        else:
            logger.info(f"  ✓ {filename} ({size_mb:.1f} MB) - {specs['description']}")

        # Check file permissions
        if not os.access(filepath, os.R_OK):
            logger.error(f"✗ PERMISSION: {filename} - Not readable")
            results['issues'].append(f"Cannot read file (permission denied): {filename}")
            all_files_ok = False

    if not all_files_ok:
        logger.error("\n❌ CRITICAL: Required model files are missing or corrupted!")
        return results

    # Step 4: Check directory structure for InsightFace
    logger.info("\n" + "=" * 80)
    logger.info("STEP 2: Checking Directory Structure")
    logger.info("=" * 80)

    parent_dir = os.path.dirname(found_path)
    dir_name = os.path.basename(found_path)

    if dir_name != 'buffalo_l':
        logger.warning(f"⚠ Directory name is '{dir_name}' instead of 'buffalo_l'")
        results['warnings'].append(f"Non-standard directory name: {dir_name}")
    else:
        logger.info(f"✓ Directory name correct: {dir_name}")

    # Check if parent has models/ subdirectory structure
    grandparent = os.path.dirname(parent_dir)
    parent_name = os.path.basename(parent_dir)

    if parent_name == 'models':
        logger.info(f"✓ Standard structure: .../models/buffalo_l/")
        root_for_insightface = grandparent
        results['info'].append(f"Standard structure detected")
        results['info'].append(f"Root for InsightFace: {root_for_insightface}")
    else:
        logger.warning(f"⚠ Non-standard structure: .../{parent_name}/buffalo_l/")
        logger.info(f"  Expected: .../models/buffalo_l/")
        logger.info(f"  Will need to create models/ symlink")
        root_for_insightface = parent_dir
        results['warnings'].append("Non-standard directory structure")
        results['info'].append(f"Root for InsightFace: {root_for_insightface}")

    # Step 5: Check InsightFace installation
    logger.info("\n" + "=" * 80)
    logger.info("STEP 3: Checking InsightFace Installation")
    logger.info("=" * 80)

    try:
        import insightface
        logger.info(f"✓ InsightFace installed: version {insightface.__version__ if hasattr(insightface, '__version__') else 'unknown'}")
        results['info'].append(f"InsightFace version: {insightface.__version__ if hasattr(insightface, '__version__') else 'unknown'}")
    except ImportError as e:
        logger.error(f"✗ InsightFace not installed: {e}")
        results['issues'].append("InsightFace library not installed")
        logger.info("\nInstall with: pip install insightface onnxruntime")
        return results

    try:
        import onnxruntime as ort
        ort_version = ort.__version__
        logger.info(f"✓ ONNXRuntime installed: version {ort_version}")
        results['info'].append(f"ONNXRuntime version: {ort_version}")

        # Check providers
        providers = ort.get_available_providers()
        logger.info(f"✓ Available providers: {', '.join(providers)}")
        results['info'].append(f"Available providers: {', '.join(providers)}")
    except ImportError as e:
        logger.error(f"✗ ONNXRuntime not installed: {e}")
        results['issues'].append("ONNXRuntime library not installed")
        logger.info("\nInstall with: pip install onnxruntime")
        return results

    # Step 6: Test actual model loading
    logger.info("\n" + "=" * 80)
    logger.info("STEP 4: Testing Model Loading")
    logger.info("=" * 80)

    try:
        from insightface.app import FaceAnalysis
        import inspect

        # Check version compatibility
        sig = inspect.signature(FaceAnalysis.__init__)
        logger.info(f"✓ FaceAnalysis parameters: {list(sig.parameters.keys())}")

        # Prepare initialization parameters
        init_params = {'name': 'buffalo_l'}

        # Determine root based on structure
        if parent_name == 'models':
            init_params['root'] = grandparent
            logger.info(f"  Using root: {grandparent}")
        else:
            # Non-standard structure - need to check/create models/ link
            models_dir = os.path.join(parent_dir, 'models')
            models_buffalo = os.path.join(models_dir, 'buffalo_l')

            if not os.path.exists(models_dir):
                logger.info(f"  Creating models/ directory: {models_dir}")
                try:
                    os.makedirs(models_dir, exist_ok=True)
                except Exception as e:
                    logger.error(f"  ✗ Failed to create models/ directory: {e}")
                    results['issues'].append(f"Cannot create models/ directory: {e}")
                    return results

            if not os.path.exists(models_buffalo):
                logger.info(f"  Creating symlink: {models_buffalo} → {found_path}")
                try:
                    if os.name == 'nt':
                        # Windows junction
                        try:
                            import _winapi
                            _winapi.CreateJunction(found_path, models_buffalo)
                            logger.info(f"  ✓ Created junction")
                        except Exception:
                            os.symlink(found_path, models_buffalo, target_is_directory=True)
                            logger.info(f"  ✓ Created symlink")
                    else:
                        os.symlink(found_path, models_buffalo, target_is_directory=True)
                        logger.info(f"  ✓ Created symlink")
                except Exception as e:
                    logger.error(f"  ✗ Failed to create symlink: {e}")
                    results['issues'].append(f"Cannot create symlink: {e}")
                    return results

            init_params['root'] = parent_dir
            logger.info(f"  Using root: {parent_dir}")

        # Add providers if supported
        if 'providers' in sig.parameters:
            if 'CUDAExecutionProvider' in providers:
                init_params['providers'] = ['CUDAExecutionProvider', 'CPUExecutionProvider']
            else:
                init_params['providers'] = ['CPUExecutionProvider']
            logger.info(f"  Using providers: {init_params['providers']}")

        # Add allowed_modules if supported
        if 'allowed_modules' in sig.parameters:
            init_params['allowed_modules'] = ['detection', 'recognition']
            logger.info(f"  Restricting to modules: detection, recognition")

        logger.info(f"\n  Initializing FaceAnalysis...")
        logger.info(f"  Parameters: {init_params}")

        app = FaceAnalysis(**init_params)

        logger.info(f"  ✓ FaceAnalysis created successfully")

        # Prepare the model
        logger.info(f"\n  Preparing model...")
        ctx_id = 0 if 'CUDAExecutionProvider' in providers else -1
        prepare_params = {'ctx_id': ctx_id, 'det_size': (640, 640)}

        app.prepare(**prepare_params)

        logger.info(f"  ✓ Model prepared successfully")

        # Verify detection model loaded
        if hasattr(app, 'models'):
            if 'detection' in app.models:
                logger.info(f"  ✓ Detection model loaded: {type(app.models['detection']).__name__}")
                results['success'] = True
            else:
                logger.error(f"  ✗ Detection model not loaded!")
                logger.error(f"    Available models: {list(app.models.keys())}")
                results['issues'].append("Detection model not loaded")
                return results

        logger.info("\n" + "=" * 80)
        logger.info("✅ ALL TESTS PASSED - Models are working correctly!")
        logger.info("=" * 80)

        results['success'] = True
        return results

    except Exception as e:
        logger.error(f"\n✗ Model loading failed: {e}")
        logger.error(f"  Error type: {type(e).__name__}")

        import traceback
        traceback_str = traceback.format_exc()
        logger.debug(f"\nFull traceback:\n{traceback_str}")

        results['issues'].append(f"Model loading failed: {e}")

        # Provide specific guidance based on error type
        if "AssertionError" in str(type(e).__name__):
            logger.info("\n💡 AssertionError suggests model files are not being loaded correctly.")
            logger.info("   Possible causes:")
            logger.info("   1. Model files are corrupted")
            logger.info("   2. InsightFace version incompatible with model files")
            logger.info("   3. Directory structure issue")
        elif "RuntimeError" in str(type(e).__name__) and "model routing" in str(e):
            logger.info("\n💡 Model routing error suggests path/structure issue.")
            logger.info("   Try reorganizing to standard structure: .../models/buffalo_l/")

        return results


def print_summary(results: dict):
    """Print diagnostic summary."""
    print("\n" + "=" * 80)
    print("DIAGNOSTIC SUMMARY")
    print("=" * 80)

    if results['info']:
        print("\n📋 Information:")
        for info in results['info']:
            print(f"  • {info}")

    if results['warnings']:
        print("\n⚠️  Warnings:")
        for warning in results['warnings']:
            print(f"  • {warning}")

    if results['issues']:
        print("\n❌ Issues Found:")
        for issue in results['issues']:
            print(f"  • {issue}")

    if results['success']:
        print("\n✅ RESULT: InsightFace models are working correctly!")
    else:
        print("\n❌ RESULT: Issues detected that prevent model loading")
        print("\nRecommended Actions:")
        if any("missing" in i.lower() or "corrupted" in i.lower() for i in results['issues']):
            print("  1. Re-download buffalo_l models")
            print("  2. Run: python download_face_models.py")
        if any("structure" in i.lower() for i in results['issues'] + results['warnings']):
            print("  1. Reorganize to standard structure: .../models/buffalo_l/")
        if any("permission" in i.lower() for i in results['issues']):
            print("  1. Check file permissions")
            print("  2. Ensure files are readable")

    print("=" * 80 + "\n")


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Diagnose InsightFace model issues')
    parser.add_argument('--path', help='Specific path to check (optional, auto-detects if omitted)')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    results = diagnose_models(args.path)
    print_summary(results)

    sys.exit(0 if results['success'] else 1)
