"""Command line entry point.

  python -m collectors probe                         # one season-list request, prints the structure
  python -m collectors seasons [--gcode 001]         # publish season list to Kafka
  python -m collectors schedule --season 023 [--league 201] [--gender 1] [--dry-run]
  python -m collectors games --season 022 [--league 203] [--gnum 3] [--limit 5] [--only match|rally]
  python -m collectors probe --path /stat/real-time -p season=022 -p gnum=3 ...   # one request -> samples/

Under Git Bash, set MSYS_NO_PATHCONV=1 so `--path /stat/...` is not rewritten into a Windows path.
"""
import argparse
import json
import logging
import sys
from pathlib import Path

from .kovo_client import KovoClient
from .kovo_collectors import (MatchCollector, RallyCollector, ScheduleCollector, SeasonCollector,
                              finished_games)
from .publisher import DryRunPublisher, KafkaPublisher

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def shape(value, depth=0, max_depth=3):
    """Short description of a JSON value's structure (keys and types, first list item only)."""
    if depth >= max_depth:
        return type(value).__name__
    if isinstance(value, dict):
        return {k: shape(v, depth + 1, max_depth) for k, v in list(value.items())[:25]}
    if isinstance(value, list):
        return [f"list[{len(value)}]", shape(value[0], depth + 1, max_depth)] if value else "list[0]"
    return type(value).__name__


def cmd_probe(args):
    """One request, saved to samples/ so its structure can be checked before writing a collector."""
    if args.path:
        params = dict(p.split("=", 1) for p in args.param)
        body = KovoClient().get(args.path, params)
        name = args.out or args.path.strip("/").replace("/", "-")
    else:
        body = KovoClient().season_list(args.gcode)
        name = args.out or f"season-list-{args.gcode}"
    SAMPLES.mkdir(exist_ok=True)
    out = SAMPLES / f"{name}.json"
    out.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(shape(body), ensure_ascii=False, indent=2))
    print(f"saved: {out}")


def make_publisher(args):
    return DryRunPublisher() if args.dry_run else KafkaPublisher()


def cmd_seasons(args):
    n = SeasonCollector(KovoClient(), make_publisher(args)).run(gcode=args.gcode)
    print(f"published {n} message(s)")


def cmd_schedule(args):
    publisher = make_publisher(args)
    n = ScheduleCollector(KovoClient(), publisher).run(
        season_code=args.season, gcode=args.gcode,
        leagues=[args.league] if args.league else None,
        genders=[args.gender] if args.gender else ("1", "2"),
    )
    print(f"published {n} message(s)")
    if args.dry_run and args.save_sample and publisher.messages:
        SAMPLES.mkdir(exist_ok=True)
        topic, key, value = next(m for m in publisher.messages if m[0] == "kovo.schedule")
        out = SAMPLES / f"game-schedule-{args.season}.json"
        out.write_text(json.dumps(value["payload"], ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(shape(value["payload"]), ensure_ascii=False, indent=2))
        print(f"saved: {out}")


def cmd_games(args):
    client = KovoClient()  # one client, so one rate limiter for every request below
    games = finished_games(client, args.season, args.gcode,
                           leagues=[args.league] if args.league else None,
                           genders=[args.gender] if args.gender else ("1", "2"))
    if args.gnum:
        wanted = set(args.gnum)
        games = [g for g in games if g.gnum in wanted]
    if args.limit:
        games = games[:args.limit]
    if not games:
        print("no finished games to collect")
        return
    requests = len(games) * (0 if args.only == "rally" else 1) + \
        sum(g.sets for g in games) * (0 if args.only == "match" else 1)
    print(f"{len(games)} finished game(s), about {requests} KOVO requests (~{requests // 60 + 1} min at 1 req/s)")
    publisher = make_publisher(args)
    if args.only != "rally":
        print(f"kovo.match: published {MatchCollector(client, publisher).run(games=games, gcode=args.gcode)} message(s)")
    if args.only != "match":
        print(f"kovo.rally: published {RallyCollector(client, publisher).run(games=games, gcode=args.gcode)} message(s)")


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="collectors")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("probe", help="fetch one endpoint once, save it to samples/ and print its structure")
    sp.add_argument("--gcode", default="001")
    sp.add_argument("--path", help="KOVO API path, e.g. /stat/game-schedule/3 (default: season list)")
    sp.add_argument("-p", "--param", action="append", default=[], help="query parameter key=value (repeatable)")
    sp.add_argument("--out", help="sample file name without .json")
    sp.set_defaults(func=cmd_probe)

    sp = sub.add_parser("seasons", help="publish the season list")
    sp.add_argument("--gcode", default="001")
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_seasons)

    sp = sub.add_parser("schedule", help="publish one season's games")
    sp.add_argument("--season", required=True, help="KOVO season code, e.g. 023")
    sp.add_argument("--gcode", default="001")
    sp.add_argument("--league", help="only this league code, e.g. 201")
    sp.add_argument("--gender", choices=["1", "2"], help="1 men, 2 women")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--save-sample", action="store_true", help="with --dry-run: save the first response")
    sp.set_defaults(func=cmd_schedule)

    sp = sub.add_parser("games", help="publish box scores and play-by-play of finished games")
    sp.add_argument("--season", required=True, help="KOVO season code, e.g. 022")
    sp.add_argument("--gcode", default="001")
    sp.add_argument("--league", help="only this league code, e.g. 203")
    sp.add_argument("--gender", choices=["1", "2"], help="1 men, 2 women")
    sp.add_argument("--gnum", type=int, action="append", help="only this game number (repeatable)")
    sp.add_argument("--limit", type=int, help="at most this many games (lowest game numbers first)")
    sp.add_argument("--only", choices=["match", "rally"], help="collect only box scores or only play-by-play")
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_games)

    args = p.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
