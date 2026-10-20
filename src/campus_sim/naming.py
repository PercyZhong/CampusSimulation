"""Readable, filesystem-safe experiment names."""


def run_stem(condition, seed, config):
    """Place the decision driver immediately after the policy name."""
    if config.get("driver") == "llm":
        driver = f"llm-{config['model_provider']}-{config['model_id']}"
    else:
        driver = "rule"
    return f"{condition}-{driver}-seed{seed}"
