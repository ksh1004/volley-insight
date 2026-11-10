-- train_model.py가 만든 모델 버전 하나의 워크포워드 백테스트 결과(ANL-10)를 한 트랜잭션으로 넣는다.

-- 모델 버전 행: predict_matches.py가 아직 옮겨 놓지 않았으면 여기서 만든다(별칭은 예측할 때 그쪽이 채움)
INSERT INTO model_version (name, version, mlflow_run_id, alias, metrics, train_seasons, test_season)
SELECT name, version, run_id, '', metrics::jsonb, train_seasons, test_season FROM staging.model_version
ON CONFLICT (name, version) DO NOTHING;

-- 같은 버전의 이전 백테스트 행을 지우고 새로 넣는다(다시 학습해도 중복되지 않게)
DELETE FROM prediction_backtest pb USING model_version mv
WHERE mv.id = pb.model_version_id AND (mv.name, mv.version) IN (SELECT name, version FROM staging.model_version);

INSERT INTO prediction_backtest (match_id, model_version_id, home_win_prob, elo_home_win_prob, pred_home_sets,
                                 pred_away_sets, home_won, train_seasons)
SELECT m.id, mv.id, b.home_win_prob, b.elo_home_win_prob, b.pred_home_sets, b.pred_away_sets, b.home_won, b.train_seasons
FROM staging.prediction_backtest b
JOIN season s ON s.code = b.season_code
JOIN competition c ON c.season_id = s.id
JOIN match m ON m.competition_id = c.id AND m.game_no = b.game_no
JOIN model_version mv ON (mv.name, mv.version) = (SELECT name, version FROM staging.model_version)
ON CONFLICT (match_id, model_version_id) DO NOTHING;

-- 요약: 이 버전의 백테스트 행 수
SELECT 'backtest rows', count(*) FROM prediction_backtest pb JOIN model_version mv ON mv.id = pb.model_version_id
WHERE (mv.name, mv.version) IN (SELECT name, version FROM staging.model_version);
