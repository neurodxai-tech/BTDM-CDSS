# weights/

Optional. The usual way to supply the model is `MODEL_TRAINING_DIR` in `.env`
(see the top-level README): the worker imports and validates checkpoints from
your training folder automatically.

Alternatively, place the Seed 42 checkpoint here as `classifier.pt`. On first
start it is registered and pinned as the active model.

Expected checkpoint: internal name `best_model_seed42`, 25,057,731 parameters,
SHA-256 `604e1b4191cd27fc14a8500d613578a137f31ad98b742b765e6a59ba794abce2`.

Checkpoint files are never committed to git (see `.gitignore`).
