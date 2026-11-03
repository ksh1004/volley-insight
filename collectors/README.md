# collectors

KOVO 공개 API와 뉴스 API에서 데이터를 받아 Kafka에 **원본 그대로** 발행하는 수집기다. 가공은 Flink·Spark가 맡는다(설계서 1.1 ELT 원칙).

## 지금 구현된 것

| 명령 | 요청 | 발행 토픽 (키) | 요구사항 |
|---|---|---|---|
| `python -m collectors probe` | 시즌 목록 1건, 결과를 `samples/`에 저장 | 없음 | 확인용 |
| `python -m collectors seasons` | `/stat/season-list` | `kovo.schedule` (`season-list-001`) | COL-01 |
| `python -m collectors schedule --season 023` | 시즌 목록 1건 + 리그 4개 × 남녀부 = 9건 | `kovo.schedule` (시즌 코드) | COL-02, COL-04 |
| `python -m collectors games --season 022` | 일정 9건으로 끝난 경기를 고른 뒤, 경기마다 상세 1건 + 세트마다 문자중계 1건 | `kovo.match`, `kovo.rally` (`시즌-경기번호`, 예: `022-3`) | COL-03, COL-10 |
| `python -m collectors probe --path … -p k=v` | 아무 API 1건, 결과를 `samples/`에 저장 | 없음 | 새 API 확인용 |

옵션: `--league 201`(특정 리그만), `--gender 1|2`(남자부·여자부만), `--dry-run`(Kafka 없이 확인), `--save-sample`(첫 응답 저장).
`games`에는 `--gnum 3`(특정 경기, 여러 번 가능), `--limit 5`(앞에서부터 N경기), `--only match|rally`도 있다.

한 시즌(약 266경기, 세트 약 950개) 전체는 요청 약 1,250번, **약 20분**이 걸린다(초당 1회 제한).
`games`는 끝난 경기(`result`가 W/L이고 3세트 이상)만 수집한다. 이미 수집한 경기를 건너뛰지는 않으므로, 다시 실행하면 같은 경기가 한 번 더 발행된다. 중복은 Spark 정제 단계에서 `시즌-경기번호` + `fetched_at` 기준으로 제거한다(COL-09). 이미 수집한 경기를 건너뛰는 기능은 PostgreSQL 경기 상태(`CONFIRMED`)가 생기면 Airflow `kovo_postgame` DAG에서 처리한다.

## 실행

Git Bash에서는 `MSYS_NO_PATHCONV=1`을 붙인다. 그러지 않으면 `--path /stat/...`가 `C:/Program Files/Git/stat/...`로 바뀐다(클라이언트가 이런 경로는 거부한다).

```bash
docker compose up -d                                   # Kafka가 떠 있어야 한다
.venv/Scripts/pip install -e "collectors[dev]"
cd collectors
KAFKA_BOOTSTRAP_SERVERS=localhost:29092 python -m collectors schedule --season 023
python -m pytest                                       # 단위 테스트 (네트워크·Kafka 불필요)
```

Kafka에 연결할 수 없으면 KOVO에 요청하기 전에 멈춘다.

## 지켜야 할 수집 정책 (요구사항 5.1)

- 모든 KOVO 요청은 `KovoClient` 하나를 거치고 `RateLimiter`가 **초당 1회 이하**를 보장한다 (`KOVO_MIN_INTERVAL_SEC`).
- User-Agent에 프로젝트를 밝히고, 다른 사이트인 척하는 헤더(Origin 등)를 보내지 않는다.
- 로그인이 필요한 API, 이미지·영상, KOVO 기사 본문은 수집하지 않는다.
- 429·5xx·네트워크 오류는 최대 3회 재시도(2·4·8초 간격), 4xx는 재시도하지 않는다.

## 메시지 형식

```json
{
  "source": "kovo",
  "endpoint": "/stat/game-schedule",
  "params": {"gcode": "001", "seasonCode": "023", "leagueCode": "201", "gender": "1", "page": 0, "size": 200},
  "collector": "kovo.schedule",
  "run_id": "…",
  "fetched_at": "2026-11-03T03:39:50+00:00",
  "payload": { "result": {"status": 200}, "payload": { "content": [ … ], "page": { … } } }
}
```

수집 실행마다 `ops.collection-log`에 시작(`RUNNING`)과 결과(`SUCCESS`·`FAILED`, 건수, 오류)가 기록된다 (COL-08).

## KOVO API에서 확인한 사실 (2026-11-03)

- 로그인 없이 조회된다.
- 시즌 코드는 `001`(2005) ~ `023`(2026-27). 리그 코드: `201` 정규리그, `202` 플레이오프, `203` 챔피언결정전, `204` 준플레이오프. `200` 시즌통산, `801` 올스타전, `901` 시범경기는 경기 수집에서 제외한다.
- 일정 응답(`/stat/game-schedule`)에는 경기 번호·팀 코드·날짜·시각 외에 **세트별 점수와 소요 시간, 관중 수, 심판진, 장소**까지 들어 있다.
- 목록 응답은 `payload.content` + `payload.page.totalPages` 구조로 페이지가 나뉜다.
- 경기 번호(`gnum`)는 한 시즌 안에서 리그·성별을 통틀어 겹치지 않는다(2025-26: 정규리그 1~252, 준PO 253~254, PO 255~258, 챔프전 261~270). 그래서 메시지 키는 `시즌-경기번호`다.
- 경기 상세(`/stat/game-schedule/{gnum}`, 조건 `gcode`·`seasonCode`·`leagueCode`): `game`(일정과 같은 메타 + 세트별 소요 시간), `player`(출전 선수별 공격·오픈·시간차·이동·후위·속공·퀵오픈·서브·블로킹·디그·세트·리시브 세부 기록), `team`(두 팀 합계), `ranking`(팀별 부문 상위 선수).
- 문자중계(`/stat/real-time`, 조건 `season`·`gcode`·`gnum`·`hcode`·`acode`·`rset`·`page`·`size`): 세트 하나씩 조회한다. `feed`가 이벤트 목록이고, 같은 응답에 그 세트의 선발 명단(`lineup`)과 경기 상세 기록도 들어 있다. 페이지 정보는 없어서, 받은 개수가 `size`보다 적으면 마지막 페이지로 본다. 한 세트는 약 250~300개라 `size=500`이면 요청 1번이다.
- 이벤트의 `rpage`는 묶음 순번이다. 랠리 하나, 선수 교체(`CI`/`CO`), 작전 타임(`CT`)이 각각 한 묶음이고, 세트 시작 `ST`는 0번, 종료 `ET`는 200번이다.

- 한 세트는 보통 250~300개 이벤트이고, 15점 5세트는 120~190개다. 듀스가 길면 500개를 넘기도 한다(2025-26에서 8개 세트). 이때는 다음 페이지로 이어지고, 이어 붙이면 `ST`~`ET`가 끊김·중복 없이 이어진다.

- **문자중계는 2023-24 시즌(`020`)부터만 있다.** 시즌마다 1번 경기 1세트를 조회하면 `001`~`019`는 비어 있고 `020`·`021`은 360개가 온다(2026-11-03). 그래서 `RallyCollector`는 `020` 이전 경기를 요청하지 않는다(`PLAY_BY_PLAY_FROM`). 경기 상세(선수·팀 기록)는 2005년(`001`)부터 있다.
- 어떤 경기의 1세트 문자중계가 비어 있으면 그 경기의 나머지 세트는 요청하지 않는다(아직 올라오지 않은 경우). 다음 수집 때 다시 묻는다.

### 알려진 원천 누락

| 경기 | 내용 | 확인 |
|---|---|---|
| 2025-26 `022-163` (2026-01-21 현대건설 1:3 페퍼저축은행) | 3·4세트 문자중계가 비어 있음. 점수·세트 시간·선수 기록은 있음 | 2026-11-03 재조회해도 동일 |

`samples/`의 JSON은 실제 응답이며 회귀 테스트(`tests/test_real_samples.py`)에 쓴다.
