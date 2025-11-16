# MemoryMate-PhotoFlow Deployment Guide

This guide explains how to package MemoryMate-PhotoFlow for deployment on PCs without Python installed.

## Overview

The application uses PyInstaller to create a standalone executable that includes:
- Python runtime
- All Python dependencies (PySide6, InsightFace, OpenCV, etc.)
- InsightFace buffalo_l face detection models (~200MB)
- Application code and resources

## Prerequisites

### Development PC (Where you build the package)

1. **Python 3.9+** with all dependencies installed:
   ```bash
   pip install -r requirements.txt
   pip install pyinstaller
   ```

2. **InsightFace models downloaded** (buffalo_l):
   - Models should be in `~/.insightface/models/buffalo_l/`
   - Run the download script if not present:
     ```bash
     python download_models.py
     ```

3. **FFmpeg/FFprobe** (optional, for video support):
   - If you want video thumbnail support in the packaged app
   - Download from https://ffmpeg.org/download.html
   - Can be bundled or configured later on target PC

## Step-by-Step Packaging

### Step 1: Download InsightFace Models

**IMPORTANT**: Models must be downloaded BEFORE packaging!

```bash
python download_models.py
```

**Expected output**:
```
✓ Models already downloaded at: /home/user/.insightface/models/buffalo_l
✓ Found 2 model files:
  - det_10g.onnx (16.9 MB)
  - w600k_r50.onnx (166.8 MB)
✅ Models are ready for PyInstaller packaging!
```

If models are not present, the script will download them automatically (~200MB download).

### Step 2: Build the Executable

Run PyInstaller with the provided spec file:

```bash
pyinstaller memorymate_pyinstaller.spec
```

**Build process**:
- Analyzes dependencies (2-3 minutes)
- Collects all Python packages
- Bundles InsightFace models
- Creates executable and support files
- Output: `dist/MemoryMate-PhotoFlow/` directory

**Expected console output**:
```
Found 2 model files in /home/user/.insightface/models/buffalo_l
...
Building EXE from EXE-00.toc completed successfully.
```

### Step 3: Verify the Package

Check that the build completed successfully:

```bash
ls -lh dist/MemoryMate-PhotoFlow/
```

**Expected contents**:
```
MemoryMate-PhotoFlow.exe    (main executable, Windows)
MemoryMate-PhotoFlow        (main executable, Linux/Mac)
insightface/                (bundled models directory)
  └── models/
      └── buffalo_l/
          ├── det_10g.onnx
          └── w600k_r50.onnx
*.dll / *.so / *.dylib      (runtime libraries)
PySide6/                    (Qt libraries)
... (other dependencies)
```

**Test run** (on development PC):
```bash
cd dist/MemoryMate-PhotoFlow
./MemoryMate-PhotoFlow  # Linux/Mac
# or
MemoryMate-PhotoFlow.exe  # Windows
```

Check the console output for:
```
[PyInstaller Hook] ✓ Found bundled models at ...
🎁 Running from PyInstaller bundle, model root: ...
✓ Found bundled buffalo_l models at ...
✅ InsightFace (buffalo_l) loaded successfully with CPU acceleration
```

## Deployment to Target PC

### Option A: Directory Distribution (Recommended)

**Package the entire folder**:

1. Compress the `dist/MemoryMate-PhotoFlow/` directory:
   ```bash
   cd dist
   zip -r MemoryMate-PhotoFlow-v1.0.zip MemoryMate-PhotoFlow/
   # or
   tar -czf MemoryMate-PhotoFlow-v1.0.tar.gz MemoryMate-PhotoFlow/
   ```

2. **Transfer to target PC** (USB drive, network share, etc.)

3. **Extract and run**:
   - Extract the archive
   - Double-click `MemoryMate-PhotoFlow.exe` (Windows)
   - Or run `./MemoryMate-PhotoFlow` (Linux/Mac)

**Pros**:
- Preserves all file relationships
- Models are guaranteed to be found
- Easier to debug if issues occur

**Cons**:
- Larger download size (~500MB uncompressed)
- Multiple files to manage

### Option B: Single File Executable (Advanced)

Modify the spec file for single-file mode (not recommended for ML apps due to slow startup):

```python
# In memorymate_pyinstaller.spec
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,      # Include these
    a.zipfiles,      # Include these
    a.datas,         # Include these
    [],
    name='MemoryMate-PhotoFlow',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    onefile=True,    # Add this
)
```

**Warning**: Single-file mode extracts to temp directory on each run, causing:
- Slower startup (5-10 seconds)
- Temporary disk usage
- Potential antivirus false positives

## Target PC Requirements

### Minimum Requirements:

- **OS**: Windows 10/11, Linux (Ubuntu 20.04+), macOS 10.15+
- **RAM**: 4GB minimum, 8GB recommended (face detection is memory-intensive)
- **CPU**: Multi-core processor (face detection uses CPU by default)
- **Disk Space**: 1GB for application + data
- **GPU** (optional): CUDA-compatible GPU for faster face detection

### Optional: GPU Acceleration

If target PC has NVIDIA GPU:

1. Install **CUDA Toolkit** (11.x or 12.x)
2. Install **cuDNN**
3. The app will automatically detect and use GPU:
   ```
   🚀 CUDA (GPU) available - Using GPU acceleration for face detection
   ```

Without GPU:
```
💻 Using CPU for face detection (CUDA not available)
```

## Troubleshooting

### Issue: "InsightFace models not found"

**Symptoms**:
```
⚠ Bundled models not found at ...
❌ Failed to initialize InsightFace
```

**Solutions**:
1. Verify models were downloaded before packaging:
   ```bash
   python download_models.py
   ```

2. Check the `dist/MemoryMate-PhotoFlow/insightface/models/buffalo_l/` directory contains:
   - `det_10g.onnx` (detection model)
   - `w600k_r50.onnx` (recognition model)

3. Rebuild the package:
   ```bash
   pyinstaller --clean memorymate_pyinstaller.spec
   ```

### Issue: "ONNX Runtime not found"

**Symptoms**:
```
❌ ONNXRuntime not found
```

**Solution**:
Ensure `onnxruntime` is installed before building:
```bash
pip install onnxruntime
# or for GPU support:
pip install onnxruntime-gpu
```

### Issue: Slow startup or high memory usage

**Cause**: Face detection models are loaded on startup

**Solutions**:
- Use directory distribution (not single-file)
- Ensure target PC has adequate RAM (8GB+)
- Close other memory-intensive applications

### Issue: "DLL load failed" or "Library not found" (Windows)

**Solution**:
Install **Visual C++ Redistributable**:
- Download from: https://aka.ms/vs/17/release/vc_redist.x64.exe
- Install on target PC

### Issue: Face detection not working on target PC

**Debug steps**:

1. Run from command line to see error messages:
   ```cmd
   cd dist\MemoryMate-PhotoFlow
   MemoryMate-PhotoFlow.exe
   ```

2. Check console output for:
   - `✓ Found bundled models at ...` (models found)
   - `✅ InsightFace (buffalo_l) loaded successfully` (initialized correctly)

3. Enable debug logging:
   - Open Preferences (Ctrl+,)
   - Set log level to "DEBUG"
   - Restart and check logs

## Advanced Options

### Custom Build Options

**Reduce package size**:
```python
# In memorymate_pyinstaller.spec
excludes=[
    'matplotlib',
    'tkinter',
    'scipy',  # If not used
    'pandas',  # If not used
]
```

**Enable UPX compression** (smaller binaries):
```bash
# Install UPX first
# Ubuntu: sudo apt install upx
# Windows: Download from https://upx.github.io/

# Already enabled in spec file:
upx=True
```

**Add custom icon**:
```python
# In memorymate_pyinstaller.spec
exe = EXE(
    ...
    icon='path/to/icon.ico',  # Windows .ico file
)
```

### Bundle FFmpeg for Video Support

To include FFmpeg in the package:

1. Download FFmpeg static builds:
   - Windows: https://www.gyan.dev/ffmpeg/builds/
   - Linux: https://johnvansickle.com/ffmpeg/

2. Extract `ffmpeg` and `ffprobe` executables

3. Add to spec file:
   ```python
   binaries=[
       ('path/to/ffmpeg.exe', '.'),
       ('path/to/ffprobe.exe', '.'),
   ]
   ```

4. Update FFmpeg paths in app to look for bundled executables

## Platform-Specific Notes

### Windows

- Build on Windows to deploy to Windows (cross-compilation not reliable)
- May need to disable antivirus during build/run
- Executable size: ~400-500MB
- First run may be slow (Windows Defender scanning)

### Linux

- Build on same Linux distribution as target (or similar)
- May need to install system dependencies on target:
  ```bash
  sudo apt install libxcb-xinerama0 libxcb-cursor0
  ```
- Executable may not run on older glibc versions

### macOS

- Build on macOS to deploy to macOS
- May need to sign and notarize for distribution
- Users may need to allow app in Security & Privacy settings

## Distribution Checklist

Before distributing to users:

- [ ] Models downloaded (`python download_models.py`)
- [ ] Package built successfully (`pyinstaller memorymate_pyinstaller.spec`)
- [ ] Test run on development PC (verify models load)
- [ ] Test run on clean VM or target PC (verify all features work)
- [ ] Compress distribution folder
- [ ] Document target PC requirements
- [ ] Provide troubleshooting guide
- [ ] Test on multiple PCs if possible

## Performance Expectations

### Face Detection Speed (target PC, CPU-only):

- **Small library** (500 photos): ~5-10 minutes
- **Medium library** (2000 photos): ~20-30 minutes
- **Large library** (10000 photos): ~2-3 hours

### With GPU acceleration:

- **Small library**: ~2-3 minutes
- **Medium library**: ~8-12 minutes
- **Large library**: ~45-60 minutes

## Support and Issues

If you encounter issues during deployment:

1. **Check logs**: Enable debug logging in Preferences
2. **Verify models**: Ensure bundled models are present
3. **Test on development PC first**: Rule out packaging issues
4. **System requirements**: Verify target PC meets minimum specs
5. **Report issues**: Include full error messages and logs

## Additional Resources

- PyInstaller documentation: https://pyinstaller.org/
- InsightFace documentation: https://github.com/deepinsight/insightface
- MemoryMate-PhotoFlow repository: https://github.com/aaayyysss/MemoryMate-PhotoFlow

---

**Last updated**: 2025-11-16
**PyInstaller version**: 6.0+
**Tested platforms**: Windows 10/11, Ubuntu 22.04, macOS 13+
