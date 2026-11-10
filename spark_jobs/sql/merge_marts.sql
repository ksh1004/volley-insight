-- build_marts.py가 staging에 올린 행들로 분석 마트를 통째로 바꾼다. 한 트랜잭션이라 커밋되기 전까지 사이트는
-- 이전 마트를 계속 읽는다(빈 표를 보는 순간이 없음). staging의 KOVO 코드(시즌·팀·선수 코드)를 여기서
-- 서비스 테이블의 id(외래 키)로 바꾼다.
-- (staging. 은 pg_merge.py가 이번 실행의 스키마 이름으로 바꿔 끼운다)

-- 팀 Elo: 경기마다 두 팀의 점수 행

DELETE FROM team_rating;
INSERT INTO team_rating (team_id, season_id, match_id, rated_on, rating, rating_change, win_prob)
SELECT t.id, s.id, m.id, r.rated_on::date, r.rating, r.rating_change, r.win_prob
FROM staging.team_rating r
JOIN season s ON s.code = r.season_code
JOIN competition c ON c.season_id = s.id
JOIN match m ON m.competition_id = c.id AND m.game_no = r.game_no   -- 경기 번호는 한 시즌 안에서 유일하다
JOIN team t ON t.code = r.team_code;

-- 팀 시즌 요약: (팀, 대회)마다 한 행. 대회 = V-리그(gcode 001)의 그 시즌 리그
DELETE FROM team_season_summary;
INSERT INTO team_season_summary (team_id, competition_id, rank, matches, wins, losses, win_points, sets_won, sets_lost,
  points_won, points_lost, sideout_rate, break_rate, final_rating, attack_success_rate, attack_efficiency,
  receive_efficiency, block_per_set, serve_per_set, dig_per_set, set_per_set, error_per_set,
  attack_after_good_receive, attack_after_poor_receive, clutch_rallies, clutch_win_rate)
SELECT t.id, c.id, x.rank, x.matches, x.wins, x.losses, x.win_points, x.sets_won, x.sets_lost,
  x.points_won, x.points_lost, x.sideout_rate, x.break_rate, x.final_rating, x.attack_success_rate, x.attack_efficiency,
  x.receive_efficiency, x.block_per_set, x.serve_per_set, x.dig_per_set, x.set_per_set, x.error_per_set,
  x.attack_after_good_receive, x.attack_after_poor_receive, x.clutch_rallies, x.clutch_win_rate
FROM staging.team_season_summary x
JOIN season s ON s.code = x.season_code
JOIN competition c ON c.season_id = s.id AND c.gcode = '001' AND c.league_code = x.league_code
JOIN team t ON t.code = x.team_code;

-- 선수 시즌 요약: (선수, 대회)마다 한 행. 백분위는 JSON 문자열을 jsonb로 바꿔 넣는다
DELETE FROM player_season_summary;
INSERT INTO player_season_summary (player_id, competition_id, team_id, position, matches, sets, points, qualified,
  percentiles, attack_success_rate, attack_efficiency, receive_efficiency, block_per_set, serve_per_set, dig_per_set,
  set_per_set, error_per_set)
SELECT p.id, c.id, t.id, x.position, x.matches, x.sets, x.points, x.qualified,
  x.percentiles::jsonb, x.attack_success_rate, x.attack_efficiency, x.receive_efficiency, x.block_per_set,
  x.serve_per_set, x.dig_per_set, x.set_per_set, x.error_per_set
FROM staging.player_season_summary x
JOIN season s ON s.code = x.season_code
JOIN competition c ON c.season_id = s.id AND c.gcode = '001' AND c.league_code = x.league_code
JOIN player p ON p.code = x.player_code
JOIN team t ON t.code = x.team_code;

-- 마지막 SELECT: Spark 드라이버가 로그에 찍는 요약(테이블별 행 수)
SELECT 'team_rating' AS tbl, count(*) FROM team_rating
UNION ALL SELECT 'team_season_summary', count(*) FROM team_season_summary
UNION ALL SELECT 'player_season_summary', count(*) FROM player_season_summary;
