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
  .grid{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-top:22px}
  .card.eq{grid-column:span 3}
  .card.sess{grid-column:span 1}
  @media(max-width:720px){.grid{grid-template-columns:1fr}
    .card.eq,.card.sess{grid-column:1/-1}}
  .card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:18px 20px}
  .card.wide{grid-column:1/-1}
  /* This session, stacked for the narrow column */
  .sessrow{display:flex;justify-content:space-between;align-items:baseline;
    border-bottom:1px solid var(--line);padding:0 0 10px;margin-bottom:10px}
  .sessrow:last-child{border-bottom:none;margin-bottom:0;padding-bottom:0}
  .sessrow .k{color:var(--ink3);font:500 .68rem/1 var(--mono);letter-spacing:.06em;text-transform:uppercase}
  .sessrow .n{font-size:1.5rem;font-weight:700;font-variant-numeric:tabular-nums}
  .daylab{font:500 10px/1 var(--mono);fill:var(--ink3)}
  .card h2{font-size:.72rem;letter-spacing:.14em;text-transform:uppercase;color:var(--ink3);
    margin:0 0 14px;font-family:var(--mono);font-weight:500}
  .big{font-size:2.1rem;font-weight:700;font-variant-numeric:tabular-nums;letter-spacing:-1px}
  .sub{color:var(--ink2);font-size:.9rem;margin-top:2px}
  .up{color:var(--gain)} .down{color:var(--loss)}
  svg{width:100%;height:150px;display:block;margin-top:10px}
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
  .fbtn{font:500 .72rem/1 var(--mono);color:var(--ink2);background:var(--raise);border:1px solid var(--line);
    border-radius:7px;padding:6px 11px;margin-left:4px;cursor:pointer}
  .fbtn:hover{border-color:var(--acc);color:var(--acc)}
  .fbtn.on{background:var(--acc);border-color:var(--acc);color:#0e1613}
  .foot{margin-top:26px;color:var(--ink3);font-size:.78rem;text-align:center;font-family:var(--mono)}
  a{color:var(--acc)}
  /* Live Decision Stream */
  .streamhead{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px}
  .livechip{font:600 .64rem/1 var(--mono);letter-spacing:.12em;color:var(--acc);border:1px solid var(--line);
    border-radius:6px;padding:5px 8px;background:rgba(61,186,140,.06);display:inline-flex;align-items:center;gap:6px}
  .livechip .dot{width:6px;height:6px;box-shadow:none;animation:hb 1.8s infinite}
  @keyframes hb{0%{box-shadow:0 0 0 2px rgba(61,186,140,.28)}70%{box-shadow:0 0 0 6px rgba(61,186,140,0)}100%{box-shadow:0 0 0 0 rgba(61,186,140,0)}}
  .stream{font-family:var(--mono);font-size:.82rem;margin-top:12px}
  .srow{display:grid;grid-template-columns:70px 54px 1fr auto;gap:12px;align-items:center;
    padding:10px 2px;border-bottom:1px solid var(--line);animation:fade .5s both}
  .srow:last-child{border-bottom:none}
  @keyframes fade{from{opacity:0;transform:translateY(-3px)}to{opacity:1;transform:none}}
  .srow .tm{color:var(--ink3)}
  .srow .sy{font-weight:600;color:var(--acc2)}
  .srow .mg{color:var(--ink2);font-size:.85rem;font-family:-apple-system,"Segoe UI",sans-serif;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .srow .mg b{color:var(--ink);font-weight:600}
  .vd{font:600 .64rem/1 var(--mono);letter-spacing:.05em;padding:4px 8px;border-radius:6px;white-space:nowrap;text-align:right}
  .vd-open{color:var(--acc);background:rgba(61,186,140,.12);border:1px solid var(--line)}
  .vd-close{color:var(--acc2);background:rgba(87,201,158,.12);border:1px solid rgba(87,201,158,.28)}
  .vd-veto{color:var(--warn);background:rgba(217,154,78,.10);border:1px solid rgba(217,154,78,.28)}
  .vd-abs{color:var(--ink3);background:var(--raise);border:1px solid var(--line)}
  .curs{display:inline-block;width:7px;height:12px;background:var(--acc);margin-left:3px;vertical-align:-2px;animation:bl 1s step-end infinite}
  @keyframes bl{50%{opacity:0}}
  .streamfoot{margin-top:12px;padding-top:12px;border-top:1px solid var(--line);display:flex;
    justify-content:space-between;flex-wrap:wrap;gap:8px;color:var(--ink3);font:500 .74rem/1 var(--mono)}
  .badgeNEW{font:600 .58rem/1 var(--mono);letter-spacing:.1em;color:#0e1613;background:var(--acc);
    border-radius:4px;padding:3px 6px;margin-left:8px;vertical-align:middle}
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
    <div class="card eq">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
        <h2 style="margin:0">Account equity</h2>
        <div id="eqranges" style="display:inline-flex;gap:4px">
          <button class="fbtn on" data-r="week">5D</button>
          <button class="fbtn" data-r="30">30D</button>
          <button class="fbtn" data-r="all">All</button>
        </div>
      </div>
      <div class="big" id="equity" style="margin-top:12px">—</div>
      <div class="sub" id="pnl">since $100,000 start</div>
      <svg id="spark" viewBox="0 0 600 134" preserveAspectRatio="none"></svg>
    </div>
    <div class="card sess">
      <h2>Resumen</h2>
      <div id="counts"></div>
      <div class="sub" id="rangepnl" style="margin-top:14px;padding-top:12px;border-top:1px solid var(--line)"></div>
    </div>
    <div class="card wide">
      <h2>Positions</h2>
      <div id="positions"><div class="muted">No positions yet.</div></div>
    </div>
    <div class="card wide">
      <div class="streamhead">
        <h2 style="margin:0">Live Decision Stream<span class="badgeNEW">LIVE</span></h2>
        <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
          <span class="livechip"><span class="dot"></span>LIVE · 120s</span>
          <div id="decfilters" style="display:inline-flex;gap:0;flex-wrap:wrap">
            <button class="fbtn on" data-f="all">All</button>
            <button class="fbtn" data-f="opened">Opened</button>
            <button class="fbtn" data-f="closed">Closed</button>
            <button class="fbtn" data-f="vetoed">Vetoed</button>
            <button class="fbtn" data-f="abstain">Abstain</button>
            <button class="fbtn" data-f="SPY">SPY</button>
            <button class="fbtn" data-f="QQQ">QQQ</button>
            <button class="fbtn" data-f="IWM">IWM</button>
          </div>
        </div>
      </div>
      <div class="stream" id="decisions"><div class="muted">Waiting for the first cycle…</div></div>
      <div class="streamfoot" id="streamfoot"><span></span><span>streaming<span class="curs"></span></span></div>
    </div>
  </div>
  <div class="foot">optionwright · paper trading · auto-refresh 15s · <a href="/metrics">/metrics</a></div>
</div>

<script>
const $ = id => document.getElementById(id);
const fmt = n => (n==null?'—':Number(n).toLocaleString('en-US',{maximumFractionDigits:2}));
const money = n => '$'+fmt(n);
async function j(u){ try{ const r=await fetch(u); return await r.json(); }catch(e){ return null; } }

const MES=['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'];
const _dOf = p => p.date || (p.ts ? p.ts.slice(0,10) : '');  // date string of a point
function _weekKey(iso){  // Monday of that ISO date's week, as YYYY-MM-DD
  const [Y,M,D]=iso.split('-').map(Number);
  const dt=new Date(Date.UTC(Y,M-1,D));
  dt.setUTCDate(dt.getUTCDate()-((dt.getUTCDay()+6)%7));
  return dt.toISOString().slice(0,10);
}
function spark(pts){
  const svg=$('spark'); svg.innerHTML='';
  if(!pts||pts.length<2){ return; }
  const ys=pts.map(p=>p.equity), min=Math.min(...ys), max=Math.max(...ys), rng=(max-min)||1;
  const W=600,Hc=110,pad=6,labelY=128;   // Hc = drawing area; labels sit below at 128
  const x=i=>pad+i*(W-2*pad)/(pts.length-1);
  const y=v=>Hc-pad-((v-min)/rng)*(Hc-2*pad);
  // Adaptive bands: one per DAY when few, per WEEK when many, so 20-30+ days
  // never crowd. Weekends have no equity points, so they never get a band.
  // Band granularity by number of distinct DAYS shown, not by point count
  // (the intraday week view has hundreds of points across only a few days).
  const byWeek = new Set(pts.map(_dOf)).size > 10;
  const keyOf = p => byWeek ? _weekKey(_dOf(p)) : _dOf(p);
  const segs=[]; let start=0;
  for(let i=1;i<=pts.length;i++){
    if(i===pts.length || keyOf(pts[i])!==keyOf(pts[i-1])){ segs.push({key:keyOf(pts[start]),a:start,b:i-1}); start=i; }
  }
  let bands='';
  segs.forEach((s,k)=>{
    const left  = k===0 ? 0 : (x(segs[k-1].b)+x(s.a))/2;
    const right = k===segs.length-1 ? W : (x(s.b)+x(segs[k+1].a))/2;
    if(k%2===0) bands+=`<rect x="${left.toFixed(1)}" y="0" width="${(right-left).toFixed(1)}" height="${Hc}" fill="rgba(61,186,140,.06)"/>`;
    if(k>0) bands+=`<line x1="${left.toFixed(1)}" y1="0" x2="${left.toFixed(1)}" y2="${Hc}" stroke="var(--line)" stroke-dasharray="3 4"/>`;
    if(right-left>=34){
      const p=s.key.split('-'); const lab=`${byWeek?'sem ':''}${+p[2]} ${MES[+p[1]-1]}`;
      bands+=`<text class="daylab" x="${((left+right)/2).toFixed(1)}" y="${labelY}" text-anchor="middle">${lab}</text>`;
    }
  });
  let d='M'+pts.map((p,i)=>x(i).toFixed(1)+','+y(p.equity).toFixed(1)).join(' L');
  const up = ys[ys.length-1] >= ys[0];
  const col = up ? 'var(--gain)' : 'var(--loss)';
  const area = d+` L${x(pts.length-1).toFixed(1)},${Hc-pad} L${x(0).toFixed(1)},${Hc-pad} Z`;
  svg.innerHTML =
    `<defs><linearGradient id="g" x1="0" x2="0" y1="0" y2="1">
       <stop offset="0" stop-color="${col}" stop-opacity="0.28"/>
       <stop offset="1" stop-color="${col}" stop-opacity="0"/></linearGradient></defs>
     ${bands}
     <path d="${area}" fill="url(#g)"/>
     <path d="${d}" fill="none" stroke="${col}" stroke-width="2" stroke-linejoin="round"/>
     <circle cx="${x(pts.length-1).toFixed(1)}" cy="${y(ys[ys.length-1]).toFixed(1)}" r="3.2" fill="${col}"/>`;
}

let _eqIntra = [], _eqDaily = [], _eqRange = 'week';
function drawEquity(){
  // Headline = latest live equity (intraday is freshest; fall back to daily).
  const latest = (_eqIntra.length ? _eqIntra : _eqDaily);
  if(!latest.length) return;
  const last=latest[latest.length-1].equity, pnl=last-100000, pct=pnl/1000;
  $('equity').textContent = money(last);
  $('pnl').innerHTML = `<span class="${pnl>=0?'up':'down'}">${pnl>=0?'+':''}${money(pnl)} (${pnl>=0?'+':''}${pct.toFixed(2)}%)</span> since $100k start`;
  // Week view uses the DENSE intraday curve (keeps the ups/downs detail);
  // 30D / All use one point per day (intraday would be too many points).
  let pts;
  if(_eqRange==='week' && _eqIntra.length){
    const mon=_weekKey(_dOf(_eqIntra[_eqIntra.length-1]));
    pts=_eqIntra.filter(p=>_dOf(p)>=mon);
  } else if(_eqRange==='30'){
    pts=_eqDaily.slice(-30);
  } else {
    pts=_eqDaily;
  }
  spark(pts);
  const lbl = _eqRange==='week' ? 'This week' : _eqRange==='30' ? 'Last 30 days' : 'All time';
  if(pts.length>=2){
    const chg=pts[pts.length-1].equity-pts[0].equity, cpct=chg/pts[0].equity*100;
    $('rangepnl').innerHTML=`${lbl}: <span class="${chg>=0?'up':'down'}">${chg>=0?'+':''}${money(chg)} (${chg>=0?'+':''}${cpct.toFixed(2)}%)</span>`;
  } else {
    $('rangepnl').innerHTML=`${lbl}: <span class="sub">only 1 day of data so far</span>`;
  }
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
  const [intra, daily] = await Promise.all([j('/api/equity?limit=5000'), j('/api/equity/daily?limit=200')]);
  if(intra) _eqIntra = intra;
  if(daily) _eqDaily = daily;
  if(_eqIntra.length || _eqDaily.length) drawEquity();
  const pos = await j('/api/positions?limit=50') || [];
  const open = pos.filter(p=>p.status==='open').length, closed = pos.filter(p=>p.status==='closed');
  _closedPos = closed;
  const wins = closed.filter(p=>(p.realized_pnl||0)>0).length;
  const realized = closed.reduce((s,p)=>s+(p.realized_pnl||0),0);
  const winrate = closed.length?Math.round(wins/closed.length*100)+'%':'—';
  $('counts').innerHTML =
    `<div class="sessrow"><span class="k">Open</span><span class="n">${open}</span></div>
     <div class="sessrow"><span class="k">Closed</span><span class="n">${closed.length}</span></div>
     <div class="sessrow"><span class="k">Win rate</span><span class="n up">${winrate}</span></div>
     <div class="sessrow"><span class="k">Realized P&L</span><span class="n ${realized>=0?'up':'down'}" style="font-size:1.15rem">${realized>=0?'+':''}${money(realized)}</span></div>`;
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
  _decisions = await j('/api/decisions?limit=100') || [];
  renderDecisions();
}

let _decisions = [];
let _closedPos = [];
let _decFilter = 'all';

// Merge cycle decisions (open/veto/abstain) and position exits (close) into one
// time-ordered event list — the single "what the agent did" log.
function buildEvents(){
  const ev = [];
  for(const d of _decisions){
    const kind = d.approved ? 'open' : (d.direction==='abstain' ? 'abstain' : 'veto');
    ev.push({t:d.ts, u:d.underlying, kind, dir:d.direction, conf:d.confidence,
             reason:d.reason||d.rationale||'', contracts:d.contracts});
  }
  for(const p of _closedPos){
    ev.push({t:p.ts_close||p.ts_open, u:p.underlying, kind:'close',
             reason:p.exit_reason||'exit by rule', pnl:p.realized_pnl});
  }
  ev.sort((a,b)=> (a.t<b.t?1:a.t>b.t?-1:0));
  return ev;
}
function matchFilter(e){
  if(_decFilter==='all') return true;
  if(_decFilter==='SPY'||_decFilter==='QQQ'||_decFilter==='IWM') return e.u===_decFilter;
  if(_decFilter==='opened') return e.kind==='open';
  if(_decFilter==='closed') return e.kind==='close';
  if(_decFilter==='vetoed') return e.kind==='veto';
  if(_decFilter==='abstain') return e.kind==='abstain';
  return true;
}
function renderDecisions(){
  const all = buildEvents();
  const rows = all.filter(matchFilter);
  const el = $('decisions');
  const nOpen=all.filter(e=>e.kind==='open').length, nClose=all.filter(e=>e.kind==='close').length,
        nVeto=all.filter(e=>e.kind==='veto').length, nAbs=all.filter(e=>e.kind==='abstain').length;
  $('streamfoot').innerHTML =
    `<span>today: ${nClose} closes · ${nOpen} opens · ${nVeto} vetoes · ${nAbs} abstains</span>`+
    `<span>streaming<span class="curs"></span></span>`;
  if(!rows.length){ el.innerHTML = '<div class="muted">No events match this filter.</div>'; return; }
  el.innerHTML = rows.map(e=>{
    const tm = (e.t||'').slice(11,19);
    const syCol = e.dir==='bearish' ? 'var(--loss)' : (e.dir==='bullish' ? 'var(--acc)' : 'var(--acc2)');
    const conf = e.conf!=null ? Number(e.conf).toFixed(2) : '';
    let msg='', vd='';
    if(e.kind==='open'){
      msg = `<b>${e.dir}</b> ${conf} · opened ${e.contracts}× spread`;
      vd = `<span class="vd vd-open">OPEN ${e.contracts}×</span>`;
    } else if(e.kind==='close'){
      const pnl=e.pnl; const c=pnl==null?'':(pnl>=0?'up':'down');
      msg = `closed · <b>${e.reason}</b>`;
      vd = `<span class="vd vd-close">CLOSE${pnl==null?'':` <span class="${c}">${pnl>=0?'+':''}${money(pnl)}</span>`}</span>`;
    } else if(e.kind==='veto'){
      msg = `<b>${e.dir}</b> ${conf} · ${e.reason}`;
      vd = `<span class="vd vd-veto">VETO</span>`;
    } else {
      msg = e.reason || 'no directional edge';
      vd = `<span class="vd vd-abs">ABSTAIN</span>`;
    }
    return `<div class="srow"><span class="tm">${tm}</span>`+
           `<span class="sy" style="color:${syCol}">${e.u}</span>`+
           `<span class="mg">${msg}</span>${vd}</div>`;
  }).join('');
}
document.getElementById('decfilters').addEventListener('click', e=>{
  const b = e.target.closest('.fbtn'); if(!b) return;
  _decFilter = b.dataset.f;
  document.querySelectorAll('#decfilters .fbtn').forEach(x=>x.classList.toggle('on', x===b));
  renderDecisions();
});
document.getElementById('eqranges').addEventListener('click', e=>{
  const b = e.target.closest('.fbtn'); if(!b) return;
  _eqRange = b.dataset.r;
  document.querySelectorAll('#eqranges .fbtn').forEach(x=>x.classList.toggle('on', x===b));
  drawEquity();
});
refresh(); setInterval(refresh, 15000);
</script>
</body>
</html>
"""
