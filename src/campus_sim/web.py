"""Loopback-only workbench for synthetic Society0 experiments."""

import csv
import hashlib
import json
import os
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

from .analysis_agent import batch_evidence, generate_report, run_evidence, save_report
from .domain import Config
from .event_view import read_events, read_timeline
from .models import PROVIDERS, choices, require_key, validate
from .naming import run_stem

CONDITIONS = ("baseline", "outreach", "easier_access", "targeted_outreach")
SEEDS = (11, 22, 33)
MAX_STUDENTS = 100
MAX_STAFF = 10
MAX_DAYS = 30
MAX_BATCH_SEEDS = 10
RUN_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,190}\Z")
DISPLAY_TIMEZONE = timezone(timedelta(hours=8))


def run_name(condition, seed, when, sequence=1, *, config=None):
    """Use a readable local-minute name, suffixing same-minute collisions."""
    stem = run_stem(condition, seed, config or {})
    base = f"{stem}-{when.astimezone(DISPLAY_TIMEZONE):%Y%m%d%H%M}"
    return base if sequence == 1 else f"{base}-{sequence}"


def save_run_config(root, data, when):
    """Reserve a unique name atomically while saving its exact run config."""
    config_dir = root / "configs"
    config_dir.mkdir(exist_ok=True)
    for sequence in range(1, 1000):
        name = run_name(data["condition"], data["seed"], when, sequence, config=data)
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


def build_run_config(project, data, api_key=None):
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
        if not api_key:
            require_key(provider)
        raw.update(driver="llm", model_provider=provider, model_id=data["model"],
                   max_model_calls=data["max_model_calls"],
                   model_temperature=data.get("temperature", 0.2))
    elif set(data) & {"model", "max_model_calls", "temperature"}:
        raise ValueError("model settings require provider")
    Config.parse(raw)
    return raw


def build_batch_plan(project, data, api_key=None):
    """Validate and expand a shared scenario into policy-by-seed runs."""
    conditions = data.get("conditions")
    seeds = data.get("seeds")
    if (not isinstance(conditions, list) or not conditions
            or len(conditions) > len(CONDITIONS)
            or any(not isinstance(condition, str) or condition not in CONDITIONS
                   for condition in conditions)
            or len(set(conditions)) != len(conditions)):
        raise ValueError("select one or more distinct policies")
    if (not isinstance(seeds, list) or not 1 <= len(seeds) <= MAX_BATCH_SEEDS
            or any(type(seed) is not int or not 0 <= seed <= 999999 for seed in seeds)
            or len(set(seeds)) != len(seeds)):
        raise ValueError("provide 1 to 10 distinct integer seeds between 0 and 999999")
    runs = []
    for condition in conditions:
        for seed in seeds:
            config = build_run_config(project, {**data, "condition": condition, "seed": seed},
                                      api_key=api_key)
            runs.append({"name": run_stem(condition, seed, config), "config": config})
    return {"conditions": conditions, "seeds": seeds, "runs": runs}


def read_job_progress(root, name, status, *, batch=False):
    """Report completed simulation days from Society0's atomic runner status."""
    root = Path(root)
    try:
        if batch:
            plan = json.loads((root / "configs" / f"{name}.json").read_text(encoding="utf-8"))
            targets = [(root / name / item["name"], item["config"]["end"])
                       for item in plan["runs"]]
        else:
            config = json.loads((root / "configs" / f"{name}.json").read_text(encoding="utf-8"))
            targets = [(root / name, config["end"])]
    except (OSError, ValueError, KeyError, TypeError):
        targets = []

    completed = 0
    total = 0
    finished_runs = 0
    for path, days in targets:
        if type(days) is not int or days < 1:
            continue
        total += days
        try:
            progress = json.loads((path / "runner-status.json").read_text(encoding="utf-8"))
            step = progress["complete_step"]
        except (OSError, ValueError, KeyError, TypeError):
            step = 0
        if type(step) is not int or step < 0:
            step = 0
        done = min(days, step)
        completed += done
        finished_runs += done == days

    if status == "complete":
        completed = total
        finished_runs = len(targets)
    percent = min(99 if status == "running" else 100,
                  completed * 100 // total) if total else (100 if status == "complete" else 0)
    return {"percent": percent, "completed": completed, "total": total,
            "finished_runs": finished_runs, "run_count": len(targets)}


def valid_record_name(name):
    parts = name.split("/") if isinstance(name, str) else []
    return 1 <= len(parts) <= 2 and all(RUN_NAME.fullmatch(part) for part in parts)


def read_hidden_names(root):
    path = Path(root) / ".hidden-runs.json"
    try:
        names = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set()
    return {name for name in names if valid_record_name(name)}


def hide_record_names(root, names):
    if not isinstance(names, list):
        raise TypeError("record names must be a list")
    if not 1 <= len(names) <= 1000 or any(not valid_record_name(name) for name in names):
        raise ValueError("invalid record names")
    root = Path(root)
    hidden = read_hidden_names(root) | set(names)
    path = root / ".hidden-runs.json"
    temporary = root / f".hidden-runs-{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(json.dumps(sorted(hidden), ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return hidden


PAGE = """<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CampusSimulation</title><style>body{font:16px system-ui;margin:2rem auto;max-width:1050px;padding:0 1rem;background:#f5f7fb;color:#172337}h1{margin-bottom:0}small{color:#526279}.box{background:white;padding:1.2rem;border-radius:12px;margin:1rem 0;box-shadow:0 1px 4px #ccd4e0}button,select,input{font:inherit;padding:.5rem;margin:.25rem}button{background:#1756a9;color:white;border:0;border-radius:6px;cursor:pointer}table{border-collapse:collapse;width:100%}td,th{padding:.5rem;border-bottom:1px solid #dce2eb;text-align:left;vertical-align:top}pre{white-space:pre-wrap;overflow:auto}canvas{max-width:100%}.timeline-actions{line-height:1.5;min-width:220px}.timeline-list{font-size:.85rem;margin:.1rem 0;padding-left:1.3rem;max-height:220px;overflow:auto}.timeline-list li{margin-bottom:.5rem}.timeline-list pre{font-size:.8rem;max-width:390px;margin:.2rem 0;background:#f5f7fb;padding:.3rem;border-radius:4px}</style>
<style>.job-progress{margin:.6rem 0 1rem}.job-progress progress{display:block;width:min(100%,560px);height:1rem;accent-color:#1756a9}.job-progress small{display:block;margin-top:.2rem}.policy-guide{margin:.4rem 0 1rem;padding:.7rem 1rem;background:#f5f7fb;border-radius:8px}.policy-guide ul{margin:.4rem 0 0;padding-left:1.5rem}.policy-guide li{margin:.2rem 0}.timeline-period{margin:.15rem 0}.timeline-period strong{display:inline-block;min-width:3.5em}.action-guide{margin:.6rem 0;color:#526279}.action-guide ul{columns:2;margin:.4rem 0;padding-left:1.5rem}</style>
<h1>CampusSimulation</h1><small>未校准的合成校园情景；不能用于真实学生诊断、个人风险判断或政策效果预测。</small>
<div class="box"><h2>构建校园实验</h2><label>政策条件 <select id="condition"><option value="baseline">baseline（基线）</option><option value="outreach">outreach（全体外展）</option><option value="easier_access">easier_access（降低求助门槛）</option><option value="targeted_outreach">targeted_outreach（定向外展）</option></select></label><div class="policy-guide"><strong>政策说明</strong><ul><li><code>baseline</code>：保持基线求助门槛与服务规则，不主动推送支持服务信息。</li><li><code>outreach</code>：在设置的外展投递日向全体学生发送支持服务信息。</li><li><code>easier_access</code>：降低求助门槛，让学生更容易提出求助。</li><li><code>targeted_outreach</code>：在设置的外展投递日向初始低联结组发送支持服务信息。</li></ul></div><label>随机种子 <input id="seed" type="number" min="0" max="999999" value="11"></label><label>学生人数 <input id="nStudents" type="number" min="2" max="100" value="40"></label><label>辅导员人数 <input id="nStaff" type="number" min="1" max="10" value="2"></label><label>运行天数 <input id="days" type="number" min="1" max="30" value="10"></label><label>每日决策时段 <select id="periods"><option value="1">一次</option><option value="3">上午、下午、晚上</option></select></label><label>每日服务名额 <input id="capacity" type="number" min="1" max="30" value="3"></label><label>服务开放间隔（天） <input id="openEvery" type="number" min="1" max="7" value="1"></label><label>基线求助门槛 <input id="barrier" type="number" min="0" max="1" step="0.01" value="0.22"></label><label>外展投递日 <input id="outreachDay" type="number" min="1" max="30" value="5"></label><p><small>上限：学生 100 人、辅导员 10 人、30 天。每日服务名额不能超过辅导员人数的 3 倍。外展投递日仅影响外展条件。</small></p><label>决策方式 <select id="driver" onchange="renderModels()"><option value="rule">离线规则</option><option value="llm">真实 LLM API</option></select></label><label>提供商 <select id="provider" onchange="renderModels()"></select></label><label>模型 <input id="model" list="modelSuggestions" placeholder="模型 ID"></label><datalist id="modelSuggestions"></datalist><label>温度 <input id="temperature" type="number" min="0" max="2" step="0.1" value="0.2"></label><label>每次运行最多调用 <input id="maxCalls" type="number" min="1" value="1200"></label><label>在线 API 密钥 <input id="apiKey" type="password" autocomplete="off" spellcheck="false" placeholder="输入后以 •••• 隐藏"></label><p><small>可在线输入本次运行的密钥，也可使用启动服务时设置的环境变量。在线密钥仅传给本次运行进程，提交后清空输入框。</small></p><button onclick="launch()">启动新运行</button><p id="message"></p></div>
<div class="box"><h2>自定义对照实验</h2><p>选择政策并输入随机种子；对照中的每次运行共用上方的学生、辅导员、天数、服务和决策参数。</p><div id="batchPolicies"><label><input type="checkbox" value="baseline" checked>baseline（基线）</label><label><input type="checkbox" value="outreach" checked>outreach（全体外展）</label><label><input type="checkbox" value="easier_access" checked>easier_access（降低求助门槛）</label><label><input type="checkbox" value="targeted_outreach" checked>targeted_outreach（定向外展）</label></div><label>随机种子列表 <input id="batchSeeds" type="text" value="11,22,33" placeholder="例如 11,22,33"></label><p><small>可选 1–4 种政策、1–10 个不重复种子，最多 40 次运行。单次运行的政策和种子输入框不影响此对照。</small></p><button onclick="launchBatch()">启动自定义对照</button><p id="batchMessage"></p></div>
<div class="box"><h2>已有运行</h2><button onclick="refresh()">刷新</button><button onclick="clearDisplayed()">清空显示</button><small id="hiddenCount"></small><div id="jobs"></div><table><thead><tr><th>名称</th><th>模型</th><th>条件</th><th>学生</th><th>辅导员</th><th>天数</th><th>求助率</th><th>支持率</th><th>等待天数</th><th>操作</th></tr></thead><tbody id="runs"></tbody></table><small>“删除记录”和“清空显示”会永久移除网页显示，无法在页面恢复；实验文件和日志仍保留在本地。</small></div>
<div class="box"><h2>实验分析 Agent 设置</h2><p><small>选择用于解释结果的模型。分析模型独立于实验决策模型；密钥可在此临时输入，也可使用服务启动时的环境变量。</small></p><label>分析提供商 <select id="analysisProvider" onchange="renderAnalysisModels()"></select></label><label>分析模型 <input id="analysisModel" list="analysisSuggestions" placeholder="模型 ID"></label><datalist id="analysisSuggestions"></datalist><label>分析 API 密钥 <input id="analysisKey" type="password" autocomplete="off" spellcheck="false" placeholder="输入后以 •••• 隐藏"></label><p><small>单次分析会调用模型 API 并可能产生费用。密钥只用于本次分析，不写入报告。获取密钥和计费步骤见 README 的“实验分析 Agent”部分。</small></p></div>
<div class="box"><h2>独立批处理结果</h2><p><small>这里列出各批次政策 × 种子的整体汇总，不随下方“选中运行”切换；旧批次保留原来的实验组合。</small></p><div id="batches">暂无结果。</div><div id="batchAnalysisReport"></div></div>
<div class="box"><h2>选中运行</h2><div id="detail">请选择一个运行。</div><div id="runAnalysisReport"></div><div style="overflow-x:auto"><canvas id="chart" width="900" height="320" style="max-width:none"></canvas></div></div>
<div class="box"><h2>主体行动轨迹</h2><p>每天按辅导员、学生的编号升序排列；每位主体每天占一行，行动按发生顺序连接。辅导员轨迹包含值班和其承接的服务。每页 50 行。</p><label>主体 <select id="eventActor" onchange="loadEvents(0)"><option value="">全部主体</option></select></label><label>模拟日 <input id="eventDay" type="number" min="1" max="30" placeholder="全部"></label><label>行动 <select id="eventAction"><option value="">全部</option><option>attend</option><option>course_absence</option><option>rest</option><option>peer</option><option>request</option><option>accept_support</option><option>staff_shift</option><option>model_decision</option><option>outreach_information</option><option>staff_follow_up</option></select></label><button onclick="loadEvents(0)">筛选</button><p id="eventStatus">请选择一个运行。</p><table><thead><tr><th>日</th><th>主体</th><th>行动顺序</th><th>状态与详情</th></tr></thead><tbody id="events"></tbody></table><button onclick="previousEvents()">上一页</button><button onclick="nextEvents()">下一页</button></div>
<style>.metric-guide{margin:.8rem 0 1rem;padding:.8rem 1rem;background:#f5f7fb;border-radius:8px}.metric-guide ul{margin:.4rem 0 0;padding-left:1.4rem}.metric-guide li{margin:.3rem 0;line-height:1.5}.analysis-item{padding:.8rem;margin:.6rem 0;border-left:3px solid #1756a9;background:#f5f7fb}.analysis-item p{margin:.4rem 0}.analysis-item small{display:inline-block;overflow-wrap:anywhere}</style>
<script>
const j=async u=>{const r=await fetch(u);if(!r.ok)throw Error(await r.text());return r.json()};
const pct=x=>x==null?'—':(100*x).toFixed(1)+'%';
const ACTION_LABELS={attend:'上课',course_absence:'缺课',rest:'休息',peer:'同伴互动',request:'申请支持',accept_support:'接受支持',staff_shift:'辅导员值班',model_decision:'模型决策',outreach_information:'收到支持服务信息',staff_follow_up:'辅导员跟进',peer_invitation:'同伴邀请',peer_response:'同伴回应',peer_callback:'同伴回访',study_group:'学习小组',intervention_exposure:'受到政策影响',scenario_trigger:'情境触发'};
const PERIOD_LABELS={day_start:'日初',morning:'上午',afternoon:'下午',evening:'晚上',day:'当天'};
const actionLabel=code=>ACTION_LABELS[code]||code;
function groupTimelineActions(events){
  const periods=['morning','afternoon','evening'];
  const hasPeriods=events.some(event=>periods.includes(event.detail?.period));
  const groups=new Map();let current=hasPeriods?'day_start':'day';
  for(const event of events){if(periods.includes(event.detail?.period))current=event.detail.period;
    if(!groups.has(current))groups.set(current,[]);groups.get(current).push(event)}
  return (hasPeriods?['day_start',...periods]:['day']).filter(period=>period!=='day_start'||groups.has(period)).map(period=>({label:PERIOD_LABELS[period],events:groups.get(period)||[]}))
}
function initActionFilter(){
  const select=document.getElementById('eventAction');select.replaceChildren(new Option('全部',''));
  const guide=document.createElement('details');guide.className='action-guide';
  const title=document.createElement('summary');title.textContent='行动名称说明';guide.append(title);
  const list=document.createElement('ul');
  for(const [code,label] of Object.entries(ACTION_LABELS)){
    select.add(new Option(label+'（'+code+'）',code));
    const item=document.createElement('li');item.textContent=code+'：'+label;list.append(item)
  }
  guide.append(list);select.closest('label').insertAdjacentElement('afterend',guide)
}
initActionFilter();
let selectedRun=null,eventOffset=0,eventTotal=0,latestData={runs:[],jobs:[],batches:[]};
let hiddenNames;
try{hiddenNames=new Set(JSON.parse(localStorage.getItem('campus-sim-hidden-runs')||'[]'))}catch{hiddenNames=new Set()}
let hiddenSynced=false;
async function persistHidden(names){let result;for(let start=0;start<names.length;start+=100){let response=await fetch('/api/hidden-runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({names:names.slice(start,start+100)})});result=await response.json();if(!response.ok)throw Error(result.error)}return result}
let providerInfo={};
async function initModels(){providerInfo=await j('/api/models');for(let id of ['provider','analysisProvider']){let p=document.getElementById(id);for(let [name,info] of Object.entries(providerInfo)){let o=document.createElement('option');o.value=name;o.textContent=name+(info.configured?' · 环境密钥已配置':' · 可在线输入密钥');p.append(o)}}document.getElementById('analysisProvider').value='qwen';renderModels();renderAnalysisModels()}
function renderAnalysisModels(){let provider=document.getElementById('analysisProvider').value,info=providerInfo[provider],list=document.getElementById('analysisSuggestions');list.replaceChildren();if(info){for(let name of info.models)list.append(new Option('',name));document.getElementById('analysisModel').value=provider==='qwen'?'qwen3.5-plus-2026-02-15':info.models[0]}document.getElementById('analysisKey').value=''}
function renderModels(){let active=document.getElementById('driver').value==='llm';for(let id of ['provider','model','temperature','maxCalls','apiKey'])document.getElementById(id).disabled=!active;let info=providerInfo[document.getElementById('provider').value];let list=document.getElementById('modelSuggestions');if(!active||(list.dataset.provider&&list.dataset.provider!==document.getElementById('provider').value))document.getElementById('apiKey').value='';list.replaceChildren();if(info){for(let name of info.models){let o=document.createElement('option');o.value=name;list.append(o)}if(!list.dataset.provider||list.dataset.provider!==document.getElementById('provider').value)document.getElementById('model').value=info.models[0];list.dataset.provider=document.getElementById('provider').value}}
function modelOptions(){if(document.getElementById('driver').value!=='llm')return {};let key=document.getElementById('apiKey').value.trim();return {provider:document.getElementById('provider').value,model:document.getElementById('model').value,temperature:Number(document.getElementById('temperature').value),max_model_calls:Number(document.getElementById('maxCalls').value),...(key?{api_key:key}:{})}}
function sharedOptions(){let number=id=>Number(document.getElementById(id).value);return {n_students:number('nStudents'),n_staff:number('nStaff'),days:number('days'),periods_per_day:number('periods'),capacity:number('capacity'),open_every:number('openEvery'),access_barrier:number('barrier'),outreach_day:number('outreachDay'),...modelOptions()}}
let batchCount=document.createElement('p');document.getElementById('batchPolicies').append(batchCount);
function updateBatchCount(){let policies=document.querySelectorAll('#batchPolicies input:checked').length,seeds=document.getElementById('batchSeeds').value.split(/[,，]/).map(x=>x.trim()).filter(Boolean).length;batchCount.textContent='当前组合：'+policies+' 政策 × '+seeds+' 种子 = '+policies*seeds+' 次运行'}
for(let box of document.querySelectorAll('#batchPolicies input'))box.addEventListener('change',updateBatchCount);
document.getElementById('batchSeeds').addEventListener('input',updateBatchCount);updateBatchCount();
function appendProgress(parent,item){let value=item.progress||{percent:0,completed:0,total:0},wrap=document.createElement('div'),bar=document.createElement('progress'),label=document.createElement('small');wrap.className='job-progress';bar.max=100;bar.value=value.percent;let detail=value.total?(item.seeds?` · 已完成 ${value.finished_runs}/${value.run_count} 次运行，${value.completed}/${value.total} 模拟天`:` · 已完成 ${value.completed}/${value.total} 模拟天`):' · 等待进度数据';if(item.status==='running'&&value.total&&value.completed===value.total)detail+=' · 结果整理中';label.textContent=value.percent+'%'+detail;wrap.append(bar,label);parent.append(wrap)}
let analysisWatch={run:null,batch:null};
function analysisTarget(kind){return document.getElementById(kind==='run'?'runAnalysisReport':'batchAnalysisReport')}
function renderAnalysis(kind,data){let host=analysisTarget(kind);host.replaceChildren();if(data.status==='running'){host.textContent='分析 Agent 正在读取证据并生成报告…';return}if(data.status==='failed'){host.textContent='分析失败：'+data.error;return}
  let report=data.result,title=document.createElement('h3');title.textContent='分析报告 · '+report.scope.name;host.append(title);
  let info=document.createElement('small');info.textContent='模型：'+report.analysis_model.provider+' / '+report.analysis_model.model+' · 提示词：'+report.prompt_version+' · 文件：'+data.report;host.append(info);
  let byId=new Map(report.evidence.map(item=>[item.id,item]));
  for(let [label,entries] of [['主要发现',report.findings],['校园启示与待验证建议',report.recommendations]]){let heading=document.createElement('h4');heading.textContent=label;host.append(heading);for(let entry of entries){let block=document.createElement('div');block.className='analysis-item';let name=document.createElement('strong');name.textContent=(entry.title||entry.action||'分析')+(entry.status==='hypothesis'?'（假设）':'');block.append(name);for(let key of (entry.title?['interpretation']:['rationale','verification'])){let p=document.createElement('p');p.textContent=entry[key];block.append(p)}for(let id of entry.evidence_ids){let item=byId.get(id);if(!item)continue;let p=document.createElement('small');p.textContent=id+' '+item.label+'：'+JSON.stringify(item.value)+'（'+item.source+'）';block.append(p,document.createElement('br'))}if(entry.review_notes.length){let p=document.createElement('small');p.textContent='复核：'+entry.review_notes.join('；');block.append(p)}host.append(block)}}
  let limits=document.createElement('h4');limits.textContent='局限';host.append(limits);for(let value of report.limitations){let p=document.createElement('p');p.textContent=value;host.append(p)}let audit=document.createElement('small');audit.textContent=report.audit_note;host.append(audit)
}
async function pollAnalysis(kind,name){try{let data=await j('/api/analysis/'+kind+'/'+name);if(kind==='run'&&selectedRun!==name)return;renderAnalysis(kind,data);if(data.status==='running'){analysisWatch[kind]=setTimeout(()=>pollAnalysis(kind,name),2500)}}catch(e){analysisTarget(kind).textContent=e.message}}
async function loadExistingAnalysis(kind,name){try{let response=await fetch('/api/analysis/'+kind+'/'+name);if(response.ok)pollAnalysis(kind,name)}catch{}}
async function launchAnalysis(kind,name){let host=analysisTarget(kind),data={kind,name,provider:document.getElementById('analysisProvider').value,model:document.getElementById('analysisModel').value,api_key:document.getElementById('analysisKey').value.trim()};try{let response=await fetch('/api/analysis',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}),result=await response.json();if(!response.ok)throw Error(result.error);document.getElementById('analysisKey').value='';if(analysisWatch[kind])clearTimeout(analysisWatch[kind]);host.textContent='分析已启动…';pollAnalysis(kind,name)}catch(e){host.textContent='无法启动分析：'+e.message}}
function renderRuns(){
  let data=latestData,body=document.getElementById('runs');body.replaceChildren();
  for(let x of data.runs.filter(x=>!hiddenNames.has(x.name))){
    let tr=document.createElement('tr'),model=x.driver==='llm'?x.model_provider+' / '+x.model_id:'离线规则';
    let values=[x.name,model,x.condition,x.n_students,x.n_staff,x.complete_steps,pct(x.requested_rate),pct(x.supported_rate),x.completed_wait_mean_days==null?'—':x.completed_wait_mean_days.toFixed(2)];
    for(let v of values){let td=document.createElement('td');td.textContent=v;tr.append(td)}
    let action=document.createElement('td'),remove=document.createElement('button');
    remove.textContent='删除记录';remove.title='永久移除网页显示，不删除实验文件';
    remove.onclick=e=>{e.stopPropagation();hideRun(x.name)};action.append(remove);tr.append(action);
    tr.style.cursor='pointer';tr.onclick=()=>show(x.name);body.append(tr)
  }
  let listedRuns=new Set(data.runs.map(x=>x.name));
  let jobs=document.getElementById('jobs');jobs.replaceChildren();
  for(let x of data.jobs.filter(x=>
    !hiddenNames.has(x.name)&&!(x.status==='complete'&&listedRuns.has(x.name))
  )){let title=document.createElement('div');title.textContent=x.name+'：'+x.status+(x.error?'（查看服务器状态文件）':'');jobs.append(title);if(x.status==='running'||x.status==='failed')appendProgress(jobs,x)}
  let b=document.getElementById('batches');b.replaceChildren();
  for(let run of data.batches.filter(x=>!hiddenNames.has(x.name))){
    let p=document.createElement('p'),conditions=run.by_condition?Object.keys(run.by_condition):(run.conditions||[]);
    let seeds=run.by_condition?run.by_condition[conditions[0]]?.requested_rate?.n_seeds:run.seeds?.length;
    p.textContent=run.name+'：'+run.status+(seeds?'（'+conditions.length+' 政策 × '+seeds+' 种子）':'');
    if(run.status==='complete'&&run.by_condition){let analyze=document.createElement('button');analyze.textContent='分析政策对照';analyze.onclick=()=>launchAnalysis('batch',run.name);p.append(analyze);let previous=document.createElement('button');previous.textContent='查看最近分析';previous.onclick=()=>loadExistingAnalysis('batch',run.name);p.append(previous)}
    let removeBatch=document.createElement('button');removeBatch.textContent='删除记录';removeBatch.onclick=()=>hideRun(run.name);p.append(removeBatch);b.append(p);
    if(run.status==='running'||run.status==='failed')appendProgress(b,run);
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
  let hiddenCount=data.hidden_count??hiddenNames.size;
  document.getElementById('hiddenCount').textContent=hiddenCount?'已删除 '+hiddenCount+' 条显示记录':''
}
async function refresh(){try{if(!hiddenSynced&&hiddenNames.size){await persistHidden([...hiddenNames]);localStorage.removeItem('campus-sim-hidden-runs');hiddenNames.clear()}hiddenSynced=true;latestData=await j('/api/runs');if(selectedRun&&!latestData.runs.some(x=>x.name===selectedRun))resetSelected();renderRuns()}catch(e){document.getElementById('message').textContent=e.message}}
function resetSelected(){
  selectedRun=null;eventOffset=0;eventTotal=0;
  if(analysisWatch.run)clearTimeout(analysisWatch.run);
  document.getElementById('runAnalysisReport').replaceChildren();
  document.getElementById('detail').textContent='请选择一个运行。';
  document.getElementById('events').replaceChildren();document.getElementById('eventStatus').textContent='请选择一个运行。';
  let canvas=document.getElementById('chart');canvas.getContext('2d').clearRect(0,0,canvas.width,canvas.height)
}
async function hideRun(name){
  try{await persistHidden([name]);if(selectedRun===name)resetSelected();await refresh()}
  catch(e){document.getElementById('hiddenCount').textContent='删除记录失败：'+e.message}
}
async function clearDisplayed(){
  let names=[...new Set([latestData.runs,latestData.jobs,latestData.batches].flatMap(group=>group.map(item=>item.name)))];
  if(!names.length)return;
  try{await persistHidden(names);resetSelected();await refresh()}
  catch(e){document.getElementById('hiddenCount').textContent='清空显示失败：'+e.message}
}
function actorOptions(summary){let select=document.getElementById('eventActor');select.replaceChildren();let all=document.createElement('option');all.value='';all.textContent='全部主体';select.append(all);for(let [prefix,count,label] of [['c',summary.n_staff,'辅导员'],['s',summary.n_students,'学生']]){let group=document.createElement('optgroup');group.label=label;for(let i=0;i<count;i++){let id=prefix+String(i).padStart(3,'0'),option=document.createElement('option');option.value=id;option.textContent=label+' '+id;group.append(option)}select.append(group)}}
async function show(name){
  let d=document.getElementById('detail');
  try{
    let x=await j('/api/runs/'+name);selectedRun=name;eventOffset=0;
    actorOptions(x.summary);document.getElementById('eventDay').value='';document.getElementById('eventAction').value='';loadEvents(0);
    d.replaceChildren();let summary=document.createElement('table');
    for(let [label,value] of [
      ['实验名称',name],['学生人数',x.summary.n_students+' 名'],['辅导员人数',x.summary.n_staff+' 名'],
      ['运行天数',x.summary.complete_steps+' 天'],['求助率',pct(x.summary.requested_rate)],
      ['获得支持率',pct(x.summary.supported_rate)],['持续低联结率',pct(x.summary.low_connection_7d_rate)],
      ['压力均值',x.summary.stress_distribution.mean.toFixed(3)]
    ]){let row=document.createElement('tr'),key=document.createElement('th'),valueCell=document.createElement('td');key.scope='row';key.style.width='140px';key.textContent=label;valueCell.textContent=value;valueCell.style.overflowWrap='anywhere';row.append(key,valueCell);summary.append(row)}
    d.append(summary);
    document.getElementById('runAnalysisReport').replaceChildren();
    let analyze=document.createElement('button');analyze.textContent='生成分析报告';analyze.onclick=()=>launchAnalysis('run',name);d.append(analyze);
    loadExistingAnalysis('run',name);
    let guide=document.createElement('div');guide.className='metric-guide';
    let heading=document.createElement('strong');heading.textContent='指标含义';guide.append(heading);
    let descriptions=document.createElement('ul');
    for(let [label,meaning] of [
      ['求助率','运行期间至少提出过一次求助的学生数 ÷ 全体学生数；反映模拟中的求助行为覆盖范围，不代表求助次数。'],
      ['获得支持率','运行期间至少获得过一次辅导员支持的学生数 ÷ 全体学生数；反映模拟服务触达范围，不等于求助后的成功率。'],
      ['持续低联结率','运行结束时，连续至少 7 天联结值低于 0.3 的学生数 ÷ 全体学生数；反映模拟中持续缺少联结的学生占比。'],
      ['压力均值','运行结束时全体学生压力值的平均数，取值范围为 0–1；反映模型内的总体压力水平，不是临床测量结果。']
    ]){let item=document.createElement('li'),title=document.createElement('strong');title.textContent=label+'：';item.append(title,document.createTextNode(meaning));descriptions.append(item)}
    guide.append(descriptions);d.append(guide);
    if(x.daily_metrics_error){let error=document.createElement('p');error.textContent='每日指标读取失败：'+x.daily_metrics_error;d.append(error)}
    drawStressChart(x.days)
  }catch(e){selectedRun=null;d.textContent='无法打开运行 '+name+'：'+e.message}
}
function drawStressChart(metrics){
  let c=document.getElementById('chart'),ctx=c.getContext('2d');
  let days=metrics.filter(p=>Number.isFinite(p.day)).sort((a,b)=>a.day-b.day);
  let first=days.length?days[0].day:1,last=days.length?days[days.length-1].day:1;
  c.width=Math.max(700,(last-first+1)*75+145);c.height=320;
  ctx.font='14px system-ui';ctx.fillStyle='#172337';
  ctx.fillText('逐日平均压力（模型变量）',55,25);
  if(!days.some(p=>Number.isFinite(p.mean_stress))){ctx.fillText('暂无每日压力数据',55,65);return}
  let left=110,right=c.width-40,top=80,bottom=280;
  let X=day=>left+(day-first)*(right-left)/Math.max(1,last-first),Y=value=>bottom-value*(bottom-top);
  ctx.lineWidth=1;
  for(let value of [0,.25,.5,.75,1]){
    ctx.strokeStyle='#dce2eb';ctx.beginPath();ctx.moveTo(left,Y(value));ctx.lineTo(right,Y(value));ctx.stroke();
    ctx.fillStyle='#526279';ctx.textAlign='right';ctx.fillText(String(value),left-28,Y(value)+5)
  }
  ctx.textAlign='center';
  for(let day=first;day<=last;day++){
    ctx.fillStyle='#172337';ctx.fillText('第 '+day+' 天',X(day),58);
    ctx.strokeStyle='#edf0f5';ctx.beginPath();ctx.moveTo(X(day),top);ctx.lineTo(X(day),bottom);ctx.stroke()
  }
  ctx.strokeStyle='#1756a9';ctx.lineWidth=3;ctx.beginPath();let previous=null;
  for(let p of days){
    if(!Number.isFinite(p.mean_stress)){previous=null;continue}
    if(previous!==null&&p.day===previous+1)ctx.lineTo(X(p.day),Y(p.mean_stress));else ctx.moveTo(X(p.day),Y(p.mean_stress));
    previous=p.day
  }
  ctx.stroke();
  for(let p of days.filter(p=>Number.isFinite(p.mean_stress))){
    ctx.fillStyle='#1756a9';ctx.beginPath();ctx.arc(X(p.day),Y(p.mean_stress),4,0,Math.PI*2);ctx.fill();
    ctx.fillStyle='#172337';ctx.fillText(p.mean_stress.toFixed(3),X(p.day),Y(p.mean_stress)-12)
  }
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
      for(const group of groupTimelineActions(row.events)){
        const line=document.createElement('div'),label=document.createElement('strong');
        line.className='timeline-period';label.textContent=group.label+'：';
        line.append(label,document.createTextNode(group.events.length?group.events.map(e=>actionLabel(e.action)).join(' → '):'—'));
        pathCell.append(line)
      }
      let detailCell=document.createElement('td'),list=document.createElement('ol');list.className='timeline-list';
      for(let event of row.events){
        let item=document.createElement('li'),header=document.createElement('span');
        let related=event.actor!==row.actor?' · 涉及 '+event.actor:'';
        header.textContent=(event.detail?.period?(PERIOD_LABELS[event.detail.period]||event.detail.period)+' · ':'')+actionLabel(event.action)+'（'+event.action+'） · '+event.status+(event.reason?' · '+event.reason:'')+related;
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
async function launch(){let m=document.getElementById('message');try{let data={condition:document.getElementById('condition').value,seed:Number(document.getElementById('seed').value),...sharedOptions()};let r=await fetch('/api/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});let x=await r.json();if(!r.ok)throw Error(x.error);document.getElementById('apiKey').value='';m.textContent='已启动 '+x.name+'；运行完成后会出现在下方列表。';refresh()}catch(e){m.textContent=e.message}}refresh();initModels();setInterval(refresh,5000);
async function launchBatch(){let m=document.getElementById('batchMessage');try{let tokens=document.getElementById('batchSeeds').value.split(/[,，]/).map(x=>x.trim());if(tokens.some(x=>!/^[0-9]+$/.test(x)))throw Error('种子须为以逗号分隔的整数');let conditions=[...document.querySelectorAll('#batchPolicies input:checked')].map(x=>x.value);let data={conditions,seeds:tokens.map(Number),...sharedOptions()};let r=await fetch('/api/batches',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});let x=await r.json();if(!r.ok)throw Error(x.error);document.getElementById('apiKey').value='';m.textContent='已启动 '+x.name+'（'+conditions.length+' 政策 × '+data.seeds.length+' 种子）；请稍后刷新。';refresh()}catch(e){m.textContent=e.message}}
</script></html>"""


def create_handler(root):
    root = Path(root).resolve()
    project = Path(__file__).resolve().parents[2]
    hidden_lock = threading.Lock()
    analysis_lock = threading.Lock()
    analysis_io_lock = threading.Lock()
    analysis_instance = uuid.uuid4().hex

    def analysis_status_path(kind, name):
        digest = hashlib.sha256(f"{kind}:{name}".encode()).hexdigest()
        return root / ".analysis-status" / f"{digest}.json"

    def write_analysis_status(path, value):
        with analysis_io_lock:
            path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def start_analysis(kind, name, provider, model, api_key):
        status_path = analysis_status_path(kind, name)
        with analysis_lock:
            if status_path.is_file():
                previous = json.loads(status_path.read_text(encoding="utf-8"))
                if (previous.get("status") == "running"
                        and previous.get("instance") == analysis_instance):
                    raise ValueError("analysis is already running")
            status_path.parent.mkdir(exist_ok=True)
            write_analysis_status(status_path, {"status": "running", "name": name,
                "kind": kind, "instance": analysis_instance, "started_at": time.time()})

        def work():
            try:
                target = root.joinpath(*name.split("/"))
                if kind == "run":
                    packet = run_evidence(target)
                else:
                    packet = batch_evidence(target, root / "configs" / f"{name}.json")
                report = generate_report(packet, provider, model, api_key=api_key)
                saved = save_report(target, report)
                result = {"status": "complete", "name": name, "kind": kind,
                          "report": str(saved.relative_to(root).as_posix()),
                          "finished_at": time.time()}
            except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
                message = str(error).replace(api_key, "[REDACTED]") if api_key else str(error)
                result = {"status": "failed", "name": name, "kind": kind,
                          "error": message[:500], "finished_at": time.time()}
            write_analysis_status(status_path, result)

        threading.Thread(target=work, daemon=True).start()

    def spawn(command, name, started_at=None, key_env=None, api_key=None, metadata=None):
        started_at = started_at if started_at is not None else time.time()
        environment = os.environ.copy()
        if api_key:
            environment[key_env] = api_key
        def work():
            result = subprocess.run(command, cwd=project, capture_output=True, text=True,
                                    check=False, env=environment)
            status = "complete" if result.returncode == 0 else "failed"
            error = result.stderr if result.returncode else None
            if error and api_key:
                error = error.replace(api_key, "[REDACTED]")
            if error:
                error = error[-3000:]
            (root / f"{name}.status.json").write_text(json.dumps({
                "status": status, "returncode": result.returncode,
                "started_at": started_at,
                "error": error, **(metadata or {}),
            }), encoding="utf-8")
        (root / f"{name}.status.json").write_text(json.dumps({
            "status": "running", "started_at": started_at, **(metadata or {}),
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
            analysis_match = re.fullmatch(r"/api/analysis/(run|batch)/([a-zA-Z0-9_./-]+)", path)
            if analysis_match:
                kind, name = analysis_match.groups()
                if not valid_record_name(name) or (kind == "batch" and "/" in name):
                    return self.send_data(404, {"error": "analysis not found"})
                status_path = analysis_status_path(kind, name)
                try:
                    with analysis_io_lock:
                        status = json.loads(status_path.read_text(encoding="utf-8"))
                    if status["status"] == "complete":
                        status["result"] = json.loads((root / status["report"]).read_text(
                            encoding="utf-8"))
                    return self.send_data(200, status)
                except (OSError, ValueError, KeyError):
                    return self.send_data(404, {"error": "analysis not found"})
            if path == "/api/runs":
                hidden = read_hidden_names(root)
                def visible(name):
                    return name not in hidden and name.split("/", 1)[0] not in hidden
                runs = [item for item in list_run_summaries(root) if visible(item["name"])]
                batches = []
                for status_file in sorted(root.glob("batch-*.status.json"),
                                          key=lambda item: item.stat().st_mtime,
                                          reverse=True):
                    name = status_file.name.removesuffix(".status.json")
                    if not visible(name):
                        continue
                    try:
                        record = json.loads(status_file.read_text(encoding="utf-8"))
                        record["progress"] = read_job_progress(root, name, record["status"],
                                                                 batch=True)
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
                        name = status_file.name.removesuffix(".status.json")
                        if not visible(name):
                            continue
                        record = json.loads(status_file.read_text(encoding="utf-8"))
                        record["progress"] = read_job_progress(root, name, record["status"])
                        jobs.append({"name": name, **record})
                    except (ValueError, OSError):
                        continue
                return self.send_data(200, {"runs": runs, "batches": batches, "jobs": jobs,
                                            "hidden_count": len(hidden)})
            event_match = re.fullmatch(r"/api/runs/([a-zA-Z0-9_./-]+)/(events|timeline)", path)
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
            match = re.fullmatch(r"/api/runs/([a-zA-Z0-9_./-]+)", path)
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
            if origin and origin != "http://" + self.headers.get("Host", ""):
                return self.send_data(403, {"error": "same-origin request required"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                maximum = 65536 if self.path == "/api/hidden-runs" else 4096
                if length < 1 or length > maximum:
                    raise ValueError("invalid request size")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise TypeError("request body must be an object")
                if self.path == "/api/hidden-runs":
                    with hidden_lock:
                        hidden = hide_record_names(root, data.get("names"))
                    return self.send_data(200, {"hidden_count": len(hidden)})
                if self.path == "/api/analysis":
                    kind, name = data.get("kind"), data.get("name")
                    if kind not in {"run", "batch"} or not valid_record_name(name):
                        raise ValueError("invalid analysis target")
                    if kind == "batch" and ("/" in name or not name.startswith("batch-")):
                        raise ValueError("invalid batch target")
                    provider, model = data.get("provider"), data.get("model")
                    validate(provider, model, 1)
                    api_key = data.get("api_key")
                    if api_key is not None and (not isinstance(api_key, str)
                                                or not 1 <= len(api_key.strip()) <= 512
                                                or not api_key.isprintable()):
                        raise ValueError("invalid API key")
                    if not api_key:
                        require_key(provider)
                    target = root.joinpath(*name.split("/"))
                    if kind == "run" and not (target / "summary.json").is_file():
                        raise ValueError("run is not complete")
                    if kind == "batch" and not (target / "comparison.json").is_file():
                        raise ValueError("batch is not complete")
                    start_analysis(kind, name, provider, model,
                                   api_key.strip() if api_key else None)
                    return self.send_data(202, {"kind": kind, "name": name,
                                                "status": "running"})
                provider = data.get("provider")
                api_key = data.get("api_key")
                if api_key is not None and (not isinstance(api_key, str)
                                            or not 1 <= len(api_key.strip()) <= 512
                                            or not api_key.isprintable()):
                    raise ValueError("invalid API key")
                if api_key:
                    api_key = api_key.strip()
                if api_key and provider is None:
                    raise ValueError("API key requires a model provider")
                key_env = None
                if provider is not None:
                    model, max_calls = data.get("model"), data.get("max_model_calls")
                    validate(provider, model, max_calls)
                    if not api_key:
                        require_key(provider)
                    key_env = PROVIDERS[provider]["env"]
                    temperature = data.get("temperature", 0.2)
                    if type(temperature) not in (int, float) or not 0 <= temperature <= 2:
                        raise ValueError("temperature must be in [0,2]")
                elif set(data) & {"model", "max_model_calls", "temperature"}:
                    raise ValueError("model settings require provider")
            except (ValueError, TypeError, json.JSONDecodeError) as error:
                return self.send_data(400, {"error": str(error)})
            if self.path == "/api/batches":
                try:
                    plan = build_batch_plan(project, data, api_key=api_key)
                    name = f"batch-{uuid.uuid4().hex[:8]}"
                    config_dir = root / "configs"
                    config_dir.mkdir(exist_ok=True)
                    plan_path = config_dir / f"{name}.json"
                    with plan_path.open("x", encoding="utf-8") as stream:
                        json.dump(plan, stream, ensure_ascii=False, indent=2)
                    command = [sys.executable, "-m", "campus_sim.cli", "batch",
                               "--plan", str(plan_path), "--output", str(root / name)]
                    spawn(command, name, key_env=key_env, api_key=api_key,
                          metadata={"conditions": plan["conditions"], "seeds": plan["seeds"]})
                    return self.send_data(202, {"name": name})
                except (ValueError, KeyError, TypeError) as error:
                    return self.send_data(400, {"error": str(error)})
            if self.path != "/api/runs":
                return self.send_data(404, {"error": "not found"})
            try:
                config_data = build_run_config(project, data, api_key=api_key)
                started = datetime.now(DISPLAY_TIMEZONE)
                name, config = save_run_config(root, config_data, started)
                output = root / name
                command = [sys.executable, "-m", "campus_sim.cli", "run", "--config",
                           str(config), "--output", str(output)]
                spawn(command, name, started.timestamp(), key_env=key_env, api_key=api_key)
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
