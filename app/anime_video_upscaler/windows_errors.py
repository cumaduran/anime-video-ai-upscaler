"""Avoid OS crash dialogs blocking the app's child-process error handling."""
import sys


def configure_windows_error_mode() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        current = kernel.GetErrorMode()
        # Children inherit this process error mode. This suppresses Windows Error
        # Reporting UI/reporting for this app and its children, not system-wide.
        kernel.SetErrorMode(current | 0x0001 | 0x0002)
        return bool(kernel.GetErrorMode() & 0x0002)
    except (OSError, AttributeError):
        return False
