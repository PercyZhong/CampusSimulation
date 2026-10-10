"""Local rule experiments and result export."""

import argparse
import asyncio
import json
from pathlib import Path

from .analysis import export, verify_resume
from .plan import build


def _config(path, seed=None, days=None):
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    if seed is not None:
        config["seed"] = seed
    if days is not None:
        config["end"] = days
    return config


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--config", required=True)
    run.add_argument("--output", required=True)
    run.add_argument("--seed", type=int)
    run.add_argument("--days", type=int)
    run.add_argument("--source")
    run.add_argument("--step", type=int)
    batch = sub.add_parser("batch")
    batch.add_argument("--configs", default="configs")
    batch.add_argument("--output", default="runs/experiment")
    analyzed = sub.add_parser("export")
    analyzed.add_argument("run_dir")
    check = sub.add_parser("verify-resume")
    check.add_argument("original")
    check.add_argument("resumed")
    args = parser.parse_args(argv)
    if args.command == "export":
        print(json.dumps(export(args.run_dir), ensure_ascii=False))
        return 0
    if args.command == "verify-resume":
        print(json.dumps(verify_resume(args.original, args.resumed)))
        return 0
    from society0.kernel.runner import run_plan
    if args.command == "run":
        config = _config(args.config, args.seed, args.days)
        result = asyncio.run(run_plan(args.output, build(config), source=args.source, step=args.step))
        print(json.dumps({"runner": result, "summary": export(args.output)}, ensure_ascii=False))
        return 0
    root = Path(args.output)
    summaries = []
    for condition in ("baseline", "outreach", "easier_access"):
        for seed in (11, 22, 33):
            config = _config(Path(args.configs) / f"{condition}.json", seed)
            path = root / f"{condition}-seed{seed}"
            asyncio.run(run_plan(path, build(config)))
            summaries.append(export(path))
    aggregate = {}
    for condition in ("baseline", "outreach", "easier_access"):
        subset = [s for s in summaries if s["condition"] == condition]
        aggregate[condition] = {}
        for metric in ("requested_count", "requested_rate", "supported_count", "supported_rate",
                       "completed_wait_mean_days", "low_connection_7d_count",
                       "low_connection_7d_rate"):
            values = [s[metric] for s in subset if s[metric] is not None]
            aggregate[condition][metric] = {"mean": sum(values) / len(values) if values else None,
                                            "min": min(values) if values else None,
                                            "max": max(values) if values else None,
                                            "n_seeds": len(values)}
    comparison = {"schema_version": 1, "runs": summaries, "by_condition": aggregate,
                  "interpretation": "uncalibrated model-internal scenario results; three seeds imply no significance"}
    root.mkdir(parents=True, exist_ok=True)
    (root / "comparison.json").write_text(json.dumps(comparison, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"runs": len(summaries), "comparison": str(root / "comparison.json")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
