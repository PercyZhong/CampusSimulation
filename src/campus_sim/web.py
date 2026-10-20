"""Loopback-only workbench for synthetic Society0 experiments."""

import csv
import json
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .domain import Config
from .event_view import read_events, read_timeline
from .models import choices, require_key, validate

CONDITIONS = ("baseline", "outreach", "easier_access", "targeted_outreach")
SEEDS = (11, 22, 33)
MAX_STUDENTS = 100
MAX_STAFF = 10
MAX_DAYS = 30
RUN_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}\Z")
DISPLAY_TIMEZONE = timezone(timedelta(hours=8))


def run_name(condition, seed, when, sequence=1):
    """Use a readable local-minute name, suffixing same-minute collisions."""
    base = f"{condition}-seed{seed}-{when.astimezone(DISPLAY_TIMEZONE):%Y%m%d%H%M}"
    return base if sequence == 1 else f"{base}-{sequence}"


def save_run_config(root, data, when):
    """Reserve a unique name atomically while saving its exact run config."""
    config_dir = root / "configs"
    config_dir.mkdir(exist_ok=True)
    for sequence in range(1, 1000):
        name = run_name(data["condition"], data["seed"], when, sequence)
        if (root / name).exists() or (root / f"{name}.status.json").exists():
            continue
        config_path = config_dir / f"{name}.json"
        try:
            with config_path.open("x", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
        except FileExistsError:
            continue
        return name, config_path
    raise ValueError("too many runs started in the same minute")


def list_run_summaries(root):
    """List completed runs newest first, including older names and batch runs."""
    summary_files = list(root.glob("*/summary.json")) + list(
        root.glob("batch-*/*/summary.json"))
    runs = []
    for summary_file in summary_files:
        try:
            summary = json.loads(summary_file.read_text(encoding="utf-8"))
            name = summary_file.parent.relative_to(root).as_posix()
            status_file = root / f"{name}.status.json"
            run_status = (json.loads(status_file.read_text(encoding="utf-8"))
                          if status_file.is_file() else {"status": "complete"})
            started_at = run_status.get("started_at", summary_file.stat().st_mtime)
            runs.append({"name": name, **summary,
                         "run_status": run_status["status"], "started_at": started_at})
        except (ValueError, OSError, KeyError):
            continue
    runs.sort(key=lambda item: (item["started_at"], item["name"]), reverse=True)
    return runs


def read_run_detail(run):
    """Read a run even when optional daily metric cells are blank."""
    summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    days = []
    try:
        with (run / "daily_metrics.csv").open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                parsed = {}
                for key, value in row.items():
                    if value in (None, ""):
                        parsed[key] = None
                    else:
                        try:
                            parsed[key] = float(value)
                        except ValueError:
                            parsed[key] = value
                days.append(parsed)
    except (OSError, csv.Error) as error:
        return {"summary": summary, "days": [], "daily_metrics_error": str(error)}
    return {"summary": summary, "days": days}


def build_run_config(project, data):
    """Build a bounded, validated experiment from an existing policy template."""
    condition = data.get("condition")
    if condition not in CONDITIONS:
        raise ValueError("invalid condition")
    fields = {
        "n_students": (2, MAX_STUDENTS), "n_staff": (1, MAX_STAFF),
        "days": (1, MAX_DAYS), "seed": (0, 999999),
        "periods_per_day": (1, 3), "capacity": (1, MAX_STAFF * 3),
        "open_every": (1, 7), "outreach_day": (1, MAX_DAYS),
    }
    values = {}
    for key, (minimum, maximum) in fields.items():
        value = data.get(key)
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f"{key} must be an integer between {minimum} and {maximum}")
        values[key] = value
    if values["periods_per_day"] not in (1, 3):
        raise ValueError("periods_per_day must be 1 or 3")
    if values["capacity"] > values["n_staff"] * 3:
        raise ValueError("capacity exceeds staff slots")
    barrier = data.get("access_barrier")
    if type(barrier) not in (int, float) or not 0 <= barrier <= 1:
        raise ValueError("access_barrier must be between 0 and 1")
    raw = json.loads((project / "configs" / f"{condition}.json").read_text(encoding="utf-8"))
    raw.update(n_students=values["n_students"], n_staff=values["n_staff"],
               end=values["days"], seed=values["seed"],
               periods_per_day=values["periods_per_day"],
               capacity=values["capacity"], open_every=values["open_every"],
               outreach_day=values["outreach_day"], access_barrier=barrier)
    components = raw.setdefault("policy_components", {})
    components.setdefault("service", {}).update(capacity=values["capacity"],
                                                  open_every=values["open_every"])
    if "information_delivery" in components:
        components["information_delivery"]["day"] = values["outreach_day"]
    provider = data.get("provider")
    if provider is not None:
        validate(provider, data.get("model"), data.get("max_model_calls"))
        require_key(provider)
        raw.update(driver="llm", model_provider=provider, model_id=data["model"],
                   max_model_calls=data["max_model_calls"],
                   model_temperature=data.get("temperature", 0.2))
    elif set(data) & {"model", "max_model_calls", "temperature"}:
        raise ValueError("model settings require provider")
    Config.parse(raw)
    return raw
PAGE = """<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CampusSimulation</title><style>body{font:16px system-ui;margin:2rem auto;max-width:1050px;padding:0 1rem;background:#f5f7fb;color:#172337}h1{margin-bottom:0}small{color:#526279}.box{background:white;padding:1.2rem;border-radius:12px;margin:1rem 0;box-shadow:0 1px 4px #ccd4e0}button,select,input{font:inherit;padding:.5rem;margin:.25rem}button{background:#1756a9;color:white;border:0;border-radius:6px;cursor:pointer}table{border-collapse:collapse;width:100%}td,th{padding:.5rem;border-bottom:1px solid #dce2eb;text-align:left;vertical-align:top}pre{white-space:pre-wrap;overflow:auto}canvas{max-width:100%}.timeline-actions{line-height:1.5;min-width:220px}.timeline-list{font-size:.85rem;margin:.1rem 0;padding-left:1.3rem;max-height:220px;overflow:auto}.timeline-list li{margin-bottom:.5rem}.timeline-list pre{font-size:.8rem;max-width:390px;margin:.2rem 0;background:#f5f7fb;padding:.3rem;border-radius:4px}</style>
<h1>CampusSimulation</h1><small>未校准的合成校园情景；不能用于真实学生诊断、个人风险判断或政策效果预测。</small>
<div class="box"><h2>构建校园实验</h2><label>政策条件 <select id="condition"><option>baseline</option><option>outreach</option><option>easier_access</option><option>targeted_outreach</option></select></label><label>随机种子 <input id="seed" type="number" min="0" max="999999" value="11"></label><label>学生人数 <input id="nStudents" type="number" min="2" max="100" value="40"></label><label>辅导员人数 <input id="nStaff" type="number" min="1" max="10" value="2"></label><label>运行天数 <input id="days" type="number" min="1" max="30" value="10"></label><label>每日决策时段 <select id="periods"><option value="1">一次</option><option value="3">上午、下午、晚上</option></select></label><label>每日服务名额 <input id="capacity" type="number" min="1" max="30" value="3"></label><label>服务开放间隔（天） <input id="openEvery" type="number" min="1" max="7" value="1"></label><label>基线求助门槛 <input id="barrier" type="number" min="0" max="1" step="0.01" value="0.22"></label><label>外展投递日 <input id="outreachDay" type="number" min="1" max="30" value="5"></label><p><small>上限：学生 100 人、辅导员 10 人、30 天。每日服务名额不能超过辅导员人数的 3 倍。外展投递日仅影响外展条件。</small></p><label>决策方式 <select id="driver" onchange="renderModels()"><option value="rule">离线规则</option><option value="llm">真实 LLM API</option></select></label><label>提供商 <select id="provider" onchange="renderModels()"></select></label><label>模型 <input id="model" list="modelSuggestions" placeholder="模型 ID"></label><datalist id="modelSuggestions"></datalist><label>温度 <input id="temperature" type="number" min="0" max="2" step="0.1" value="0.2"></label><label>每次运行最多调用 <input id="maxCalls" type="number" min="1" value="1200"></label><p><small>模型调用预算至少为学生人数 × 天数 × 每日时段数；同伴对话还会增加调用。API 密钥只从启动服务的环境变量读取。</small></p><button onclick="launch()">启动新运行</button><button onclick="launchBatch()">启动固定 4×3 对照</button><p><small>固定对照包含四种政策 × 三个随机种子（共 12 次运行），使用预设人数和天数，不使用上方自定义参数。</small></p><p id="message"></p></div>
<div class="box"><h2>已有运行</h2><button onclick="refresh()">刷新</button><button onclick="clearDisplayed()">清空显示</button><button onclick="restoreDisplayed()">恢复显示</button><small id="hiddenCount"></small><div id="jobs"></div><table><thead><tr><th>名称</th><th>模型</th><th>条件</th><th>学生</th><th>辅导员</th><th>天数</th><th>求助率</th><th>支持率</th><th>等待天数</th><th>操作</th></tr></thead><tbody id="runs"></tbody></table><small>“删除记录”和“清空显示”只隐藏浏览器中的记录，不删除实验文件；“恢复显示”可找回。</small></div>
<div class="box"><h2>独立批处理结果</h2><p><small>这里列出各批次政策 × 种子的整体汇总，不随下方“选中运行”切换；旧批次保留原来的实验组合。</small></p><div id="batches">暂无结果。</div></div>
<div class="box"><h2>选中运行</h2><div id="detail">请选择一个运行。</div><canvas id="chart" width="900" height="260"></canvas></div>
<div class="box"><h2>主体行动轨迹</h2><p>每天按辅导员、学生的编号升序排列；每位主体每天占一行，行动按发生顺序连接。辅导员轨迹包含值班和其承接的服务。每页 50 行。</p><label>主体 <select id="eventActor" onchange="loadEvents(0)"><option value="">全部主体</option></select></label><label>模拟日 <input id="eventDay" type="number" min="1" max="30" placeholder="全部"></label><label>行动 <select id="eventAction"><option value="">全部</option><option>attend</option><option>course_absence</option><option>rest</option><option>peer</option><option>request</option><option>accept_support</option><option>staff_shift</option><option>model_decision</option><option>outreach_information</option><option>staff_follow_up</option></select></label><button onclick="loadEvents(0)">筛选</button><p id="eventStatus">请选择一个运行。</p><table><thead><tr><th>日</th><th>主体</th><th>行动顺序</th><th>状态与详情</th></tr></thead><tbody id="events"></tbody></table><button onclick="previousEvents()">上一页</button><button onclick="nextEvents()">下一页</button></div>
<script>
const j=async u=>{const r=await fetch(u);if(!r.ok)throw Error(await r.text());return r.json()};
const pct=x=>x==null?'—':(100*x).toFixed(1)+'%';
let selectedRun=null,eventOffset=0,eventTotal=0,latestData={runs:[],jobs:[],batches:[]};
let hiddenNames;
try{hiddenNames=new Set(JSON.parse(localStorage.getItem('campus-sim-hidden-runs')||'[]'))}catch{hiddenNames=new Set()}
let providerInfo={};
async function initModels(){providerInfo=await j('/api/models');let p=document.getElementById('provider');for(let [name,info] of Object.entries(providerInfo)){let o=document.createElement('option');o.value=name;o.textContent=name+(info.configured?' · 密钥已配置':' · 缺少密钥');p.append(o)}renderModels()}
function renderModels(){let active=document.getElementById('driver').value==='llm';for(let id of ['provider','model','temperature','maxCalls'])document.getElementById(id).disabled=!active;let info=providerInfo[document.getElementById('provider').value];let list=document.getElementById('modelSuggestions');list.replaceChildren();if(info){for(let name of info.models){let o=document.createElement('option');o.value=name;list.append(o)}if(!list.dataset.provider||list.dataset.provider!==document.getElementById('provider').value)document.getElementById('model').value=info.models[0];list.dataset.provider=document.getElementById('provider').value}}
function modelOptions(){if(document.getElementById('driver').value!=='llm')return {};return {provider:document.getElementById('provider').value,model:document.getElementById('model').value,temperature:Number(document.getElementById('temperature').value),max_model_calls:Number(document.getElementById('maxCalls').value)}}
function renderRuns(){
  let data=latestData,body=document.getElementById('runs');body.replaceChildren();
  for(let x of data.runs.filter(x=>!hiddenNames.has(x.name))){
    let tr=document.createElement('tr'),model=x.driver==='llm'?x.model_provider+' / '+x.model_id:'离线规则';
    let values=[x.name,model,x.condition,x.n_students,x.n_staff,x.complete_steps,pct(x.requested_rate),pct(x.supported_rate),x.completed_wait_mean_days==null?'—':x.completed_wait_mean_days.toFixed(2)];
    for(let v of values){let td=document.createElement('td');td.textContent=v;tr.append(td)}
    let action=document.createElement('td'),remove=document.createElement('button');
    remove.textContent='删除记录';remove.title='只从当前浏览器隐藏，不删除实验文件';
    remove.onclick=e=>{e.stopPropagation();hideRun(x.name)};action.append(remove);tr.append(action);
    tr.style.cursor='pointer';tr.onclick=()=>show(x.name);body.append(tr)
  }
  let listedRuns=new Set(data.runs.map(x=>x.name));
  document.getElementById('jobs').textContent=data.jobs.filter(x=>
    !hiddenNames.has(x.name)&&!(x.status==='complete'&&listedRuns.has(x.name))
  ).map(x=>x.name+'：'+x.status+(x.error?'（查看服务器状态文件）':'')).join('；');
  let b=document.getElementById('batches');b.replaceChildren();
  for(let run of data.batches.filter(x=>!hiddenNames.has(x.name))){
    let p=document.createElement('p'),conditions=run.by_condition?Object.keys(run.by_condition):[];
    let seeds=conditions.length?run.by_condition[conditions[0]]?.requested_rate?.n_seeds:null;
    p.textContent=run.name+'：'+run.status+(seeds?'（'+conditions.length+' 政策 × '+seeds+' 种子）':'');b.append(p);
    if(run.by_condition){
      let table=document.createElement('table'),h=document.createElement('tr');
      for(let label of ['条件','平均求助率','平均支持率','平均等待天数','平均持续低联结率']){let th=document.createElement('th');th.textContent=label;h.append(th)}
      table.append(h);
      for(let [condition,m] of Object.entries(run.by_condition)){
        let tr=document.createElement('tr');
        for(let value of [condition,pct(m.requested_rate.mean),pct(m.supported_rate.mean),m.completed_wait_mean_days.mean?.toFixed(2)??'—',pct(m.low_connection_7d_rate.mean)]){let td=document.createElement('td');td.textContent=String(value);tr.append(td)}
        table.append(tr)
      }
      b.append(table)
    }
  }
  document.getElementById('hiddenCount').textContent=hiddenNames.size?'已隐藏 '+hiddenNames.size+' 条记录':''
}
async function refresh(){try{latestData=await j('/api/runs');renderRuns()}catch(e){document.getElementById('message').textContent=e.message}}
function resetSelected(){
  selectedRun=null;eventOffset=0;eventTotal=0;
  document.getElementById('detail').textContent='请选择一个运行。';
  document.getElementById('events').replaceChildren();document.getElementById('eventStatus').textContent='请选择一个运行。';
  let canvas=document.getElementById('chart');canvas.getContext('2d').clearRect(0,0,canvas.width,canvas.height)
}
function hideRun(name){
  hiddenNames.add(name);localStorage.setItem('campus-sim-hidden-runs',JSON.stringify([...hiddenNames]));
  if(selectedRun===name)resetSelected();renderRuns()
}
function clearDisplayed(){
  for(let group of [latestData.runs,latestData.jobs,latestData.batches])for(let item of group)hiddenNames.add(item.name);
  localStorage.setItem('campus-sim-hidden-runs',JSON.stringify([...hiddenNames]));
  resetSelected();renderRuns()
}
function restoreDisplayed(){hiddenNames.clear();localStorage.removeItem('campus-sim-hidden-runs');renderRuns()}
function actorOptions(summary){let select=document.getElementById('eventActor');select.replaceChildren();let all=document.createElement('option');all.value='';all.textContent='全部主体';select.append(all);for(let [prefix,count,label] of [['c',summary.n_staff,'辅导员'],['s',summary.n_students,'学生']]){let group=document.createElement('optgroup');group.label=label;for(let i=0;i<count;i++){let id=prefix+String(i).padStart(3,'0'),option=document.createElement('option');option.value=id;option.textContent=label+' '+id;group.append(option)}select.append(group)}}
async function show(name){
  let d=document.getElementById('detail');
  try{
    let x=await j('/api/runs/'+name);selectedRun=name;eventOffset=0;
    actorOptions(x.summary);document.getElementById('eventDay').value='';document.getElementById('eventAction').value='';loadEvents(0);
    d.textContent=`${name} · ${x.summary.condition} · seed ${x.summary.seed} · ${x.summary.n_students} 名学生 · ${x.summary.n_staff} 名辅导员 · ${x.summary.complete_steps} 天 · 求助 ${pct(x.summary.requested_rate)} · 获得支持 ${pct(x.summary.supported_rate)} · 持续低联结 ${pct(x.summary.low_connection_7d_rate)} · 压力均值 ${x.summary.stress_distribution.mean.toFixed(3)}`;
    if(x.daily_metrics_error)d.textContent+=' · 每日指标读取失败：'+x.daily_metrics_error;
    let c=document.getElementById('chart'),ctx=c.getContext('2d');ctx.clearRect(0,0,c.width,c.height);
    let days=x.days.filter(p=>Number.isFinite(p.mean_stress));if(!days.length)return;
    ctx.strokeStyle='#1756a9';ctx.lineWidth=3;ctx.beginPath();
    days.forEach((p,i)=>{let X=35+i*(c.width-55)/Math.max(1,days.length-1),Y=230-p.mean_stress*195;i?ctx.lineTo(X,Y):ctx.moveTo(X,Y)});
    ctx.stroke();ctx.fillStyle='#172337';ctx.fillText('逐日平均压力（模型变量）',35,22);ctx.fillText('0',10,230);ctx.fillText('1',10,35)
  }catch(e){selectedRun=null;d.textContent='无法打开运行 '+name+'：'+e.message}
}
async function loadEvents(offset){
  if(!selectedRun)return;
  try{
    let params=new URLSearchParams({offset:String(offset),limit:'50'});
    let day=document.getElementById('eventDay').value,action=document.getElementById('eventAction').value,actor=document.getElementById('eventActor').value;
    if(day)params.set('day',day);if(action)params.set('action',action);if(actor)params.set('actor',actor);
    let data=await j('/api/runs/'+selectedRun+'/timeline?'+params);
    eventOffset=data.offset;eventTotal=data.total;
    let body=document.getElementById('events');body.replaceChildren();
    for(let row of data.rows){
      let tr=document.createElement('tr'),dayCell=document.createElement('td'),actorCell=document.createElement('td');
      dayCell.textContent=row.day;actorCell.textContent=(row.actor.startsWith('c')?'辅导员 ':'学生 ')+row.actor;
      let pathCell=document.createElement('td');pathCell.className='timeline-actions';
      pathCell.textContent=row.events.map(e=>(e.detail?.period?e.detail.period+':':'')+e.action).join(' → ');
      let detailCell=document.createElement('td'),list=document.createElement('ol');list.className='timeline-list';
      for(let event of row.events){
        let item=document.createElement('li'),header=document.createElement('span');
        let related=event.actor!==row.actor?' · 涉及 '+event.actor:'';
        header.textContent=(event.detail?.period?event.detail.period+' · ':'')+event.action+' · '+event.status+(event.reason?' · '+event.reason:'')+related;
        item.append(header);
        if(event.detail&&Object.keys(event.detail).length){
          let raw=document.createElement('pre');raw.textContent=JSON.stringify(event.detail,null,2);
          item.append(raw)
        }
        list.append(item)
      }
      detailCell.append(list);tr.append(dayCell,actorCell,pathCell,detailCell);body.append(tr)
    }
    document.getElementById('eventStatus').textContent=`${data.total} 条每日轨迹；显示 ${data.total?data.offset+1:0}–${Math.min(data.total,data.offset+data.rows.length)}`
  }catch(e){document.getElementById('eventStatus').textContent=e.message}
}
function previousEvents(){loadEvents(Math.max(0,eventOffset-50))}
function nextEvents(){if(eventOffset+50<eventTotal)loadEvents(eventOffset+50)}
async function launch(){let m=document.getElementById('message');try{let number=id=>Number(document.getElementById(id).value);let data={condition:document.getElementById('condition').value,seed:number('seed'),n_students:number('nStudents'),n_staff:number('nStaff'),days:number('days'),periods_per_day:number('periods'),capacity:number('capacity'),open_every:number('openEvery'),access_barrier:number('barrier'),outreach_day:number('outreachDay'),...modelOptions()};let r=await fetch('/api/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});let x=await r.json();if(!r.ok)throw Error(x.error);m.textContent='已启动 '+x.name+'；运行完成后会出现在下方列表。';refresh()}catch(e){m.textContent=e.message}}refresh();initModels();setInterval(refresh,5000);
async function launchBatch(){let m=document.getElementById('message');try{let r=await fetch('/api/batches',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(modelOptions())});let x=await r.json();if(!r.ok)throw Error(x.error);m.textContent='已启动 '+x.name+'；请稍后刷新。'}catch(e){m.textContent=e.message}}
</script></html>"""


def create_handler(root):
    root = Path(root).resolve()
    project = Path(__file__).resolve().parents[2]

    def spawn(command, name, started_at=None):
        started_at = started_at if started_at is not None else time.time()
        def work():
            result = subprocess.run(command, cwd=project, capture_output=True, text=True,
                                    check=False)
            status = "complete" if result.returncode == 0 else "failed"
            (root / f"{name}.status.json").write_text(json.dumps({
                "status": status, "returncode": result.returncode,
                "started_at": started_at,
                "error": result.stderr[-3000:] if result.returncode else None,
            }), encoding="utf-8")
        (root / f"{name}.status.json").write_text(json.dumps({
            "status": "running", "started_at": started_at,
        }), encoding="utf-8")
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
            parsed = urlsplit(self.path)
            path = parsed.path
            if path == "/":
                return self.send_data(200, PAGE, "text/html; charset=utf-8")
            if path == "/api/models":
                return self.send_data(200, choices())
            if path == "/api/runs":
                runs = list_run_summaries(root)
                batches = []
                for status_file in sorted(root.glob("batch-*.status.json"),
                                          key=lambda item: item.stat().st_mtime,
                                          reverse=True):
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
                for status_file in sorted(root.glob("*.status.json"),
                                          key=lambda item: item.stat().st_mtime,
                                          reverse=True):
                    if status_file.name.startswith("batch-"):
                        continue
                    try:
                        jobs.append({"name": status_file.name.removesuffix(".status.json"),
                                     **json.loads(status_file.read_text(encoding="utf-8"))})
                    except (ValueError, OSError):
                        continue
                return self.send_data(200, {"runs": runs, "batches": batches, "jobs": jobs})
            event_match = re.fullmatch(r"/api/runs/([a-zA-Z0-9_/-]+)/(events|timeline)", path)
            if event_match:
                parts = event_match[1].split("/")
                if len(parts) not in (1, 2) or not all(
                    RUN_NAME.fullmatch(part) for part in parts
                ):
                    return self.send_data(404, {"error": "run not found"})
                try:
                    query = parse_qs(parsed.query, strict_parsing=True)
                    if set(query) - {"day", "action", "actor", "offset", "limit"}:
                        raise ValueError("unknown event filter")
                    day = int(query["day"][0]) if "day" in query else None
                    action = query["action"][0] if "action" in query else None
                    actor = query["actor"][0] if "actor" in query else None
                    offset = int(query.get("offset", ["0"])[0])
                    limit = int(query.get("limit", ["50"])[0])
                    reader = read_timeline if event_match[2] == "timeline" else read_events
                    return self.send_data(200, reader(root.joinpath(*parts),
                        day=day, action=action, actor=actor, offset=offset, limit=limit))
                except (ValueError, KeyError) as error:
                    return self.send_data(400, {"error": str(error)})
                except OSError:
                    return self.send_data(404, {"error": "run not found"})
            match = re.fullmatch(r"/api/runs/([a-zA-Z0-9_/-]+)", path)
            parts = match[1].split("/") if match else []
            if parts and len(parts) <= 2 and all(RUN_NAME.fullmatch(part) for part in parts):
                run = root.joinpath(*parts)
                try:
                    return self.send_data(200, read_run_detail(run))
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
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0 or length > 4096 or (length == 0 and self.path != "/api/batches"):
                    raise ValueError("invalid request size")
                data = json.loads(self.rfile.read(length)) if length else {}
                provider = data.get("provider")
                options = []
                if provider is not None:
                    model, max_calls = data.get("model"), data.get("max_model_calls")
                    validate(provider, model, max_calls)
                    require_key(provider)
                    temperature = data.get("temperature", 0.2)
                    if type(temperature) not in (int, float) or not 0 <= temperature <= 2:
                        raise ValueError("temperature must be in [0,2]")
                    options = ["--provider", provider, "--model", model,
                               "--max-model-calls", str(max_calls),
                               "--temperature", str(temperature)]
                elif set(data) & {"model", "max_model_calls", "temperature"}:
                    raise ValueError("model settings require provider")
            except (ValueError, TypeError, json.JSONDecodeError) as error:
                return self.send_data(400, {"error": str(error)})
            if self.path == "/api/batches":
                name = f"batch-{uuid.uuid4().hex[:8]}"
                command = [sys.executable, "-m", "campus_sim.cli", "batch", "--configs",
                           str(project / "configs"), "--output", str(root / name), *options]
                spawn(command, name)
                return self.send_data(202, {"name": name})
            if self.path != "/api/runs":
                return self.send_data(404, {"error": "not found"})
            try:
                config_data = build_run_config(project, data)
                started = datetime.now(DISPLAY_TIMEZONE)
                name, config = save_run_config(root, config_data, started)
                output = root / name
                command = [sys.executable, "-m", "campus_sim.cli", "run", "--config",
                           str(config), "--output", str(output)]
                spawn(command, name, started.timestamp())
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
