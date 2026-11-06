-- Replace the analysis marts with the rows staged by build_marts.py. One transaction: until it commits,
-- the site keeps reading the previous marts. KOVO codes in staging become foreign keys here.

DELETE FROM team_rating;
INSERT INTO team_rating (team_id, season_id, match_id, rated_on, rating, rating_change, win_prob)
SELECT t.id, s.id, m.id, r.rated_on::date, r.rating, r.rating_change, r.win_prob
FROM staging.team_rating r
JOIN season s ON s.code = r.season_code
JOIN competition c ON c.season_id = s.id
JOIN match m ON m.competition_id = c.id AND m.game_no = r.game_no   -- game numbers are unique within a season
JOIN team t ON t.code = r.team_code;

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

-- summary printed by the Spark driver
SELECT 'team_rating' AS tbl, count(*) FROM team_rating
UNION ALL SELECT 'team_season_summary', count(*) FROM team_season_summary
UNION ALL SELECT 'player_season_summary', count(*) FROM player_season_summary;
