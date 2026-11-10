-- curate_kovo.py가 `staging` 스키마에 올린 정제 KOVO 테이블들을 서비스 테이블(Django가 읽는 테이블)로 병합한다.
-- 한 트랜잭션으로 실행된다. staging 행에는 KOVO 코드(시즌·팀·선수 코드, 경기 번호)가 들어 있고, 이 스크립트가
-- 그것을 서비스 테이블의 id(외래 키)로 바꾼다.
-- 같은 staging 데이터로 다시 돌려도 결과가 같다(멱등): upsert + 원본에서 사라진 행 삭제.
-- 읽는 법: INSERT ... ON CONFLICT DO UPDATE = 있으면 갱신, 없으면 추가(upsert). EXCLUDED = 새로 넣으려던 값.

-- ---------------------------------------------------------------- 기준 데이터(시즌, 대회, 팀, 선수)
INSERT INTO season (code, name, years, start_date, end_date)
SELECT code, name, years, start_date::date, end_date::date FROM staging.season
ON CONFLICT (code) DO UPDATE SET
  name = EXCLUDED.name, years = EXCLUDED.years, start_date = EXCLUDED.start_date, end_date = EXCLUDED.end_date,
  updated_at = now();

INSERT INTO competition (season_id, gcode, league_code, type, name)
SELECT s.id, c.gcode, c.league_code, c.type, c.name
FROM staging.competition c JOIN season s ON s.code = c.season_code
ON CONFLICT (season_id, gcode, league_code) DO UPDATE SET
  type = EXCLUDED.type, name = EXCLUDED.name, updated_at = now();

-- 팀과 선수: staging에는 시즌마다 한 행씩 있다. 가장 최근 것을 쓰고, 옛 시즌을 다시 처리할 때(백필)
-- 옛 이름이 최신 이름을 덮어쓰지 않게 한다(last_seen_on 비교).
INSERT INTO team (code, name, full_name, gender, home_city, home_arena, is_active, last_seen_on)
SELECT DISTINCT ON (code) code, name, COALESCE(full_name, ''), gender, COALESCE(home_city, ''),
       COALESCE(home_arena, ''), true, last_seen_on::date
FROM staging.team ORDER BY code, last_seen_on DESC
ON CONFLICT (code) DO UPDATE SET
  name = EXCLUDED.name, full_name = EXCLUDED.full_name, gender = EXCLUDED.gender,
  home_city = EXCLUDED.home_city, home_arena = EXCLUDED.home_arena, last_seen_on = EXCLUDED.last_seen_on,
  updated_at = now()
WHERE team.last_seen_on IS NULL OR EXCLUDED.last_seen_on >= team.last_seen_on;

INSERT INTO player (code, name, gender, position, height_cm, nationality, status, last_seen_on)
SELECT DISTINCT ON (code) code, name, gender, position, height_cm, '', '', last_seen_on::date
FROM staging.player ORDER BY code, last_seen_on DESC, (name <> '') DESC
ON CONFLICT (code) DO UPDATE SET
  name = COALESCE(NULLIF(EXCLUDED.name, ''), player.name),           -- 문자중계에만 나온 선수 행은 이름이 비어 있어 기존 이름을 유지
  gender = EXCLUDED.gender,
  position = COALESCE(NULLIF(EXCLUDED.position, ''), player.position),
  height_cm = COALESCE(EXCLUDED.height_cm, player.height_cm),
  last_seen_on = EXCLUDED.last_seen_on,
  updated_at = now()
WHERE player.last_seen_on IS NULL OR EXCLUDED.last_seen_on >= player.last_seen_on;

-- ---------------------------------------------------------------- 경기(경기 시각은 서울 시각으로 해석해 저장)
INSERT INTO match (competition_id, game_no, round, gender, home_team_id, away_team_id, scheduled_at, venue, city,
                   broadcasters, status, home_sets, away_sets, duration_min, spectators, referee_main, referee_sub)
SELECT c.id, m.game_no, m.round, m.gender, ht.id, at.id,
       (m.game_date || ' ' || COALESCE(m.start_time, '00:00'))::timestamp AT TIME ZONE 'Asia/Seoul',
       m.venue, m.city, m.broadcasters, m.status, m.home_sets, m.away_sets, m.duration_min, m.spectators,
       m.referee_main, m.referee_sub
FROM staging.match m
JOIN season s ON s.code = m.season_code
JOIN competition c ON c.season_id = s.id AND c.gcode = m.gcode AND c.league_code = m.league_code
JOIN team ht ON ht.code = m.home_code
JOIN team at ON at.code = m.away_code
ON CONFLICT (competition_id, game_no) DO UPDATE SET
  round = EXCLUDED.round, gender = EXCLUDED.gender, home_team_id = EXCLUDED.home_team_id,
  away_team_id = EXCLUDED.away_team_id, scheduled_at = EXCLUDED.scheduled_at, venue = EXCLUDED.venue,
  city = EXCLUDED.city, broadcasters = EXCLUDED.broadcasters,
  -- 실시간 경로(Flink live-metrics)가 LIVE로, 그다음 세트 수와 함께 FINISHED로 바꿔 둔다. KOVO 일정에 결과가
  -- 올라오기 전에 도는 배치가 그것을 SCHEDULED 0:0으로 되돌리면 안 된다
  status = CASE WHEN match.status IN ('LIVE', 'FINISHED') AND EXCLUDED.status = 'SCHEDULED' THEN match.status
                ELSE EXCLUDED.status END,
  home_sets = CASE WHEN match.status IN ('LIVE', 'FINISHED') AND EXCLUDED.status = 'SCHEDULED' THEN match.home_sets
                   ELSE EXCLUDED.home_sets END,
  away_sets = CASE WHEN match.status IN ('LIVE', 'FINISHED') AND EXCLUDED.status = 'SCHEDULED' THEN match.away_sets
                   ELSE EXCLUDED.away_sets END,
  duration_min = EXCLUDED.duration_min,
  spectators = EXCLUDED.spectators, referee_main = EXCLUDED.referee_main, referee_sub = EXCLUDED.referee_sub,
  updated_at = now();

-- 이번에 올라온 모든 경기의 (시즌, KOVO 경기 번호) → 서비스 match.id 대응표(경기 번호는 시즌 안에서 유일).
-- 임시 테이블이라 트랜잭션이 끝나면 사라진다
CREATE TEMP TABLE staged_match ON COMMIT DROP AS
SELECT m.id AS match_id, s.code AS season_code, m.game_no
FROM match m
JOIN competition c ON c.id = m.competition_id
JOIN season s ON s.id = c.season_id
JOIN (SELECT DISTINCT season_code, game_no FROM staging.match) x ON x.season_code = s.code AND x.game_no = m.game_no;

INSERT INTO set_score (match_id, set_no, home_points, away_points, duration_min)
SELECT sm.match_id, st.set_no, st.home_points, st.away_points, st.duration_min
FROM staging.set_score st JOIN staged_match sm USING (season_code, game_no)
ON CONFLICT (match_id, set_no) DO UPDATE SET
  home_points = EXCLUDED.home_points, away_points = EXCLUDED.away_points, duration_min = EXCLUDED.duration_min,
  updated_at = now();

-- 다시 수집해 보니 없어진 세트 행 삭제(이번에 올라온 경기만)
DELETE FROM set_score ss USING staged_match sm
WHERE ss.match_id = sm.match_id
  AND NOT EXISTS (SELECT 1 FROM staging.set_score st
                  WHERE st.season_code = sm.season_code AND st.game_no = sm.game_no AND st.set_no = ss.set_no);

-- ---------------------------------------------------------------- 박스스코어(팀 기록, 선수 기록, 공격 종류별 기록)
INSERT INTO team_match_stat (match_id, team_id, points, opponent_errors,
  attack_attempts, attack_success, attack_blocked, attack_errors, serve_attempts, serve_aces, serve_errors,
  block_attempts, block_success, block_effective, block_assist, receive_attempts, receive_excellent, receive_errors,
  dig_attempts, dig_success, set_attempts, set_success, errors, warnings)
SELECT sm.match_id, t.id, x.points, x.opponent_errors,
  x.attack_attempts, x.attack_success, x.attack_blocked, x.attack_errors, x.serve_attempts, x.serve_aces, x.serve_errors,
  x.block_attempts, x.block_success, x.block_effective, x.block_assist, x.receive_attempts, x.receive_excellent,
  x.receive_errors, x.dig_attempts, x.dig_success, x.set_attempts, x.set_success, x.errors, x.warnings
FROM staging.team_match_stat x
JOIN staged_match sm USING (season_code, game_no)
JOIN team t ON t.code = x.team_code
ON CONFLICT (match_id, team_id) DO UPDATE SET
  points = EXCLUDED.points, opponent_errors = EXCLUDED.opponent_errors,
  attack_attempts = EXCLUDED.attack_attempts, attack_success = EXCLUDED.attack_success,
  attack_blocked = EXCLUDED.attack_blocked, attack_errors = EXCLUDED.attack_errors,
  serve_attempts = EXCLUDED.serve_attempts, serve_aces = EXCLUDED.serve_aces, serve_errors = EXCLUDED.serve_errors,
  block_attempts = EXCLUDED.block_attempts, block_success = EXCLUDED.block_success,
  block_effective = EXCLUDED.block_effective, block_assist = EXCLUDED.block_assist,
  receive_attempts = EXCLUDED.receive_attempts, receive_excellent = EXCLUDED.receive_excellent,
  receive_errors = EXCLUDED.receive_errors, dig_attempts = EXCLUDED.dig_attempts, dig_success = EXCLUDED.dig_success,
  set_attempts = EXCLUDED.set_attempts, set_success = EXCLUDED.set_success, errors = EXCLUDED.errors,
  warnings = EXCLUDED.warnings, updated_at = now();

INSERT INTO player_match_stat (match_id, player_id, team_id, back_number, position, is_captain, played_sets, points,
  attack_attempts, attack_success, attack_blocked, attack_errors, serve_attempts, serve_aces, serve_errors,
  block_attempts, block_success, block_effective, block_assist, receive_attempts, receive_excellent, receive_errors,
  dig_attempts, dig_success, set_attempts, set_success, errors, warnings)
SELECT sm.match_id, p.id, t.id, x.back_number, x.position, x.is_captain, x.played_sets, x.points,
  x.attack_attempts, x.attack_success, x.attack_blocked, x.attack_errors, x.serve_attempts, x.serve_aces, x.serve_errors,
  x.block_attempts, x.block_success, x.block_effective, x.block_assist, x.receive_attempts, x.receive_excellent,
  x.receive_errors, x.dig_attempts, x.dig_success, x.set_attempts, x.set_success, x.errors, x.warnings
FROM staging.player_match_stat x
JOIN staged_match sm USING (season_code, game_no)
JOIN player p ON p.code = x.player_code
JOIN team t ON t.code = x.team_code
ON CONFLICT (match_id, player_id) DO UPDATE SET
  team_id = EXCLUDED.team_id, back_number = EXCLUDED.back_number, position = EXCLUDED.position,
  is_captain = EXCLUDED.is_captain, played_sets = EXCLUDED.played_sets, points = EXCLUDED.points,
  attack_attempts = EXCLUDED.attack_attempts, attack_success = EXCLUDED.attack_success,
  attack_blocked = EXCLUDED.attack_blocked, attack_errors = EXCLUDED.attack_errors,
  serve_attempts = EXCLUDED.serve_attempts, serve_aces = EXCLUDED.serve_aces, serve_errors = EXCLUDED.serve_errors,
  block_attempts = EXCLUDED.block_attempts, block_success = EXCLUDED.block_success,
  block_effective = EXCLUDED.block_effective, block_assist = EXCLUDED.block_assist,
  receive_attempts = EXCLUDED.receive_attempts, receive_excellent = EXCLUDED.receive_excellent,
  receive_errors = EXCLUDED.receive_errors, dig_attempts = EXCLUDED.dig_attempts, dig_success = EXCLUDED.dig_success,
  set_attempts = EXCLUDED.set_attempts, set_success = EXCLUDED.set_success, errors = EXCLUDED.errors,
  warnings = EXCLUDED.warnings, updated_at = now();

INSERT INTO player_attack_stat (player_match_stat_id, attack_type, attempts, success, blocked, errors)
SELECT pms.id, x.attack_type, x.attempts, x.success, x.blocked, x.errors
FROM staging.player_attack_stat x
JOIN staged_match sm USING (season_code, game_no)
JOIN player p ON p.code = x.player_code
JOIN player_match_stat pms ON pms.match_id = sm.match_id AND pms.player_id = p.id
ON CONFLICT (player_match_stat_id, attack_type) DO UPDATE SET
  attempts = EXCLUDED.attempts, success = EXCLUDED.success, blocked = EXCLUDED.blocked, errors = EXCLUDED.errors,
  updated_at = now();

-- 고쳐진 박스스코어에서 사라진 행 삭제(이번에 박스스코어가 올라온 경기만).
-- Django의 on_delete=CASCADE는 DB 규칙이 아니라 Django가 처리하는 것이라, 여기서는 공격 행을 선수 행보다 먼저 지운다.
DELETE FROM player_attack_stat pas USING player_match_stat pms, staged_match sm, player p
WHERE pas.player_match_stat_id = pms.id AND pms.match_id = sm.match_id AND p.id = pms.player_id
  AND EXISTS (SELECT 1 FROM staging.player_match_stat x WHERE x.season_code = sm.season_code AND x.game_no = sm.game_no)
  AND NOT EXISTS (SELECT 1 FROM staging.player_attack_stat x
                  WHERE x.season_code = sm.season_code AND x.game_no = sm.game_no
                    AND x.player_code = p.code AND x.attack_type = pas.attack_type);

DELETE FROM player_match_stat pms USING staged_match sm, player p
WHERE pms.match_id = sm.match_id AND p.id = pms.player_id
  AND EXISTS (SELECT 1 FROM staging.player_match_stat x WHERE x.season_code = sm.season_code AND x.game_no = sm.game_no)
  AND NOT EXISTS (SELECT 1 FROM staging.player_match_stat x
                  WHERE x.season_code = sm.season_code AND x.game_no = sm.game_no AND x.player_code = p.code);

-- ---------------------------------------------------------------- 문자중계: 경기 단위로 통째로 교체
DELETE FROM rally_event re USING staged_match sm
WHERE re.match_id = sm.match_id
  AND EXISTS (SELECT 1 FROM staging.rally_event x WHERE x.season_code = sm.season_code AND x.game_no = sm.game_no);

INSERT INTO rally_event (match_id, set_no, event_no, rally_no, seq, team_id, player_id, back_number,
                         action_code, result_code, grade, home_score, away_score)
SELECT sm.match_id, x.set_no, x.event_no, x.rally_no, x.seq, t.id, p.id, x.back_number,
       x.action_code, x.result_code, x.grade, x.home_score, x.away_score
FROM staging.rally_event x
JOIN staged_match sm USING (season_code, game_no)
LEFT JOIN team t ON t.code = x.team_code
LEFT JOIN player p ON p.code = x.player_code;

-- ---------------------------------------------------------------- 파생 값
-- 활동 중인 팀 = 데이터의 가장 최근 시즌에 경기한 팀
UPDATE team SET is_active = team.id IN (
  SELECT unnest(ARRAY[m.home_team_id, m.away_team_id])
  FROM match m JOIN competition c ON c.id = m.competition_id JOIN season s ON s.id = c.season_id
  WHERE s.code = (SELECT max(s2.code) FROM season s2 JOIN competition c2 ON c2.season_id = s2.id
                  JOIN match m2 ON m2.competition_id = c2.id));

-- 선수 포지션 = 가장 최근 박스스코어의 포지션(문자중계 전용 행에는 없고, 옛 시즌 코드는 curate_kovo.py가 이미
-- 바꿔 둠). 은퇴한 선수가 오래된 행의 코드로 남지 않게 한다
UPDATE player p SET position = latest.position, updated_at = now()
FROM (SELECT DISTINCT ON (pms.player_id) pms.player_id, pms.position
      FROM player_match_stat pms JOIN match m ON m.id = pms.match_id
      WHERE pms.position <> '' AND pms.player_id IN (SELECT p2.id FROM player p2 JOIN staging.player sp ON sp.code = p2.code)
      ORDER BY pms.player_id, m.scheduled_at DESC) latest
WHERE p.id = latest.player_id AND p.position IS DISTINCT FROM latest.position
  AND NOT EXISTS (SELECT 1 FROM data_correction dc   -- 관리자의 포지션 보정(ADM-03)이 있으면 그것이 우선
                  WHERE dc.is_active AND dc.target = 'player' AND dc.key = p.code AND dc.field = 'position');

-- ---------------------------------------------------------------- 품질 문제(PIPE-10)
-- 이번 실행이 처리한 시즌에서 찾은 문제를 upsert 한다. 그 시즌의 문제 중 이번에 더 이상 안 보이는 것은 RESOLVED(해결).
-- 관리자가 ACKNOWLEDGED(확인함)로 바꾼 상태(예: KOVO 자체 데이터의 빈틈)는 문제가 남아 있는 동안 유지한다.
-- 해결됐던 문제가 다시 나타나면 OPEN으로 되돌린다.
INSERT INTO quality_issue (season_code, game_no, match_id, set_no, rule, subject, detail, severity, status, note,
                           first_seen_at, last_seen_at)
SELECT q.season_code, q.game_no, sm.match_id, q.set_no, q.rule, q.subject, q.detail, q.severity, 'OPEN', '',
       now(), now()
FROM staging.quality_issue q LEFT JOIN staged_match sm USING (season_code, game_no)
ON CONFLICT (season_code, game_no, set_no, rule, subject) DO UPDATE SET
  match_id = EXCLUDED.match_id, detail = EXCLUDED.detail, severity = EXCLUDED.severity, last_seen_at = now(),
  status = CASE WHEN quality_issue.status = 'RESOLVED' THEN 'OPEN' ELSE quality_issue.status END,
  resolved_at = NULL;

UPDATE quality_issue qi SET status = 'RESOLVED', resolved_at = now()
WHERE qi.status <> 'RESOLVED' AND qi.season_code IN (SELECT code FROM staging.season)
  AND NOT EXISTS (SELECT 1 FROM staging.quality_issue q
                  WHERE q.season_code = qi.season_code AND q.game_no = qi.game_no AND q.set_no = qi.set_no
                    AND q.rule = qi.rule AND q.subject = qi.subject);

-- ---------------------------------------------------------------- 요약(Spark 드라이버가 로그에 찍음): 시즌·상태별 경기 수 등
SELECT sm.season_code, m.status, count(*) AS matches,
       sum((SELECT count(*) FROM rally_event re WHERE re.match_id = m.id)) AS rally_events,
       sum((SELECT count(*) FROM player_match_stat p WHERE p.match_id = m.id)) AS player_rows
FROM staged_match sm JOIN match m ON m.id = sm.match_id
GROUP BY sm.season_code, m.status ORDER BY sm.season_code, m.status;
