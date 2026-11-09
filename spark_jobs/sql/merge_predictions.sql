-- Store pre-match predictions written by predict_matches.py (one transaction).

-- the model that made them; it is now the champion, so no other version keeps that alias
UPDATE model_version SET alias = '', updated_at = now()
WHERE alias = 'champion' AND (name, version) NOT IN (SELECT name, version FROM staging.model_version);

INSERT INTO model_version (name, version, mlflow_run_id, alias, metrics, train_seasons, test_season)
SELECT name, version, run_id, 'champion', metrics::jsonb, train_seasons, test_season FROM staging.model_version
ON CONFLICT (name, version) DO UPDATE SET
  alias = 'champion', metrics = EXCLUDED.metrics, train_seasons = EXCLUDED.train_seasons,
  test_season = EXCLUDED.test_season, updated_at = now();

-- predictions only for matches that have not started and start within 7 days (design.md 1.8); finished ones keep
-- their pre-match prediction. Further ahead the features (Elo, form) would be weeks stale by kick-off.
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

-- matches not started yet keep only the current champion's prediction (older versions' rows are replaced)
DELETE FROM match_prediction mp USING match m, model_version mv
WHERE m.id = mp.match_id AND mv.id = mp.model_version_id AND m.status = 'SCHEDULED' AND m.scheduled_at > now()
  AND (mv.name, mv.version) NOT IN (SELECT name, version FROM staging.model_version);

-- beyond the 7-day horizon: no prediction yet (also clears ones made before the horizon existed, 2026-11-09)
DELETE FROM match_prediction mp USING match m
WHERE m.id = mp.match_id AND m.status = 'SCHEDULED' AND m.scheduled_at > now() + interval '7 days';

-- results that came in since the last run (ANL-10)
UPDATE match_prediction mp SET is_correct = ((mp.home_win_prob >= 0.5) = (m.home_sets > m.away_sets)), updated_at = now()
FROM match m
WHERE m.id = mp.match_id AND mp.is_correct IS NULL AND m.status IN ('FINISHED', 'CONFIRMED');

SELECT 'predictions for upcoming matches' AS what, count(*) FROM match_prediction mp JOIN match m ON m.id = mp.match_id
WHERE m.status = 'SCHEDULED'
UNION ALL SELECT 'scored predictions', count(*) FROM match_prediction WHERE is_correct IS NOT NULL
UNION ALL SELECT 'correct', count(*) FROM match_prediction WHERE is_correct;
