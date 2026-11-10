-- spark_jobs/player_types.py의 선수 유형(그룹)과 선수별 스타일(ANL-06). 한 트랜잭션으로 전체 교체:
-- 그룹 번호는 실행마다 바뀔 수 있어서 옛 행과 합치지 않고 통째로 바꾼다.
-- player_style이 player_type을 참조하므로 자식(player_style)부터 지운다.
DELETE FROM player_style;
DELETE FROM player_type;

INSERT INTO player_type (gender, cluster_no, label, position, traits, centroid, size, k, silhouette, mlflow_run_id)
SELECT gender, cluster_no, label, coalesce(position, ''), traits::jsonb, centroid::jsonb, size, k, silhouette,
       coalesce(mlflow_run_id, '')
FROM staging.player_type;

-- 선수-시즌마다 한 행. (성별, 그룹 번호)로 방금 넣은 유형 행의 id를 찾아 연결한다
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

-- 요약: 테이블별 행 수
SELECT 'player_type' AS t, count(*) FROM player_type
UNION ALL SELECT 'player_style', count(*) FROM player_style;
