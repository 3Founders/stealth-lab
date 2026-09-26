"""Fixed prior scales shared by model.py (NumPyro) and predict.py (numpy). Kept in a
JAX-free module so the API process can import them."""
SIGMA_NEW_VERSION = 0.5      # a new model version: ability ~ N(predecessor's, 0.5^2)
SUCCESSOR_Z_SD = 0.3         # ... and skill profile ~ N(predecessor's, 0.3^2 I)
TAU_GAMMA = 0.5              # scaffold effects ~ N(0, 0.5^2)
TAU_DELTA = 0.3              # model x scaffold residuals ~ N(0, 0.3^2)


def effective_dims(n_models: int, k: int) -> int:
    """Skill dimensions the data can inform: a Goal x model interaction beyond the main
    effects has rank at most (models - 1)."""
    return max(0, min(k, n_models - 1))
