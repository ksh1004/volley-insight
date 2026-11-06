-- Merge the curated KOVO tables in schema `staging` (written by curate_kovo.py) into the serving tables.
-- Runs in one transaction. Staging rows carry KOVO codes; this script turns them into foreign keys.
-- Re-running with the same staging data gives the same result (upserts + removal of rows that disappeared).

-- ---------------------------------------------------------------- reference data
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

-- Teams and players: one staging row per season. Keep the most recent one, and never let an older
-- season (backfill) overwrite newer names.
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
  name = COALESCE(NULLIF(EXCLUDED.name, ''), player.name),           -- play-by-play-only rows have no name
  gender = EXCLUDED.gender,
  position = COALESCE(NULLIF(EXCLUDED.position, ''), player.position),
  height_cm = COALESCE(EXCLUDED.height_cm, player.height_cm),
  last_seen_on = EXCLUDED.last_seen_on,
  updated_at = now()
WHERE player.last_seen_on IS NULL OR EXCLUDED.last_seen_on >= player.last_seen_on;

-- ---------------------------------------------------------------- matches
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
  -- the live pipeline (Flink live-metrics) sets LIVE, then FINISHED with the set count; a batch run before KOVO's
  -- schedule has the result must not reset that to SCHEDULED 0:0
  status = CASE WHEN match.status IN ('LIVE', 'FINISHED') AND EXCLUDED.status = 'SCHEDULED' THEN match.status
                ELSE EXCLUDED.status END,
  home_sets = CASE WHEN match.status IN ('LIVE', 'FINISHED') AND EXCLUDED.status = 'SCHEDULED' THEN match.home_sets
                   ELSE EXCLUDED.home_sets END,
  away_sets = CASE WHEN match.status IN ('LIVE', 'FINISHED') AND EXCLUDED.status = 'SCHEDULED' THEN match.away_sets
                   ELSE EXCLUDED.away_sets END,
  duration_min = EXCLUDED.duration_min,
  spectators = EXCLUDED.spectators, referee_main = EXCLUDED.referee_main, referee_sub = EXCLUDED.referee_sub,
  updated_at = now();

-- KOVO game number -> our match id for every staged game (a game number is unique within a season)
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

DELETE FROM set_score ss USING staged_match sm
WHERE ss.match_id = sm.match_id
  AND NOT EXISTS (SELECT 1 FROM staging.set_score st
                  WHERE st.season_code = sm.season_code AND st.game_no = sm.game_no AND st.set_no = ss.set_no);

-- ---------------------------------------------------------------- box scores
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

-- Rows that disappeared from a corrected box score, only for games whose box score was staged.
-- Django's on_delete=CASCADE is not a database rule, so attack rows go before their player rows.
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

-- ---------------------------------------------------------------- play-by-play: replace per game
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

-- ---------------------------------------------------------------- derived flags
UPDATE team SET is_active = team.id IN (
  SELECT unnest(ARRAY[m.home_team_id, m.away_team_id])
  FROM match m JOIN competition c ON c.id = m.competition_id JOIN season s ON s.id = c.season_id
  WHERE s.code = (SELECT max(s2.code) FROM season s2 JOIN competition c2 ON c2.season_id = s2.id
                  JOIN match m2 ON m2.competition_id = c2.id));

-- a player's position = the one in their latest box score (play-by-play-only rows have none, and old seasons'
-- codes are already mapped by curate_kovo.py), so a retired player is not left with a code from an old row
UPDATE player p SET position = latest.position, updated_at = now()
FROM (SELECT DISTINCT ON (pms.player_id) pms.player_id, pms.position
      FROM player_match_stat pms JOIN match m ON m.id = pms.match_id
      WHERE pms.position <> '' AND pms.player_id IN (SELECT p2.id FROM player p2 JOIN staging.player sp ON sp.code = p2.code)
      ORDER BY pms.player_id, m.scheduled_at DESC) latest
WHERE p.id = latest.player_id AND p.position IS DISTINCT FROM latest.position
  AND NOT EXISTS (SELECT 1 FROM data_correction dc   -- an admin's position correction (ADM-03) wins
                  WHERE dc.is_active AND dc.target = 'player' AND dc.key = p.code AND dc.field = 'position');

-- ---------------------------------------------------------------- quality issues (PIPE-10)
-- Upsert what this run found for its seasons. Issues of those seasons that it no longer finds become RESOLVED.
-- An admin's ACKNOWLEDGED status (e.g. a gap in KOVO's own data) is kept while the issue is still there.
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

-- ---------------------------------------------------------------- summary (printed by the Spark driver)
SELECT sm.season_code, m.status, count(*) AS matches,
       sum((SELECT count(*) FROM rally_event re WHERE re.match_id = m.id)) AS rally_events,
       sum((SELECT count(*) FROM player_match_stat p WHERE p.match_id = m.id)) AS player_rows
FROM staged_match sm JOIN match m ON m.id = sm.match_id
GROUP BY sm.season_code, m.status ORDER BY sm.season_code, m.status;
