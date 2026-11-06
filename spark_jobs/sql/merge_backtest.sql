-- Walk-forward backtest of a model version from train_model.py (ANL-10), one transaction.

-- the version itself, if predict_matches.py has not mirrored it yet (it sets the alias when it predicts)
INSERT INTO model_version (name, version, mlflow_run_id, alias, metrics, train_seasons, test_season)
SELECT name, version, run_id, '', metrics::jsonb, train_seasons, test_season FROM staging.model_version
ON CONFLICT (name, version) DO NOTHING;

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

SELECT 'backtest rows', count(*) FROM prediction_backtest pb JOIN model_version mv ON mv.id = pb.model_version_id
WHERE (mv.name, mv.version) IN (SELECT name, version FROM staging.model_version);
