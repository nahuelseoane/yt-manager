import csv

from ytmanager.client import Video
from ytmanager.export import export_to_csv


def test_export_to_csv_writes_header_and_rows(tmp_path):
    videos = [
        Video("8Fgpk7kM1JU", "Faroeste Caboclo (Ao Vivo)", "Natiruts - Topic"),
        Video("abc", "Título con acentos", ""),
    ]
    output = tmp_path / "out" / "playlist.csv"

    count = export_to_csv(videos, output)

    assert count == 2
    with output.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0] == {
        "title": "Faroeste Caboclo (Ao Vivo)",
        "channel": "Natiruts - Topic",
        "url": "https://www.youtube.com/watch?v=8Fgpk7kM1JU",
    }
    assert rows[1]["title"] == "Título con acentos"
