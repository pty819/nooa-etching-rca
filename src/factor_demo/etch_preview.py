"""Inspectable plots and an offline browser for the generated 200-channel traces."""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl


def preview_etching(directory: Path) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    meta = pl.read_parquet(directory / "runs.parquet")
    bounds = pl.read_parquet(directory / "steps.parquet")
    target = meta.filter(~pl.col("is_control"))
    # Older datasets may contain several recipes; compare only one shared recipe.
    for recipe_id in target["recipe_id"].unique().sort():
        matching = target.filter(pl.col("recipe_id") == recipe_id)
        if matching["period_id"].n_unique() == 3:
            target = matching
            break
    chosen = target.group_by("period_id", maintain_order=True).first()
    sensors = json.loads((directory / "sensors.json").read_text())
    examples = []
    outputs = []
    plots = [
        ("RF power / W", ["rf_source_forward_w", "rf_source_reflected_w", "rf_bias_forward_w"]),
        ("Chamber pressure / mTorr", ["vac_pressure_sp_mtorr", "vac_pressure_mtorr"]),
        ("Pressure control / %", ["vac_pcv_sp_pct", "vac_pcv_pct"]),
        (
            "ESC temperature / degC",
            ["heater_z1_sp_c", "thermal_esc_center_c", "thermal_esc_edge_c"],
        ),
        (
            "Backside helium pressure / Torr",
            ["he_edge_pressure_sp_torr", "he_center_pressure_torr", "he_edge_pressure_torr"],
        ),
        ("Backside helium inlet / sccm", ["he_center_inlet_sccm", "he_edge_inlet_sccm"]),
        ("RF matching capacitance / pF", ["rf_source_c1_pf", "rf_source_c2_pf"]),
        ("OES proxies / a.u.", ["oes_f_703", "oes_sif_product", "oes_ar_750"]),
        ("Transfer radial position / mm", ["robot_radial_mm"]),
    ]
    for run in chosen.iter_rows(named=True):
        trace = pl.read_parquet(directory / run["trace_path"])
        steps = bounds.filter(pl.col("run_id") == run["run_id"])
        fig, axes = plt.subplots(len(plots), 1, figsize=(15, 18), sharex=True, layout="constrained")
        t = trace["time_s"].to_numpy()
        for ax, (title, channels) in zip(axes, plots):
            for channel in channels:
                ax.plot(t, trace[channel].to_numpy(), label=channel, lw=1)
            for k, s in enumerate(steps.iter_rows(named=True)):
                if k % 2 == 0:
                    ax.axvspan(s["start_s"], s["end_s"], color="grey", alpha=0.08)
                ax.axvline(s["start_s"], color="grey", alpha=0.25, lw=0.5)
            ax.set_ylabel(title, fontsize=9)
            ax.legend(loc="upper right", fontsize=7, ncol=2)
            ax.grid(alpha=0.15)
        centers = (steps["start_s"].to_numpy() + steps["end_s"].to_numpy()) / 2
        top = axes[0].secondary_xaxis("top")
        top.set_xticks(centers, steps["step_name"].to_list(), rotation=40, ha="left", fontsize=8)
        axes[-1].set_xlabel("Seconds from start; actual per-run step boundaries")
        fig.suptitle(
            f"ICP reduced-order simulation — {run['run_id']} / {run['label']} / {run['recipe_id']}\n"
            "Observed FDC channels; unobserved wafer temperatures are not plotted",
            fontsize=13,
        )
        filename = f"preview-period-{run['period_id']}.png"
        fig.savefig(directory / filename, dpi=130)
        plt.close(fig)
        outputs.append(filename)
        clean = trace.with_columns(pl.col(pl.Float32, pl.Float64).fill_nan(None))
        examples.append(
            {
                "id": run["run_id"],
                "label": run["label"],
                "recipe": run["recipe_id"],
                "steps": steps.select("step_id", "step_name", "start_s", "end_s").to_dicts(),
                "time": clean["time_s"].to_list(),
                "channels": {
                    s["name"]: [
                        None if v is None else round(v, 5) for v in clean[s["name"]].to_list()
                    ]
                    for s in sensors
                },
            }
        )
    payload = json.dumps(
        {"sensors": sensors, "examples": examples}, ensure_ascii=False, allow_nan=False
    )
    html = r"""<!doctype html><meta charset="utf-8"><title>ICP etch trace explorer</title>
<style>body{font:15px system-ui;margin:30px;color:#172238;background:#f5f7fb}select{padding:9px;margin:6px;max-width:650px}canvas{background:white;border:1px solid #ccd5e3;width:100%;height:460px}table{border-collapse:collapse;background:white}td,th{padding:6px 12px;border-bottom:1px solid #ddd}#tip{white-space:pre-wrap;min-height:50px}p{max-width:1100px}</style>
<h1>ICP etch: 200-channel trace explorer</h1><p>Physics-inspired reduced-order synthetic data, not measured fab data. Three chronological case periods. Select any channel; curves use actual elapsed time, without time warping. Shading marks the selected reference run's steps. Commands, measurements and derived channels are distinguished.</p>
<label>Channel <select id="sensor"></select></label><label>Step reference <select id="reference"></select></label>
<p id="description"></p><canvas id="plot" width="1500" height="500"></canvas><p id="tip"></p><div id="steps"></div>
<script type="application/json" id="data">PAYLOAD</script><script>
const D=JSON.parse(document.getElementById('data').textContent), S=document.getElementById('sensor'), R=document.getElementById('reference'), C=document.getElementById('plot'), X=C.getContext('2d'), colors=['#2463b8','#d3493d','#19976b'];let scale;
for(const s of D.sensors){let o=document.createElement('option');o.value=s.name;o.textContent=s.subsystem+' · '+s.name+' ['+s.unit+']';S.append(o)}
for(let i=0;i<D.examples.length;i++){let o=document.createElement('option');o.value=i;o.textContent=D.examples[i].label+' / '+D.examples[i].id;R.append(o)}S.value='he_edge_inlet_sccm';
function draw(){let sensor=D.sensors.find(s=>s.name===S.value),ref=D.examples[+R.value],all=D.examples.flatMap(r=>r.channels[S.value]).filter(v=>v!==null),lo=Math.min(...all),hi=Math.max(...all);if(hi-lo<1e-6){lo-=1;hi+=1}let pad=(hi-lo)*.1;lo-=pad;hi+=pad;let T=Math.max(...D.examples.map(r=>r.time.at(-1))),L=70,B=440,W=1390,H=390;scale={T,lo,hi,L,B,W,H};X.clearRect(0,0,1500,500);X.font='13px system-ui';
ref.steps.forEach((s,i)=>{if(i%2===0){X.fillStyle='#eaf0f7';X.fillRect(L+s.start_s/T*W,40,(s.end_s-s.start_s)/T*W,H)}X.fillStyle='#536070';X.fillText(s.step_id,L+(s.start_s+s.end_s)/2/T*W,30)});
for(let i=0;i<=5;i++){let y=B-i/5*H;X.strokeStyle='#dce2ea';X.beginPath();X.moveTo(L,y);X.lineTo(L+W,y);X.stroke();X.fillStyle='#536070';X.fillText((lo+(hi-lo)*i/5).toPrecision(4),3,y+4)}
D.examples.forEach((r,i)=>{X.strokeStyle=colors[i];X.lineWidth=1.8;X.beginPath();let active=false;r.time.forEach((t,k)=>{let v=r.channels[S.value][k];if(v===null){active=false;return}let x=L+t/T*W,y=B-(v-lo)/(hi-lo)*H;if(!active)X.moveTo(x,y);else X.lineTo(x,y);active=true});X.stroke();X.fillStyle=colors[i];X.fillText(r.label+' / '+r.id+' / '+r.recipe,100+i*440,480)});X.fillStyle='#172238';X.fillText('Seconds from start',650,462);
document.getElementById('description').textContent=sensor.description;document.getElementById('steps').innerHTML='<table><tr><th>Step</th><th>Name</th><th>Start / s</th><th>End / s</th></tr>'+ref.steps.map(s=>'<tr><td>'+s.step_id+'</td><td>'+s.step_name+'</td><td>'+s.start_s.toFixed(1)+'</td><td>'+s.end_s.toFixed(1)+'</td></tr>').join('')}
S.onchange=R.onchange=draw;C.onmousemove=e=>{let t=(e.clientX-C.getBoundingClientRect().left)/C.clientWidth*1500;t=Math.max(0,Math.min(scale.T,(t-scale.L)/scale.W*scale.T));document.getElementById('tip').textContent='t='+t.toFixed(2)+' s\n'+D.examples.map(r=>{let k=r.time.findIndex(x=>x>=t);if(k<0)return r.label+': ended';return r.label+': '+r.channels[S.value][k]}).join('   ')};draw();</script>""".replace(
        "PAYLOAD", payload.replace("</", "<\\/")
    )
    (directory / "trace-explorer.html").write_text(html)
    outputs.append("trace-explorer.html")
    return outputs
