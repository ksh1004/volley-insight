-- Position ratings from spark_jobs/player_ratings.py. Full refresh in one transaction: every season is re-scored
-- because the reference (this season and the two before) moves as new games come in, and the career cards are
-- averages of the season cards.
DELETE FROM player_rating;

INSERT INTO player_rating (player_id, season_id, team_id, gender, position, sets, qualified, provisional, overall,
                           abilities, extra)
SELECT p.id, s.id, t.id, x.gender, x.position, x.sets, x.qualified, x.provisional, x.overall, x.abilities::jsonb,
       x.extra::jsonb
FROM staging.player_rating x
JOIN player p ON p.code = x.player_code
JOIN season s ON s.code = x.season_code
JOIN team t ON t.code = x.team_code
ON CONFLICT (player_id, season_id) DO NOTHING;

DELETE FROM player_rating_career;

INSERT INTO player_rating_career (player_id, gender, position, seasons, qualified_seasons, sets, first_season_id,
                                  last_season_id, best_season_id, best_overall, qualified, overall, abilities, extra)
SELECT p.id, x.gender, x.position, x.seasons, x.qualified_seasons, x.sets, f.id, l.id, b.id, x.best_overall,
       x.qualified, x.overall, x.abilities::jsonb, x.extra::jsonb
FROM staging.player_rating_career x
JOIN player p ON p.code = x.player_code
JOIN season f ON f.code = x.first_season
JOIN season l ON l.code = x.last_season
LEFT JOIN season b ON b.code = x.best_season
ON CONFLICT (player_id, position) DO NOTHING;

SELECT 'season' AS scope, position, count(*), round(avg(overall), 1) AS avg_overall, min(overall), max(overall)
FROM player_rating WHERE qualified GROUP BY position
UNION ALL
SELECT 'career', position, count(*), round(avg(overall), 1), min(overall), max(overall)
FROM player_rating_career WHERE qualified GROUP BY position
ORDER BY 1 DESC, 2;
