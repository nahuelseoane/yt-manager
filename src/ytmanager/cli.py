"""Command-line interface: `ytm <command> ...`."""

import argparse
import asyncio
import sys
from pathlib import Path

import aiohttp
from googleapiclient.errors import HttpError

from ytmanager import tv
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


# --- TV commands (Lounge API, no Data API client needed) ---


def run_tv(action: tv.Action) -> None:
    auth = tv.load_auth(tv.AUTH_FILE)  # fails before opening any connection

    async def go() -> None:
        async with tv.make_api() as api:
            await tv.send(api, auth, tv.AUTH_FILE, action)

    asyncio.run(go())


def cmd_tv_pair(args: argparse.Namespace) -> None:
    async def go() -> str:
        async with tv.make_api() as api:
            return await tv.pair(api, args.code, tv.AUTH_FILE)

    name = asyncio.run(go())
    print(f"Paired with {name}. Saved to {tv.AUTH_FILE}")


def cmd_tv_play(args: argparse.Namespace) -> None:
    run_tv(lambda api: api.play_video(args.video_id))
    print(f"Playing {args.video_id}")


def cmd_tv_pause(args: argparse.Namespace) -> None:
    run_tv(lambda api: api.pause())


def cmd_tv_resume(args: argparse.Namespace) -> None:
    run_tv(lambda api: api.play())


def cmd_tv_next(args: argparse.Namespace) -> None:
    run_tv(lambda api: api.next())


def cmd_tv_previous(args: argparse.Namespace) -> None:
    run_tv(lambda api: api.previous())


def cmd_tv_volume(args: argparse.Namespace) -> None:
    run_tv(lambda api: api.set_volume(args.level))
    print(f"Volume set to {args.level}")


def volume_level(value: str) -> int:
    level = int(value)
    if not 0 <= level <= 100:
        raise argparse.ArgumentTypeError("volume must be between 0 and 100")
    return level


def add_tv_parser(sub) -> None:
    tv_parser = sub.add_parser("tv", help="Control the YouTube app on your TV")
    tv_sub = tv_parser.add_subparsers(dest="tv_command", required=True)

    def add(name, func, help_text):
        p = tv_sub.add_parser(name, help=help_text)
        p.set_defaults(func=func, needs_youtube=False)
        return p

    add("pair", cmd_tv_pair, "Pair with a TV code").add_argument("code")
    add("play", cmd_tv_play, "Play a video on the TV").add_argument("video_id")
    add("pause", cmd_tv_pause, "Pause playback")
    add("resume", cmd_tv_resume, "Resume playback")
    add("next", cmd_tv_next, "Next video")
    add("previous", cmd_tv_previous, "Previous video")
    add("volume", cmd_tv_volume, "Set volume (0-100)").add_argument(
        "level", type=volume_level
    )


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

    add_tv_parser(sub)

    return parser


def format_http_error(error: HttpError) -> str:
    message = f"Error: YouTube API request failed ({error.status_code})"
    if error.reason:
        message += f": {error.reason}"
    return message


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        if getattr(args, "needs_youtube", True):
            client = YouTubeClient(build_service(CLIENT_SECRET, TOKEN))
            args.func(client, args)
        else:
            args.func(args)
    except HttpError as e:
        print(format_http_error(e), file=sys.stderr)
        raise SystemExit(1)
    except tv.NotPairedError:
        print("Run `ytm tv pair CODE` first.", file=sys.stderr)
        raise SystemExit(1)
    except tv.TVError as e:
        print(f"Error: {e}", file=sys.stderr)
        raise SystemExit(1)
    except (aiohttp.ClientError, TimeoutError) as e:
        print(f"Error: couldn't reach the YouTube TV service ({e})", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
