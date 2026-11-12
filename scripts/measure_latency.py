"""API 응답 시간 측정(NFR-11, NFR-12).

  python scripts/measure_latency.py                      # 기본: http://127.0.0.1:8000, API마다 30번
  python scripts/measure_latency.py --runs 50 --base http://127.0.0.1:8000

기준(요구사항 명세서 5.2):
  NFR-11 일반 페이지 API: 요청의 95%가 500ms 이내
  NFR-12 검색 API:        요청의 95%가 1초 이내

화면이 실제로 부르는 API를 대표 값으로 하나씩 골라, API마다 2번 미리 부른 뒤(첫 요청의 연결·준비 시간 제외)
정해진 횟수만큼 차례로 불러 시간을 잰다. 묶음(일반/검색)별로 모든 요청을 모아 95번째 백분위수(p95)를 구하고
기준과 비교한다. API별 p95도 함께 보여 주어 느린 API를 찾을 수 있다.
주의: 개발 서버(Django runserver, 요청을 하나씩 처리)에서 재므로 실제 배포 서버보다 느리게 나올 수 있다.
결과는 표준 출력으로만 낸다(파일을 만들지 않음).
"""
import argparse
import json
import math
import time
import urllib.parse
import urllib.request

# (묶음, 이름, 경로) — 이름은 결과 표에 쓰는 짧은 설명
ENDPOINTS = [
    ("일반", "시즌 목록", "/api/seasons"),
    ("일반", "팀 목록", "/api/teams"),
    ("일반", "오늘 경기(홈)", "/api/matches?date={today}&page_size=20"),
    ("일반", "최근 결과(홈)", "/api/matches?status=finished&order=desc&page_size=6"),
    ("일반", "월별 일정", "/api/matches?season=023&month=2026-11"),
    ("일반", "경기 상세(끝난 경기)", "/api/matches/023-18"),
    ("일반", "경기 상세(예정, 프리뷰)", "/api/matches/023-20"),
    ("일반", "문자중계 한 세트", "/api/matches/022-1/rallies?set=1"),
    ("일반", "팀 순위(남)", "/api/standings?gender=M"),
    ("일반", "선수 순위(득점)", "/api/standings/players?gender=M&category=points"),
    ("일반", "팀 상세", "/api/teams/1009"),
    ("일반", "선수 상세", "/api/players/1000525"),
    ("일반", "능력치 순위(시즌)", "/api/ratings?gender=M&position=OP"),
    ("일반", "능력치 순위(통산)", "/api/ratings?scope=career&gender=M&position=OP"),
    ("일반", "예측 검증", "/api/predictions"),
    ("일반", "진행 중 경기", "/api/live"),
    ("일반", "기사 목록", "/api/articles?page_size=6"),
    ("검색", "통합 검색", "/api/search?q=" + urllib.parse.quote("현대")),
    ("검색", "자동완성", "/api/search/suggest?q=" + urllib.parse.quote("김")),
]
LIMIT_MS = {"일반": 500, "검색": 1000}


def p95(values):
    """95번째 백분위수(가장 가까운 순위 방식): 정렬했을 때 95% 지점의 값."""
    s = sorted(values)
    return s[max(0, math.ceil(0.95 * len(s)) - 1)]


assert p95(list(range(1, 101))) == 95 and p95([7]) == 7  # 자체 확인: 1~100이면 95, 값 하나면 그 값


def timed_get(url):
    """GET 한 번의 걸린 시간(ms). 응답 본문을 끝까지 읽은 시점까지 잰다. 200이 아니면 예외."""
    start = time.perf_counter()
    with urllib.request.urlopen(url, timeout=30) as resp:
        resp.read()
        if resp.status != 200:
            raise RuntimeError(f"{url}: HTTP {resp.status}")
    return (time.perf_counter() - start) * 1000


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # localhost가 아니라 127.0.0.1: Windows에서 localhost는 IPv6(::1)를 먼저 시도하는데 Docker 포트는 IPv4로만 열려 있어
    # 요청마다 연결 실패를 기다리느라(약 2초) 서버와 상관없는 시간이 더해진다
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--runs", type=int, default=30)
    args = ap.parse_args()
    today = time.strftime("%Y-%m-%d")

    groups = {g: [] for g in LIMIT_MS}
    rows = []
    for group, name, path in ENDPOINTS:
        url = args.base + path.format(today=today)
        for _ in range(2):  # 미리 부르기(측정에서 제외)
            timed_get(url)
        samples = [timed_get(url) for _ in range(args.runs)]
        groups[group] += samples
        rows.append((group, name, path.format(today=today), p95(samples), max(samples)))
        print(f"  {group} {name:<18} p95 {p95(samples):7.0f}ms  최대 {max(samples):7.0f}ms  {path.format(today=today)}", flush=True)

    print()
    result = {}
    for group, limit in LIMIT_MS.items():
        v = p95(groups[group])
        ok = v <= limit
        result[group] = {"p95_ms": round(v), "limit_ms": limit, "pass": ok, "requests": len(groups[group])}
        print(f"{group}: 요청 {len(groups[group])}개, p95 {v:.0f}ms (기준 {limit}ms) → {'통과' if ok else '미달'}")
    slow = [r for r in rows if r[3] > LIMIT_MS[r[0]]]
    if slow:
        print("\n기준보다 느린 API:")
        for group, name, path, v, _ in sorted(slow, key=lambda r: -r[3]):
            print(f"  {name} ({path}) p95 {v:.0f}ms")
    print("\n" + json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
