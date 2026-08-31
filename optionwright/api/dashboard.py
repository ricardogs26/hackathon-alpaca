"""The demo dashboard: a single self-contained HTML page served at /.

Vanilla JS + inline SVG, no external libraries (the CSP-free, dependency-free
page loads anywhere). It polls the read-only /api/* endpoints and renders the
account equity, open/closed spreads, and the decision log — the *why* behind
every cycle, which is the point of the agent.
"""

DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>optionwright</title>
<style>
  :root{
    --bg:#0e1613; --surface:#161f1b; --raise:#1b2621; --ink:#e6efe9; --ink2:#9db0a6; --ink3:#71847a;
    --line:#26332d; --acc:#3dba8c; --acc2:#57c99e; --gain:#3dba8c; --loss:#d97b74; --warn:#d99a4e;
    --mono:"IBM Plex Mono",ui-monospace,Menlo,monospace;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
    font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased}
  .wrap{max-width:1080px;margin:0 auto;padding:28px 20px 80px}
  header{display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:16px;
    border-bottom:1px solid var(--line);padding-bottom:20px}
  h1{font-size:1.9rem;margin:0;letter-spacing:-.5px}
  h1 b{color:var(--acc)}
  .tagline{color:var(--ink2);font-size:.92rem;margin-top:4px}
  .status{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
  .dot{width:8px;height:8px;border-radius:50%;background:var(--acc);display:inline-block;
    box-shadow:0 0 0 3px rgba(61,186,140,.18)}
  .pill{font:500 .7rem/1 var(--mono);letter-spacing:.04em;padding:.4em .65em;border-radius:99px;
    background:var(--raise);border:1px solid var(--line);color:var(--ink2)}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:22px}
  @media(max-width:720px){.grid{grid-template-columns:1fr}}
  .card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:18px 20px}
  .card.wide{grid-column:1/-1}
  .card h2{font-size:.72rem;letter-spacing:.14em;text-transform:uppercase;color:var(--ink3);
    margin:0 0 14px;font-family:var(--mono);font-weight:500}
  .big{font-size:2.1rem;font-weight:700;font-variant-numeric:tabular-nums;letter-spacing:-1px}
  .sub{color:var(--ink2);font-size:.9rem;margin-top:2px}
  .up{color:var(--gain)} .down{color:var(--loss)}
  svg{width:100%;height:120px;display:block;margin-top:6px}
  table{width:100%;border-collapse:collapse;font-size:.88rem}
  th{text-align:left;font:500 .64rem/1.3 var(--mono);letter-spacing:.08em;text-transform:uppercase;
    color:var(--ink3);padding:8px 8px;border-bottom:1px solid var(--line)}
  td{padding:9px 8px;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}
  tr:last-child td{border-bottom:none}
  .tag{font:500 .68rem/1 var(--mono);padding:.28em .5em;border-radius:5px;white-space:nowrap}
  .t-bull{background:rgba(61,186,140,.14);color:var(--acc)}
  .t-bear{background:rgba(217,123,116,.14);color:var(--loss)}
  .t-abs{background:var(--raise);color:var(--ink3)}
  .t-open{background:rgba(87,201,158,.14);color:var(--acc2)}
  .t-closed{background:var(--raise);color:var(--ink2)}
  .feed{display:flex;flex-direction:column;gap:0}
  .d{display:grid;grid-template-columns:64px 1fr auto;gap:10px;align-items:baseline;
    padding:10px 0;border-bottom:1px solid var(--line)}
  .d:last-child{border-bottom:none}
  .d .tm{font:500 .72rem/1.3 var(--mono);color:var(--ink3)}
  .d .rs{color:var(--ink2);font-size:.86rem}
  .d .rs b{color:var(--ink)}
  .muted{color:var(--ink3);font-size:.86rem;padding:14px 0}
  .foot{margin-top:26px;color:var(--ink3);font-size:.78rem;text-align:center;font-family:var(--mono)}
  a{color:var(--acc)}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>option<b>wright</b></h1>
      <div class="tagline">The LLM proposes. The code decides. Defined-risk options on Alpaca paper.</div>
    </div>
    <div class="status" id="status"><span class="pill">connecting…</span></div>
  </header>

  <div class="grid">
    <div class="card">
      <h2>Account equity</h2>
      <div class="big" id="equity">—</div>
      <div class="sub" id="pnl">since $100,000 start</div>
      <svg id="spark" viewBox="0 0 600 120" preserveAspectRatio="none"></svg>
    </div>
    <div class="card">
      <h2>This session</h2>
      <div id="counts"></div>
    </div>
    <div class="card wide">
      <h2>Positions</h2>
      <div id="positions"><div class="muted">No positions yet.</div></div>
    </div>
    <div class="card wide">
      <h2>Decision log — why each cycle acted</h2>
      <div class="feed" id="decisions"><div class="muted">Waiting for the first cycle…</div></div>
    </div>
  </div>
  <div class="foot">optionwright · paper trading · auto-refresh 15s · <a href="/metrics">/metrics</a></div>
</div>

<script>
const $ = id => document.getElementById(id);
const fmt = n => (n==null?'—':Number(n).toLocaleString('en-US',{maximumFractionDigits:2}));
const money = n => '$'+fmt(n);
async function j(u){ try{ const r=await fetch(u); return await r.json(); }catch(e){ return null; } }

function spark(pts){
  const svg=$('spark'); svg.innerHTML='';
  if(!pts||pts.length<2){ return; }
  const ys=pts.map(p=>p.equity), min=Math.min(...ys), max=Math.max(...ys), rng=(max-min)||1;
  const W=600,H=120,pad=6;
  const x=i=>pad+i*(W-2*pad)/(pts.length-1);
  const y=v=>H-pad-((v-min)/rng)*(H-2*pad);
  let d='M'+pts.map((p,i)=>x(i).toFixed(1)+','+y(p.equity).toFixed(1)).join(' L');
  const up = ys[ys.length-1] >= ys[0];
  const col = up ? 'var(--gain)' : 'var(--loss)';
  const area = d+` L${x(pts.length-1).toFixed(1)},${H-pad} L${x(0).toFixed(1)},${H-pad} Z`;
  svg.innerHTML =
    `<defs><linearGradient id="g" x1="0" x2="0" y1="0" y2="1">
       <stop offset="0" stop-color="${col}" stop-opacity="0.28"/>
       <stop offset="1" stop-color="${col}" stop-opacity="0"/></linearGradient></defs>
     <path d="${area}" fill="url(#g)"/>
     <path d="${d}" fill="none" stroke="${col}" stroke-width="2" stroke-linejoin="round"/>
     <circle cx="${x(pts.length-1).toFixed(1)}" cy="${y(ys[ys.length-1]).toFixed(1)}" r="3.2" fill="${col}"/>`;
}

function dirTag(d){
  if(d==='bullish') return '<span class="tag t-bull">bullish</span>';
  if(d==='bearish') return '<span class="tag t-bear">bearish</span>';
  return '<span class="tag t-abs">abstain</span>';
}

async function refresh(){
  const st = await j('/api/status');
  if(st){
    $('status').innerHTML =
      `<span class="dot"></span><span class="pill">${st.scheduler_running?'running':'stopped'}</span>`+
      `<span class="pill">${st.underlyings.join(' · ')}</span>`+
      `<span class="pill">${st.model}</span>`+
      `<span class="pill">${st.paper?'PAPER':'LIVE'}</span>`;
  }
  const eq = await j('/api/equity?limit=500');
  if(eq && eq.length){
    const last=eq[eq.length-1].equity, base=100000, pnl=last-base, pct=pnl/base*100;
    $('equity').textContent = money(last);
    $('pnl').innerHTML = `<span class="${pnl>=0?'up':'down'}">${pnl>=0?'+':''}${money(pnl)} (${pnl>=0?'+':''}${pct.toFixed(2)}%)</span> since $100k start`;
    spark(eq);
  }
  const pos = await j('/api/positions?limit=50') || [];
  const open = pos.filter(p=>p.status==='open').length, closed = pos.filter(p=>p.status==='closed');
  const wins = closed.filter(p=>(p.realized_pnl||0)>0).length;
  $('counts').innerHTML =
    `<div style="display:flex;gap:22px;flex-wrap:wrap">
       <div><div class="big">${open}</div><div class="sub">open spreads</div></div>
       <div><div class="big">${closed.length}</div><div class="sub">closed</div></div>
       <div><div class="big">${closed.length?Math.round(wins/closed.length*100):'—'}${closed.length?'%':''}</div><div class="sub">win rate</div></div>
     </div>`;
  if(pos.length){
    $('positions').innerHTML =
      '<div style="overflow-x:auto"><table><tr><th>Opened</th><th>Underlying</th><th>Spread</th><th>Qty</th><th>Credit</th><th>Max loss</th><th>Status</th><th>P&L</th></tr>'+
      pos.map(p=>{
        const legs = p.short_symbol.replace(/^[A-Z]+/,'').slice(-8)+' / '+p.long_symbol.replace(/^[A-Z]+/,'').slice(-8);
        const pnl = p.realized_pnl;
        const pnlc = pnl==null?'':(pnl>=0?'up':'down');
        return `<tr><td>${p.ts_open.slice(5,16).replace('T',' ')}</td><td>${p.underlying} ${p.option_right}</td>`+
               `<td style="font-family:var(--mono);font-size:.8rem">${legs}</td><td>${p.contracts}</td>`+
               `<td>${fmt(p.credit)}</td><td>${money(p.max_loss)}</td>`+
               `<td><span class="tag ${p.status==='open'?'t-open':'t-closed'}">${p.status}</span></td>`+
               `<td class="${pnlc}">${pnl==null?'—':money(pnl)}</td></tr>`;
      }).join('')+'</table></div>';
  }
  const dec = await j('/api/decisions?limit=30') || [];
  if(dec.length){
    $('decisions').innerHTML = dec.map(d=>
      `<div class="d"><span class="tm">${d.ts.slice(5,16).replace('T',' ')}</span>`+
      `<span class="rs">${dirTag(d.direction)} <b>${d.underlying}</b> — ${d.reason||d.rationale||''}`+
      `${d.approved?` · opened ${d.contracts}x`:''}</span>`+
      `<span class="tm">${d.confidence!=null?'conf '+Number(d.confidence).toFixed(2):''}</span></div>`
    ).join('');
  }
}
refresh(); setInterval(refresh, 15000);
</script>
</body>
</html>
"""
