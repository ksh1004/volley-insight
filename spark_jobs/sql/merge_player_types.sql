-- Player types and styles from spark_jobs/player_types.py (ANL-06). Full refresh in one transaction:
-- cluster numbers change between runs, so old rows are replaced, never merged.
DELETE FROM player_style;
DELETE FROM player_type;

INSERT INTO player_type (gender, cluster_no, label, position, traits, centroid, size, k, silhouette, mlflow_run_id)
SELECT gender, cluster_no, label, coalesce(position, ''), traits::jsonb, centroid::jsonb, size, k, silhouette,
       coalesce(mlflow_run_id, '')
FROM staging.player_type;

INSERT INTO player_style (player_id, season_id, team_id, type_id, sets, provisional, features, z, similar_season,
                          similar_all)
SELECT p.id, s.id, t.id, pt.id, x.sets, x.provisional, x.features::jsonb, x.z::jsonb, x.similar_season::jsonb,
       x.similar_all::jsonb
FROM staging.player_style x
JOIN player p ON p.code = x.player_code
JOIN season s ON s.code = x.season_code
JOIN team t ON t.code = x.team_code
JOIN player_type pt ON pt.gender = x.gender AND pt.cluster_no = x.cluster_no
ON CONFLICT (player_id, season_id) DO NOTHING;

SELECT 'player_type' AS t, count(*) FROM player_type
UNION ALL SELECT 'player_style', count(*) FROM player_style;
