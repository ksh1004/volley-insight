"""수집기 명령줄 진입점. `python -m collectors <명령>`으로 실행한다.

  python -m collectors probe                         # 시즌 목록을 한 번 요청해 응답 구조를 출력
  python -m collectors seasons [--gcode 001]         # 시즌 목록을 Kafka로 발행
  python -m collectors schedule --season 023 [--league 201] [--gender 1] [--dry-run]   # 한 시즌 일정 발행
  python -m collectors games --season 022 [--league 203] [--gnum 3] [--limit 5] [--only match|rally]
                                                     # 끝난 경기의 박스스코어·문자중계 발행
  python -m collectors probe --path /stat/real-time -p season=022 -p gnum=3 ...   # 요청 한 번 → samples/에 저장
  python -m collectors news [--dry-run]              # RSS(+ 키가 있으면 네이버 API)의 배구 기사
  python -m collectors kafka-lag [--every 30] [--once]   # 모든 컨슈머 그룹의 지연 → Elasticsearch
  python -m collectors live [--every 15]             # 오늘 경기의 새 문자중계 이벤트 → kovo.live (계속 실행)
  python -m collectors replay 023-5 [--speed 10] [--from-set 1]   # 끝난 경기를 실시간처럼 → kovo.live (PIPE-11)
  python -m collectors honors --player 1000525 [--player ...] [--dry-run]   # 선수별 수상·주요 기록 → kovo.player

--dry-run: Kafka로 보내지 않고 무엇을 보낼지 로그만 찍는다.
Git Bash에서는 MSYS_NO_PATHCONV=1을 붙여야 `--path /stat/...`이 Windows 경로로 바뀌지 않는다.
보통은 Airflow DAG(airflow/dags)가 이 패키지의 수집기 클래스를 직접 불러 쓰고, 이 명령줄은 수동 실행·점검용이다.
"""
import argparse
import json
import logging
import sys
from pathlib import Path

from .kovo_client import KovoClient
from .kovo_collectors import (HonorCollector, MatchCollector, RallyCollector, ScheduleCollector, SeasonCollector,
                              finished_games)
from .news import NewsCollector, NewsHttp
from .publisher import DryRunPublisher, KafkaPublisher

SAMPLES = Path(__file__).resolve().parent.parent / "samples"  # probe가 응답 예시를 저장하는 폴더


def shape(value, depth=0, max_depth=3):
    """JSON 값의 구조를 짧게 요약한다(키와 자료형만, 목록은 첫 항목만, max_depth 단계까지).
    새 API의 응답 모양을 빠르게 파악할 때 쓴다."""
    if depth >= max_depth:
        return type(value).__name__
    if isinstance(value, dict):
        return {k: shape(v, depth + 1, max_depth) for k, v in list(value.items())[:25]}
    if isinstance(value, list):
        return [f"list[{len(value)}]", shape(value[0], depth + 1, max_depth)] if value else "list[0]"
    return type(value).__name__


def cmd_probe(args):
    """요청 한 번의 응답을 samples/에 저장하고 구조를 출력한다. 수집기를 만들기 전에 응답 모양을 확인하는 용도."""
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
    """--dry-run이면 로그만 찍는 발행기, 아니면 실제 Kafka 발행기."""
    return DryRunPublisher() if args.dry_run else KafkaPublisher()


def cmd_seasons(args):
    """seasons 명령: 시즌 목록을 발행한다."""
    n = SeasonCollector(KovoClient(), make_publisher(args)).run(gcode=args.gcode)
    print(f"published {n} message(s)")


def cmd_schedule(args):
    """schedule 명령: 한 시즌 일정을 발행한다. --dry-run --save-sample이면 첫 응답을 samples/에 저장."""
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
    """games 명령: 끝난 경기를 골라 박스스코어(kovo.match)와 문자중계(kovo.rally)를 발행한다.
    시작 전에 KOVO 요청 수와 예상 시간(1초 1회 기준)을 알려 준다."""
    client = KovoClient()  # 클라이언트를 하나만 써서 아래 모든 요청이 같은 간격 제한을 공유한다
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
    # 요청 수 = 경기당 상세 1번(match) + 세트당 문자중계 1번(rally)
    requests = len(games) * (0 if args.only == "rally" else 1) + \
        sum(g.sets for g in games) * (0 if args.only == "match" else 1)
    print(f"{len(games)} finished game(s), about {requests} KOVO requests (~{requests // 60 + 1} min at 1 req/s)")
    publisher = make_publisher(args)
    if args.only != "rally":
        print(f"kovo.match: published {MatchCollector(client, publisher).run(games=games, gcode=args.gcode)} message(s)")
    if args.only != "match":
        print(f"kovo.rally: published {RallyCollector(client, publisher).run(games=games, gcode=args.gcode)} message(s)")


def cmd_news(args):
    """news 명령: 배구 기사 메타데이터를 발행한다. --dry-run이면 모은 기사 제목을 출력."""
    publisher = make_publisher(args)
    collector = NewsCollector(NewsHttp(), publisher)
    n = collector.run()
    print(f"news.article: published {n} message(s)" + (f", failed feeds: {collector.failures}" if collector.failures else ""))
    if args.dry_run:
        for _, _, a in publisher.messages:
            if a.get("payload"):
                p = a["payload"]
                print(f"  {p['published_at']} {p['publisher']:<6} {p['title'][:60]}")


def cmd_kafka_lag(args):
    """kafka-lag 명령: 컨슈머 그룹 지연을 Elasticsearch에 기록한다(계속 실행, --once면 한 번)."""
    from .kafka_lag import run

    run(every=args.every, once=args.once)


def cmd_live(args):
    """live 명령: 실시간 수집기를 끝없이 돌린다(compose의 live-collector 서비스가 실행)."""
    from .live import LiveCollector

    LiveCollector(KovoClient(), KafkaPublisher()).run(every=args.every)


def cmd_replay(args):
    """replay 명령: 끝난 경기를 실시간처럼 다시 보낸다."""
    from .replay import MatchReplayer

    n = MatchReplayer(KafkaPublisher()).replay(args.match, speed=args.speed, from_set=args.from_set)
    print(f"kovo.live: replayed {n} rallies of {args.match}")


def cmd_honors(args):
    """honors 명령: 지정한 선수들의 수상·주요 기록을 발행한다(평소에는 DAG kovo_honors가 DB의 선수 목록으로 실행)."""
    collector = HonorCollector(KovoClient(), make_publisher(args))
    n = collector.run(codes=args.player)
    print(f"kovo.player: published {n} message(s)" + (f", not on KOVO: {collector.skipped}" if collector.skipped else ""))


def main(argv=None):
    """명령줄 인자를 읽어 해당 cmd_* 함수를 실행한다. 명령마다 하위 파서(subparser)를 하나씩 만든다."""
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

    sp = sub.add_parser("news", help="publish volleyball article metadata (RSS, Naver API)")
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_news)

    sp = sub.add_parser("kafka-lag", help="write every consumer group's lag to Elasticsearch (Kibana)")
    sp.add_argument("--every", type=float, default=30.0, help="seconds between checks")
    sp.add_argument("--once", action="store_true")
    sp.set_defaults(func=cmd_kafka_lag)

    sp = sub.add_parser("live", help="follow today's games and publish new play-by-play events (runs forever)")
    sp.add_argument("--every", type=float, default=15.0, help="seconds between polls of each game (COL-06: 15-30)")
    sp.set_defaults(func=cmd_live)

    sp = sub.add_parser("replay", help="re-publish a finished game's play-by-play as if it were live")
    sp.add_argument("match", help="season-game number, e.g. 023-5")
    sp.add_argument("--speed", type=float, default=10.0, help="1 = real pace, 10 = ten times faster")
    sp.add_argument("--from-set", type=int, default=1)
    sp.set_defaults(func=cmd_replay)

    sp = sub.add_parser("honors", help="publish players' awards, triple crowns and milestone records")
    sp.add_argument("--player", required=True, action="append", help="KOVO player code (repeatable)")
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_honors)

    args = p.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
