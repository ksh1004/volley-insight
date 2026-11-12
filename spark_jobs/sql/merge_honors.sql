-- spark_jobs/curate_honors.py의 선수별 수상·주요 기록(COL-04). 한 트랜잭션으로 전체 교체:
-- 정제 작업이 HDFS에 남은 모든 선수의 마지막 응답으로 표 전체를 다시 만들기 때문이다.
DELETE FROM player_honor;

-- 선수 코드로 player 행을 찾아 연결한다(우리 DB에 없는 선수의 기록은 버린다)
INSERT INTO player_honor (player_id, kind, seq, season_years, title, text)
SELECT p.id, x.kind, x.seq, coalesce(x.season_years, ''), x.title, x.text
FROM staging.player_honor x
JOIN player p ON p.code = x.player_code;

-- 요약: 행 수와 기록이 있는 선수 수
SELECT 'player_honor' AS t, count(*) FROM player_honor
UNION ALL SELECT 'players with honors', count(DISTINCT player_id) FROM player_honor;
