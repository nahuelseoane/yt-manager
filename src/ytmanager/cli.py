"""Command-line interface: `ytm <command> ...`."""

import argparse
import sys
from pathlib import Path

from googleapiclient.errors import HttpError

from ytmanager.auth import build_service
from ytmanager.client import YouTubeClient, find_duplicates
from ytmanager.export import export_to_csv

CLIENT_SECRET = Path("client_secret.json")
TOKEN = Path("token.json")


def cmd_playlists(client: YouTubeClient, args: argparse.Namespace) -> None:
    for p in client.list_playlists():
        print(f"{p.id:<36}  {p.item_count:>4}  {p.title}")


def cmd_export(client: YouTubeClient, args: argparse.Namespace) -> None:
    output = args.output or Path("exports") / f"{args.playlist_id}.csv"
    count = export_to_csv(client.list_playlist_videos(args.playlist_id), output)
    print(f"Exported {count} videos to {output}")


def cmd_search(client: YouTubeClient, args: argparse.Namespace) -> None:
    for v in client.search_videos(args.query, args.max):
        print(f"{v.video_id}  {v.title}  ({v.channel})")


def cmd_add(client: YouTubeClient, args: argparse.Namespace) -> None:
    client.add_to_playlist(args.playlist_id, args.video_id)
    print(f"Added {args.video_id} to {args.playlist_id}")


def cmd_remove_duplicates(client: YouTubeClient, args: argparse.Namespace) -> None:
    duplicates = find_duplicates(client.list_playlist_items(args.playlist_id))
    if not duplicates:
        print("No duplicates found.")
        return

    for item in duplicates:
        print(f"{item.item_id}  {item.video.video_id}  {item.video.title}")

    if args.dry_run:
        print(f"Dry run: would remove {len(duplicates)} item(s).")
        return

    print(f"Quota: this uses {len(duplicates) * 50} units (50 per delete).")
    if not args.yes:
        answer = input(f"Remove {len(duplicates)} duplicate(s)? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("Cancelled.")
            return

    for removed, item in enumerate(duplicates):
        try:
            client.delete_playlist_item(item.item_id)
        except HttpError as e:
            print(
                f"Error deleting {item.item_id}: {e}. "
                f"Removed {removed} of {len(duplicates)} before stopping.",
                file=sys.stderr,
            )
            raise SystemExit(1)
    print(f"Removed {len(duplicates)} duplicate(s).")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ytm", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("playlists", help="List your playlists")
    p.set_defaults(func=cmd_playlists)

    p = sub.add_parser("export", help="Export a playlist to CSV")
    p.add_argument("playlist_id")
    p.add_argument("-o", "--output", type=Path)
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("search", help="Search videos")
    p.add_argument("query")
    p.add_argument("--max", type=int, default=5)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("add", help="Add a video to a playlist")
    p.add_argument("playlist_id")
    p.add_argument("video_id")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser(
        "remove-duplicates", help="Remove repeated videos from a playlist"
    )
    p.add_argument("playlist_id")
    p.add_argument(
        "--dry-run", action="store_true", help="Only show what would be removed"
    )
    p.add_argument(
        "-y", "--yes", action="store_true", help="Don't ask for confirmation"
    )
    p.set_defaults(func=cmd_remove_duplicates)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    client = YouTubeClient(build_service(CLIENT_SECRET, TOKEN))
    args.func(client, args)


if __name__ == "__main__":
    main()
