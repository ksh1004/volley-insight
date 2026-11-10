-- spark_jobs/player_ratings.py의 포지션별 능력치. 한 트랜잭션으로 전체 교체: 새 경기가 들어오면 기준 집단(그 시즌과
-- 앞 두 시즌)이 바뀌므로 모든 시즌을 다시 매기고, 통산 카드는 시즌 카드들의 평균이라 함께 다시 만든다.
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

-- 통산 카드: (선수, 포지션)마다 한 행. 최고 시즌이 없을 수 있어 LEFT JOIN
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

-- 요약: 시즌 카드와 통산 카드의 포지션별 개수·평균·최저·최고 종합(자격 선수만)
SELECT 'season' AS scope, position, count(*), round(avg(overall), 1) AS avg_overall, min(overall), max(overall)
FROM player_rating WHERE qualified GROUP BY position
UNION ALL
SELECT 'career', position, count(*), round(avg(overall), 1), min(overall), max(overall)
FROM player_rating_career WHERE qualified GROUP BY position
ORDER BY 1 DESC, 2;
