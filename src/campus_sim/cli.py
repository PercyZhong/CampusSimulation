"""Local rule experiments and result export."""

import argparse
import asyncio
import json
from pathlib import Path

from .analysis import export, verify_resume
from .naming import run_stem
from .plan import build


def _config(path, seed=None, days=None, provider=None, model=None, max_calls=None, temperature=None):
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    if seed is not None:
        config["seed"] = seed
    if days is not None:
        config["end"] = days
    if provider is not None:
        config.update(driver="llm", model_provider=provider, model_id=model,
                      max_model_calls=max_calls)
        if temperature is not None:
            config["model_temperature"] = temperature
    return config


def _model_options(parser):
    parser.add_argument("--provider", choices=("deepseek", "qwen", "openai", "gemini", "anthropic"))
    parser.add_argument("--model")
    parser.add_argument("--max-model-calls", type=int)
    parser.add_argument("--temperature", type=float, default=None)


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
    _model_options(run)
    batch = sub.add_parser("batch")
    batch.add_argument("--configs", default="configs")
    batch.add_argument("--plan")
    batch.add_argument("--output", default="runs/experiment")
    _model_options(batch)
    analyzed = sub.add_parser("export")
    analyzed.add_argument("run_dir")
    trajectory = sub.add_parser("trajectory-report")
    trajectory.add_argument("run_dir")
    check = sub.add_parser("verify-resume")
    check.add_argument("original")
    check.add_argument("resumed")
    matrix = sub.add_parser("matrix-report")
    matrix.add_argument("--root", default="runs")
    matrix_run_v069 = sub.add_parser("matrix-run-v069")
    matrix_run_v069.add_argument("--root", default="runs")
    matrix_run_v069.add_argument("--driver", choices=("rule", "qwen", "deepseek"),
                                 default="rule")
    matrix_report_v069 = sub.add_parser("matrix-report-v069")
    matrix_report_v069.add_argument("--root", default="runs")
    matrix_report_v069.add_argument("--driver", choices=("rule", "qwen", "deepseek"),
                                    default="rule")
    matrix_run_v070 = sub.add_parser("matrix-run-v070")
    matrix_run_v070.add_argument("--root", default="runs")
    matrix_run_v070.add_argument("--driver", choices=("rule", "qwen", "deepseek"),
                                 default="rule")
    matrix_report_v070 = sub.add_parser("matrix-report-v070")
    matrix_report_v070.add_argument("--root", default="runs")
    matrix_report_v070.add_argument("--driver", choices=("rule", "qwen", "deepseek"),
                                    default="rule")
    matrix_run_v073 = sub.add_parser("matrix-run-v073")
    matrix_run_v073.add_argument("--root", default="runs")
    matrix_run_v073.add_argument("--driver", choices=("rule", "qwen", "deepseek"), default="rule")
    matrix_report_v073 = sub.add_parser("matrix-report-v073")
    matrix_report_v073.add_argument("--root", default="runs")
    matrix_report_v073.add_argument("--driver", choices=("rule", "qwen", "deepseek"), default="rule")
    web = sub.add_parser("web")
    web.add_argument("--output", default="runs/workbench")
    web.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    if args.command in {"run", "batch"}:
        if args.provider is None and (args.model is not None or args.max_model_calls is not None or args.temperature is not None):
            parser.error("--model and --max-model-calls require --provider")
        if args.provider is not None and (args.model is None or args.max_model_calls is None):
            parser.error("--provider requires --model and --max-model-calls")
    if args.command == "web":
        from .web import serve
        serve(args.output, args.port)
        return 0
    if args.command == "export":
        print(json.dumps(export(args.run_dir), ensure_ascii=False))
        return 0
    if args.command == "trajectory-report":
        from .analysis import trajectory_report
        print(json.dumps(trajectory_report(args.run_dir), ensure_ascii=False))
        return 0
    if args.command == "verify-resume":
        print(json.dumps(verify_resume(args.original, args.resumed)))
        return 0
    if args.command == "matrix-report":
        from .matrix import build_report
        report = build_report(args.root)
        output = Path(args.root) / "v063-matrix-report.json"
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps({"runs": report["found_runs"], "report": str(output)}, ensure_ascii=False))
        return 0
    if args.command == "matrix-run-v069":
        from .matrix_v069 import run_matrix
        completed = run_matrix(args.root, args.driver)
        print(json.dumps({"driver": args.driver, "runs": len(completed)}, ensure_ascii=False))
        return 0
    if args.command == "matrix-report-v069":
        from .matrix_v069 import build_report
        report = build_report(args.root, args.driver)
        output = Path(args.root) / f"v069-matrix-{args.driver}-report.json"
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8")
        print(json.dumps({"driver": args.driver, "runs": report["found_runs"],
                          "report": str(output)}, ensure_ascii=False))
        return 0
    if args.command == "matrix-run-v070":
        from .matrix_v070 import run_matrix
        completed = run_matrix(args.root, args.driver)
        print(json.dumps({"driver": args.driver, "runs": len(completed)}, ensure_ascii=False))
        return 0
    if args.command == "matrix-report-v070":
        from .matrix_v070 import build_report
        report = build_report(args.root, args.driver)
        output = Path(args.root) / f"v070-matrix-{args.driver}-report.json"
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8")
        print(json.dumps({"driver": args.driver, "runs": report["found_runs"],
                          "report": str(output)}, ensure_ascii=False))
        return 0
    if args.command == "matrix-run-v073":
        from .matrix_v073 import run_matrix
        completed = run_matrix(args.root, args.driver)
        print(json.dumps({"driver": args.driver, "runs": len(completed)}, ensure_ascii=False))
        return 0
    if args.command == "matrix-report-v073":
        from .matrix_v073 import build_report
        report = build_report(args.root, args.driver)
        output = Path(args.root) / f"v073-matrix-{args.driver}-report.json"
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps({"driver": args.driver, "runs": report["found_runs"], "report": str(output)}, ensure_ascii=False))
        return 0
    from society0.kernel.runner import run_plan
    if args.command == "run":
        config = _config(args.config, args.seed, args.days,
                         args.provider, args.model, args.max_model_calls, args.temperature)
        result = asyncio.run(run_plan(args.output, build(config), source=args.source, step=args.step))
        print(json.dumps({"runner": result, "summary": export(args.output)}, ensure_ascii=False))
        return 0
    root = Path(args.output)
    summaries = []
    if args.plan:
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        conditions = plan["conditions"]
        seeds = plan["seeds"]
        runs = plan["runs"]
    else:
        conditions = ("baseline", "outreach", "easier_access", "targeted_outreach")
        seeds = (11, 22, 33)
        runs = []
        for condition in conditions:
            for seed in seeds:
                config = _config(Path(args.configs) / f"{condition}.json", seed,
                                 provider=args.provider, model=args.model,
                                 max_calls=args.max_model_calls, temperature=args.temperature)
                runs.append({"name": run_stem(condition, seed, config), "config": config})
    for item in runs:
        path = root / item["name"]
        asyncio.run(run_plan(path, build(item["config"])))
        summaries.append(export(path))
    aggregate = {}
    for condition in conditions:
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
        for metric in ("stress_change_distribution", "mood_distribution"):
            values = [s[metric]["mean"] for s in subset if metric in s]
            aggregate[condition][metric + "_mean"] = {
                "mean": sum(values) / len(values) if values else None,
                "min": min(values) if values else None,
                "max": max(values) if values else None,
                "n_seeds": len(values),
            }
    comparison = {"schema_version": 1, "runs": summaries, "by_condition": aggregate,
                  "conditions": list(conditions), "seeds": list(seeds),
                  "interpretation": "uncalibrated model-internal scenario results; seed repeats imply no significance"}
    root.mkdir(parents=True, exist_ok=True)
    (root / "comparison.json").write_text(json.dumps(comparison, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"runs": len(summaries), "comparison": str(root / "comparison.json")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
