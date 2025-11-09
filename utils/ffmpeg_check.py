"""
FFmpeg/FFprobe availability checker with user-friendly notifications.

Provides clear guidance when video processing tools are not installed.
"""

import subprocess
import os
from pathlib import Path
from typing import Tuple


def check_ffmpeg_availability() -> Tuple[bool, bool, str]:
    """
    Check if FFmpeg and FFprobe are available on the system.

    Returns:
        Tuple[bool, bool, str]: (ffmpeg_available, ffprobe_available, message)
    """
    ffmpeg_available = _check_command('ffmpeg')
    ffprobe_available = _check_command('ffprobe')

    if ffmpeg_available and ffprobe_available:
        message = "✅ FFmpeg and FFprobe detected - full video support enabled"
        return True, True, message

    elif not ffmpeg_available and not ffprobe_available:
        message = _get_install_message(missing_both=True)
        return False, False, message

    elif not ffmpeg_available:
        message = _get_install_message(missing_ffmpeg=True)
        return False, True, message

    else:  # not ffprobe_available
        message = _get_install_message(missing_ffprobe=True)
        return True, False, message


def _check_command(command: str) -> bool:
    """
    Check if a command is available in the system PATH.

    Args:
        command: Command name to check (e.g., 'ffmpeg', 'ffprobe')

    Returns:
        True if command is available, False otherwise
    """
    try:
        result = subprocess.run(
            [command, '-version'],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _get_install_message(missing_both: bool = False,
                        missing_ffmpeg: bool = False,
                        missing_ffprobe: bool = False) -> str:
    """
    Get user-friendly installation message based on what's missing.

    Args:
        missing_both: Both FFmpeg and FFprobe are missing
        missing_ffmpeg: Only FFmpeg is missing
        missing_ffprobe: Only FFprobe is missing

    Returns:
        Formatted message with installation instructions
    """
    if missing_both:
        tools = "FFmpeg and FFprobe"
        impact = """
⚠️ Limited Video Support:
  ✅ Videos can be indexed and played
  ❌ Video thumbnails won't be generated
  ❌ Duration/resolution won't be extracted
  ❌ Video filtering will be limited
"""
    elif missing_ffmpeg:
        tools = "FFmpeg"
        impact = """
⚠️ Limited Video Support:
  ✅ Videos can be indexed and played
  ✅ Metadata extraction works (via FFprobe)
  ❌ Video thumbnails won't be generated
"""
    else:  # missing_ffprobe
        tools = "FFprobe"
        impact = """
⚠️ Limited Video Support:
  ✅ Videos can be indexed and played
  ✅ Thumbnail generation works (via FFmpeg)
  ❌ Duration/resolution won't be extracted
"""

    # Platform-specific installation instructions
    if os.name == 'nt':  # Windows
        install_cmd = """
📦 Installation (Windows):
  Option 1: choco install ffmpeg
  Option 2: Download from https://www.gyan.dev/ffmpeg/builds/
           Extract to C:\\ffmpeg and add C:\\ffmpeg\\bin to PATH
"""
    elif os.name == 'posix':
        if Path('/usr/bin/apt-get').exists():  # Ubuntu/Debian
            install_cmd = """
📦 Installation (Ubuntu/Debian):
  sudo apt update && sudo apt install ffmpeg
"""
        elif Path('/usr/bin/dnf').exists():  # Fedora/RHEL
            install_cmd = """
📦 Installation (Fedora/RHEL):
  sudo dnf install ffmpeg
"""
        elif Path('/usr/bin/pacman').exists():  # Arch
            install_cmd = """
📦 Installation (Arch Linux):
  sudo pacman -S ffmpeg
"""
        elif Path('/usr/local/bin/brew').exists() or Path('/opt/homebrew/bin/brew').exists():  # macOS
            install_cmd = """
📦 Installation (macOS):
  brew install ffmpeg
"""
        else:
            install_cmd = """
📦 Installation:
  Install FFmpeg from your package manager or from https://ffmpeg.org/download.html
"""
    else:
        install_cmd = """
📦 Installation:
  Download from https://ffmpeg.org/download.html
"""

    message = f"""
{'='*70}
⚠️  {tools} Not Found
{'='*70}
{impact}
{install_cmd}

📖 For detailed instructions, see: FFMPEG_INSTALL_GUIDE.md
   (located in the application directory)

After installation:
  1. Restart this application
  2. Re-scan folders containing videos
  3. Video features will be fully enabled

{'='*70}
"""

    return message


def show_ffmpeg_status_once():
    """
    Show FFmpeg status message once per session.

    This function checks for a flag file to ensure the message
    is only shown once, avoiding repetitive notifications.

    Returns:
        Message string if this is the first check, None otherwise
    """
    flag_file = Path('.ffmpeg_check_done')

    # Check FFmpeg availability
    ffmpeg_ok, ffprobe_ok, message = check_ffmpeg_availability()

    # If both are available, create flag and return success message
    if ffmpeg_ok and ffprobe_ok:
        if not flag_file.exists():
            flag_file.touch()
        return message

    # If something is missing and we haven't shown the message yet, show it
    if not flag_file.exists():
        # Don't create flag file when tools are missing
        # This ensures the message shows every session until tools are installed
        return message

    # Tools are missing but we've already shown the message this session
    return None


if __name__ == '__main__':
    # Test the checker
    message = show_ffmpeg_status_once()
    if message:
        print(message)
