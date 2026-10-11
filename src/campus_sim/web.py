"""Loopback-only workbench for synthetic Society0 experiments."""

import json
import re
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

CONDITIONS = ("baseline", "outreach", "easier_access")
SEEDS = (11, 22, 33)
RUN_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}\Z")
PAGE = """<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CampusSimulation</title><style>body{font:16px system-ui;margin:2rem auto;max-width:1050px;padding:0 1rem;background:#f5f7fb;color:#172337}h1{margin-bottom:0}small{color:#526279}.box{background:white;padding:1.2rem;border-radius:12px;margin:1rem 0;box-shadow:0 1px 4px #ccd4e0}button,select,input{font:inherit;padding:.5rem;margin:.25rem}button{background:#1756a9;color:white;border:0;border-radius:6px;cursor:pointer}table{border-collapse:collapse;width:100%}td,th{padding:.5rem;border-bottom:1px solid #dce2eb;text-align:left}pre{white-space:pre-wrap;overflow:auto}canvas{max-width:100%}</style>
<h1>CampusSimulation</h1><small>未校准的合成校园情景；不能用于真实学生诊断、个人风险判断或政策效果预测。</small>
<div class="box"><h2>运行实验</h2><label>条件 <select id="condition"><option>baseline</option><option>outreach</option><option>easier_access</option></select></label><label>种子 <select id="seed"><option>11</option><option>22</option><option>33</option></select></label><label>天数 <input id="days" type="number" min="1" max="30" value="30"></label><button onclick="launch()">启动新运行</button><button onclick="launchBatch()">启动 3×3 对照</button><p id="message"></p></div>
<div class="box"><h2>已有运行</h2><button onclick="refresh()">刷新</button><div id="jobs"></div><table><thead><tr><th>名称</th><th>条件</th><th>天数</th><th>求助率</th><th>支持率</th><th>等待天数</th></tr></thead><tbody id="runs"></tbody></table></div>
<div class="box"><h2>批处理结果</h2><div id="batches">暂无结果。</div></div>
<div class="box"><h2>选中运行</h2><div id="detail">请选择一个运行。</div><canvas id="chart" width="900" height="260"></canvas></div>
<script>
const j=async u=>{const r=await fetch(u);if(!r.ok)throw Error(await r.text());return r.json()};
const pct=x=>x==null?'—':(100*x).toFixed(1)+'%';
async function refresh(){try{let data=await j('/api/runs');let body=document.getElementById('runs');body.replaceChildren();for(let x of data.runs){let tr=document.createElement('tr');let values=[x.name,x.condition,x.complete_steps,pct(x.requested_rate),pct(x.supported_rate),x.completed_wait_mean_days??'—'];for(let v of values){let td=document.createElement('td');td.textContent=v;tr.append(td)}tr.style.cursor='pointer';tr.onclick=()=>show(x.name);body.append(tr)}document.getElementById('jobs').textContent=data.jobs.map(x=>x.name+'：'+x.status+(x.error?'（查看服务器状态文件）':'')).join('；');let b=document.getElementById('batches');b.replaceChildren();for(let run of data.batches){let p=document.createElement('p');p.textContent=run.name+'：'+run.status;b.append(p);if(run.by_condition){let table=document.createElement('table');let h=document.createElement('tr');for(let label of ['条件','平均求助率','平均支持率','平均等待天数','平均持续低联结率']){let th=document.createElement('th');th.textContent=label;h.append(th)}table.append(h);for(let [condition,m] of Object.entries(run.by_condition)){let tr=document.createElement('tr');for(let value of [condition,pct(m.requested_rate.mean),pct(m.supported_rate.mean),m.completed_wait_mean_days.mean?.toFixed(2)??'—',pct(m.low_connection_7d_rate.mean)]){let td=document.createElement('td');td.textContent=value;tr.append(td)}table.append(tr)}b.append(table)}}}catch(e){document.getElementById('message').textContent=e.message}}
async function show(name){let x=await j('/api/runs/'+name);let d=document.getElementById('detail');d.textContent=`${x.summary.condition} · seed ${x.summary.seed} · ${x.summary.complete_steps} 天 · 持续低联结 ${pct(x.summary.low_connection_7d_rate)} · 压力均值 ${x.summary.stress_distribution.mean.toFixed(3)} · 压力平均变化 ${x.summary.stress_change_distribution?.mean?.toFixed(3)??'旧版无数据'}`;let c=document.getElementById('chart'),ctx=c.getContext('2d');ctx.clearRect(0,0,c.width,c.height);let days=x.days;if(!days.length)return;ctx.strokeStyle='#1756a9';ctx.lineWidth=3;ctx.beginPath();days.forEach((p,i)=>{let X=35+i*(c.width-55)/Math.max(1,days.length-1),Y=230-p.mean_stress*195;i?ctx.lineTo(X,Y):ctx.moveTo(X,Y)});ctx.stroke();ctx.fillStyle='#172337';ctx.fillText('逐日平均压力（模型变量）',35,22);ctx.fillText('0',10,230);ctx.fillText('1',10,35)}
async function launch(){let m=document.getElementById('message');try{let r=await fetch('/api/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({condition:document.getElementById('condition').value,seed:Number(document.getElementById('seed').value),days:Number(document.getElementById('days').value)})});let x=await r.json();if(!r.ok)throw Error(x.error);m.textContent='已启动 '+x.name+'；请稍后刷新。';}catch(e){m.textContent=e.message}}refresh();
async function launchBatch(){let m=document.getElementById('message');try{let r=await fetch('/api/batches',{method:'POST'});let x=await r.json();if(!r.ok)throw Error(x.error);m.textContent='已启动 '+x.name+'；请稍后刷新。'}catch(e){m.textContent=e.message}}
</script></html>"""


def create_handler(root):
    root = Path(root).resolve()
    project = Path(__file__).resolve().parents[2]

    def spawn(command, name):
        def work():
            result = subprocess.run(command, cwd=project, capture_output=True, text=True,
                                    check=False)
            status = "complete" if result.returncode == 0 else "failed"
            (root / f"{name}.status.json").write_text(json.dumps({
                "status": status, "returncode": result.returncode,
                "error": result.stderr[-3000:] if result.returncode else None,
            }), encoding="utf-8")
        (root / f"{name}.status.json").write_text('{"status":"running"}', encoding="utf-8")
        threading.Thread(target=work, daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def allowed_host(self):
            host = self.headers.get("Host", "").split(":", 1)[0]
            return host in {"127.0.0.1", "localhost"}

        def send_data(self, status, value, content_type="application/json; charset=utf-8"):
            payload = (value.encode("utf-8") if isinstance(value, str) else
                       json.dumps(value, ensure_ascii=False).encode("utf-8"))
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if not self.allowed_host():
                return self.send_data(403, {"error": "loopback host required"})
            path = urlsplit(self.path).path
            if path == "/":
                return self.send_data(200, PAGE, "text/html; charset=utf-8")
            if path == "/api/runs":
                runs = []
                summary_files = list(root.glob("*/summary.json")) + list(
                    root.glob("batch-*/*/summary.json"))
                for summary_file in sorted(summary_files):
                    try:
                        summary = json.loads(summary_file.read_text(encoding="utf-8"))
                        name = summary_file.parent.relative_to(root).as_posix()
                        status_file = root / f"{name}.status.json"
                        run_status = (json.loads(status_file.read_text(encoding="utf-8"))
                                      if status_file.is_file() else {"status": "complete"})
                        runs.append({"name": name, **summary, "run_status": run_status["status"]})
                    except (ValueError, OSError):
                        continue
                batches = []
                for status_file in sorted(root.glob("batch-*.status.json")):
                    name = status_file.name.removesuffix(".status.json")
                    try:
                        record = json.loads(status_file.read_text(encoding="utf-8"))
                        comparison_file = root / name / "comparison.json"
                        if comparison_file.is_file():
                            record["by_condition"] = json.loads(comparison_file.read_text(
                                encoding="utf-8"))["by_condition"]
                        batches.append({"name": name, **record})
                    except (ValueError, OSError, KeyError):
                        continue
                jobs = []
                for status_file in sorted(root.glob("*.status.json")):
                    if status_file.name.startswith("batch-"):
                        continue
                    try:
                        jobs.append({"name": status_file.name.removesuffix(".status.json"),
                                     **json.loads(status_file.read_text(encoding="utf-8"))})
                    except (ValueError, OSError):
                        continue
                return self.send_data(200, {"runs": runs, "batches": batches, "jobs": jobs})
            match = re.fullmatch(r"/api/runs/([a-zA-Z0-9_/-]+)", path)
            parts = match[1].split("/") if match else []
            if parts and len(parts) <= 2 and all(RUN_NAME.fullmatch(part) for part in parts):
                run = root.joinpath(*parts)
                try:
                    summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
                    import csv
                    with (run / "daily_metrics.csv").open(encoding="utf-8", newline="") as stream:
                        days = [{key: float(value) for key, value in row.items()}
                                for row in csv.DictReader(stream)]
                    return self.send_data(200, {"summary": summary, "days": days})
                except (OSError, ValueError):
                    pass
            self.send_data(404, {"error": "run not found"})

        def do_POST(self):
            if not self.allowed_host():
                return self.send_data(403, {"error": "loopback host required"})
            origin = self.headers.get("Origin")
            if origin and origin not in {"http://127.0.0.1:" + str(self.server.server_port),
                                         "http://localhost:" + str(self.server.server_port)}:
                return self.send_data(403, {"error": "same-origin request required"})
            if self.path == "/api/batches":
                name = f"batch-{uuid.uuid4().hex[:8]}"
                command = [sys.executable, "-m", "campus_sim.cli", "batch", "--configs",
                           str(project / "configs"), "--output", str(root / name)]
                spawn(command, name)
                return self.send_data(202, {"name": name})
            if self.path != "/api/runs":
                return self.send_data(404, {"error": "not found"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > 4096:
                    raise ValueError("invalid request size")
                data = json.loads(self.rfile.read(length))
                condition, seed, days = data["condition"], data["seed"], data["days"]
                if condition not in CONDITIONS or type(seed) is not int or seed not in SEEDS:
                    raise ValueError("invalid condition or seed")
                if type(days) is not int or not 1 <= days <= 30:
                    raise ValueError("days must be between 1 and 30")
                name = f"{condition}-seed{seed}-{uuid.uuid4().hex[:8]}"
                output = root / name
                config = project / "configs" / f"{condition}.json"
                command = [sys.executable, "-m", "campus_sim.cli", "run", "--config",
                           str(config), "--seed", str(seed), "--days", str(days),
                           "--output", str(output)]
                spawn(command, name)
                self.send_data(202, {"name": name})
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
                self.send_data(400, {"error": str(error)})

    return Handler


def serve(root="runs/workbench", port=8765):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", port), create_handler(root))
    print(f"CampusSimulation: http://127.0.0.1:{port}", flush=True)
    server.serve_forever()
