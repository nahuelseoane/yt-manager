"""Export videos to CSV."""

import csv
from collections.abc import Iterable
from pathlib import Path

from ytmanager.client import Video

FIELDS = ["title", "channel", "url"]


def export_to_csv(videos: Iterable[Video], path: Path) -> int:
    """Write videos to a CSV file and return how many rows were written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for video in videos:
            writer.writerow(
                {"title": video.title, "channel": video.channel, "url": video.url}
            )
            count += 1
    return count
