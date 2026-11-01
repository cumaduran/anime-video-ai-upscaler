"""Conservative free-space guard; real compressed video sizes cannot be predicted."""
import math

GIB = 1024 ** 3
RESERVE_BYTES = GIB


def preflight_threshold(reference_bytes: int | None) -> int:
    # This is a guard threshold, not a prediction or a guaranteed sufficient size.
    return RESERVE_BYTES + (math.ceil(reference_bytes * 0.05) if reference_bytes else 0)


def disk_error_text(free_bytes: int, required_bytes: int, folder) -> str:
    return (f"Insufficient disk space on the output drive. Available: {free_bytes / GIB:.1f} GiB; "
            f"safety threshold: {required_bytes / GIB:.1f} GiB. "
            f"Temporary files are created in the output folder: {folder}. "
            "Choose an output folder on a drive with more free space. "
            "The threshold is a rough guard, not a guarantee of sufficient space.")


def contains_disk_error(text: str) -> bool:
    text = text.casefold()
    return any(term in text for term in (
        "no space left on device", "insufficient disk space", "not enough space on the disk",
        "there is not enough space on the disk", "disk full", "disk is full", "winerror 112",
    ))
