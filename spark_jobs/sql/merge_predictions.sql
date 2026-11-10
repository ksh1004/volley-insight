-- predict_matches.py가 만든 경기 전 예측을 저장한다(한 트랜잭션).

-- 예측을 만든 모델 버전: 이제 이 버전이 챔피언이므로 다른 버전에서는 champion 별칭을 지운다
UPDATE model_version SET alias = '', updated_at = now()
WHERE alias = 'champion' AND (name, version) NOT IN (SELECT name, version FROM staging.model_version);

INSERT INTO model_version (name, version, mlflow_run_id, alias, metrics, train_seasons, test_season)
SELECT name, version, run_id, 'champion', metrics::jsonb, train_seasons, test_season FROM staging.model_version
ON CONFLICT (name, version) DO UPDATE SET
  alias = 'champion', metrics = EXCLUDED.metrics, train_seasons = EXCLUDED.train_seasons,
  test_season = EXCLUDED.test_season, updated_at = now();

-- 아직 시작 안 했고 7일 안에 열리는 경기만 예측을 넣는다(설계서 1.8). 끝난 경기는 경기 전 예측을 그대로 둔다.
-- 더 먼 경기는 경기 날이면 특징(Elo, 폼)이 몇 주나 묵은 값이 되므로 예측하지 않는다.
INSERT INTO match_prediction (match_id, model_version_id, home_win_prob, set_score_probs, pred_home_sets, pred_away_sets,
                              predicted_at, is_correct)
SELECT m.id, mv.id, p.home_win_prob, p.set_score_probs::jsonb, p.pred_home_sets, p.pred_away_sets, now(), NULL
FROM staging.match_prediction p
JOIN season s ON s.code = p.season_code
JOIN competition c ON c.season_id = s.id
JOIN match m ON m.competition_id = c.id AND m.game_no = p.game_no
JOIN model_version mv ON (mv.name, mv.version) = (SELECT name, version FROM staging.model_version)
WHERE m.status = 'SCHEDULED' AND m.scheduled_at > now() AND m.scheduled_at <= now() + interval '7 days'
ON CONFLICT (match_id, model_version_id) DO UPDATE SET
  home_win_prob = EXCLUDED.home_win_prob, set_score_probs = EXCLUDED.set_score_probs,
  pred_home_sets = EXCLUDED.pred_home_sets, pred_away_sets = EXCLUDED.pred_away_sets,
  predicted_at = EXCLUDED.predicted_at, updated_at = now();

-- 아직 시작 안 한 경기는 현재 챔피언의 예측만 남긴다(이전 버전이 만든 행은 지움)
DELETE FROM match_prediction mp USING match m, model_version mv
WHERE m.id = mp.match_id AND mv.id = mp.model_version_id AND m.status = 'SCHEDULED' AND m.scheduled_at > now()
  AND (mv.name, mv.version) NOT IN (SELECT name, version FROM staging.model_version);

-- 7일보다 먼 경기: 아직 예측 없음(7일 제한이 생기기 전에 만들어진 예측도 여기서 지운다, 2026-11-09)
DELETE FROM match_prediction mp USING match m
WHERE m.id = mp.match_id AND m.status = 'SCHEDULED' AND m.scheduled_at > now() + interval '7 days';

-- 지난 실행 이후 결과가 나온 경기 채점(ANL-10): 홈 승리 확률 0.5 이상 = 홈 승 예측
UPDATE match_prediction mp SET is_correct = ((mp.home_win_prob >= 0.5) = (m.home_sets > m.away_sets)), updated_at = now()
FROM match m
WHERE m.id = mp.match_id AND mp.is_correct IS NULL AND m.status IN ('FINISHED', 'CONFIRMED');

-- 요약: 예정 경기 예측 수, 채점된 예측 수, 적중 수
SELECT 'predictions for upcoming matches' AS what, count(*) FROM match_prediction mp JOIN match m ON m.id = mp.match_id
WHERE m.status = 'SCHEDULED'
UNION ALL SELECT 'scored predictions', count(*) FROM match_prediction WHERE is_correct IS NOT NULL
UNION ALL SELECT 'correct', count(*) FROM match_prediction WHERE is_correct;
