"""Readable, filesystem-safe experiment names."""


def run_stem(condition, seed, config):
    """Place the decision driver immediately after the policy name."""
    if config.get("driver") == "llm":
        driver = f"llm-{config['model_provider']}-{config['model_id']}"
    else:
        driver = "rule"
    scenario = ("social-" if config.get("scenario") == "social_competition" else
                f"bullying-{config.get('bullying_teacher_strategy', 'none')}-"
                if config.get("scenario") == "school_bullying" else "")
    return f"{scenario}{condition}-{driver}-seed{seed}"
