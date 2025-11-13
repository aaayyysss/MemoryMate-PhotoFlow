# FFmpeg Installation Guide for MemoryMate-PhotoFlow

## Why Do I Need FFmpeg?

MemoryMate-PhotoFlow uses **FFmpeg** and **FFprobe** to provide full video support:
- **FFprobe**: Extracts video metadata (duration, resolution, codec, fps)
- **FFmpeg**: Generates video thumbnails for grid view

**Without these tools:**
- ✅ Videos are still indexed and playable
- ❌ Video thumbnails won't be generated
- ❌ Duration/resolution won't be extracted
- ❌ Video filtering may be limited

---

## Installation Instructions

### Windows

**Option 1: Using Chocolatey (Recommended)**
```cmd
choco install ffmpeg
```

**Option 2: Manual Installation**
1. Download FFmpeg from: https://www.gyan.dev/ffmpeg/builds/
2. Choose "ffmpeg-release-full.7z"
3. Extract to `C:\ffmpeg`
4. Add to PATH:
   - Open "Environment Variables"
   - Edit "Path" variable
   - Add: `C:\ffmpeg\bin`
5. Restart Command Prompt and verify:
   ```cmd
   ffmpeg -version
   ffprobe -version
   ```

---

### macOS

**Using Homebrew (Recommended)**
```bash
brew install ffmpeg
```

**Verify installation:**
```bash
ffmpeg -version
ffprobe -version
```

---

### Linux (Ubuntu/Debian)

```bash
sudo apt update
sudo apt install ffmpeg
```

**Verify installation:**
```bash
ffmpeg -version
ffprobe -version
```

---

### Linux (Fedora/RHEL)

```bash
sudo dnf install ffmpeg
```

---

### Linux (Arch)

```bash
sudo pacman -S ffmpeg
```

---

## Verifying Installation

After installation, restart MemoryMate-PhotoFlow and check the console output:

**✅ Success:**
```
[INFO] ffprobe detected - video metadata extraction enabled
[INFO] ffmpeg detected - video thumbnail generation enabled
```

**❌ Still missing:**
```
[WARNING] ffprobe not found - video metadata extraction limited
[WARNING] ffmpeg not found - video thumbnail generation disabled
```

If you still see warnings after installation:
1. Verify FFmpeg is in your system PATH
2. Restart your terminal/command prompt
3. Restart MemoryMate-PhotoFlow

---

## Alternative: Using Pre-built Binaries

If you can't modify system PATH, you can place FFmpeg binaries in the application directory:

**Windows:**
- Copy `ffmpeg.exe` and `ffprobe.exe` to the same folder as `main_qt.py`

**macOS/Linux:**
- Copy `ffmpeg` and `ffprobe` to the same folder as `main_qt.py`
- Make them executable: `chmod +x ffmpeg ffprobe`

---

## Troubleshooting

### "ffmpeg not found" even after installation

**Check PATH:**
```bash
# Windows
echo %PATH%

# macOS/Linux
echo $PATH
```

Make sure the FFmpeg bin directory is listed.

### Permission errors (Linux/macOS)

```bash
sudo chmod +x /usr/local/bin/ffmpeg
sudo chmod +x /usr/local/bin/ffprobe
```

### Videos still don't have thumbnails

1. Re-scan your video folder after installing FFmpeg
2. Check the application log for errors
3. Verify video formats are supported (MP4, MOV, MKV, AVI, etc.)

---

## Supported Video Formats

With FFmpeg installed, MemoryMate-PhotoFlow supports:
- **Apple/iPhone:** .mov, .m4v (QuickTime, Live Photos, Cinematic mode)
- **Common:** .mp4, .avi, .mkv, .webm
- **MPEG:** .mpeg, .mpg, .mpe
- **Mobile:** .3gp, .3g2
- **Other:** .wmv, .flv, .ogv, .ts, .mts, .m2ts

---

## Need Help?

If you continue to have issues:
1. Check the application log file: `app_log.txt`
2. Report issues at: https://github.com/aaayyysss/MemoryMate-PhotoFlow/issues
3. Include your FFmpeg version: `ffmpeg -version`
