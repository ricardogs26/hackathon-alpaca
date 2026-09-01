# optionwright agéntico (percepción + razonamiento real) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Alimentar al LLM de optionwright con percepción de mercado, memoria de trades recientes y contexto de portafolio para que decida dirección con base real, sin darle control del riesgo/ejecución.

**Architecture:** Tres funciones puras nuevas (percepción, memoria, portafolio) construyen un contexto enriquecido que se inyecta al LLM vía `Deps`. El código sigue resolviendo todos los números y comparaciones (banderas categóricas anti-alucinación); los gates deterministas siguen decidiendo tamaño y ejecución. Todo detrás de un feature flag con degradación a `{}` si algo falla.

**Tech Stack:** Python 3.12, alpaca-py (StockHistoricalDataClient), psycopg3, pytest, httpx/OpenAI client.

**Spec:** `docs/superpowers/specs/2026-09-01-agentic-perception-design.md`

## Global Constraints

- El LLM nunca recibe control de tamaño ni de ejecución; propone `{direction, confidence, rationale}`.
- El código resuelve todos los números y comparaciones; el LLM lee **categorías**, no compara números.
- Respuesta malformada → ABSTAIN (parseo `_parse_proposal` intacto).
- Gates deterministas intactos; sizing emerge de los gates (`_SIZE_CEILING = 100`).
- Todo el contexto enriquecido es **aditivo** y degrada a `{}` ante cualquier fallo; nunca aborta el ciclo.
- Feature flag `AGENT_RICH_CONTEXT` (default `true`) apaga todo por env sin redeploy de código.
- Solo paper. Cero dependencias nuevas (todo con alpaca-py + Postgres ya presentes).
- Direcciones: `option_right == "call"` = bajista (bear call); `"put"` = alcista (bull put).
- Commits locales como `Ricardo <ricardogs26@gmail.com>` (Ricardo hace push/merge).
- **Diferido del spec:** la señal `iv_short_leg`/`prima` se omite (requiere tocar `OptionQuote`, que no trae IV) para mantener el cambio acotado a 2 días del juicio.

---

### Task 1: Módulo de percepción (`perception.py`)

**Files:**
- Create: `optionwright/agent/perception.py`
- Test: `tests/test_perception.py`

**Interfaces:**
- Produces: `compute_signals(closes: list[float], spot: float, *, trend_flat_pct: float = 1.0, vol_high_pct: float = 1.2, sma_short: int = 5, sma_medium: int = 20) -> dict`. `closes` son cierres diarios cronológicos (viejo→nuevo). Devuelve `{}` si hay datos insuficientes. Con datos suficientes devuelve keys: `pct_5d` (float), `sma_corta` (float), `sma_media` (float), `vol_realizada_pct` (float), `tendencia_5d` ("alza"|"baja"|"lateral"), `momentum_positivo` (bool), `regimen` ("tranquilo"|"volatil").

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_perception.py
from __future__ import annotations

from optionwright.agent.perception import compute_signals


def _rising(n=25, start=100.0, step=1.0):
    return [start + i * step for i in range(n)]


def test_uptrend_flags_alza_and_positive_momentum():
    closes = _rising()
    s = compute_signals(closes, spot=closes[-1] + 1)
    assert s["tendencia_5d"] == "alza"
    assert s["momentum_positivo"] is True
    assert s["pct_5d"] > 0


def test_downtrend_flags_baja():
    closes = list(reversed(_rising()))
    s = compute_signals(closes, spot=closes[-1] - 1)
    assert s["tendencia_5d"] == "baja"
    assert s["momentum_positivo"] is False


def test_flat_market_is_lateral():
    closes = [100.0] * 25
    s = compute_signals(closes, spot=100.2)  # +0.2% < 1.0% umbral
    assert s["tendencia_5d"] == "lateral"


def test_insufficient_bars_returns_empty():
    assert compute_signals([100.0, 101.0], spot=101.0) == {}
    assert compute_signals([], spot=100.0) == {}


def test_high_variance_is_volatile():
    closes = [100.0, 110.0, 95.0, 115.0, 90.0, 120.0, 88.0]
    s = compute_signals(closes, spot=100.0, vol_high_pct=1.2)
    assert s["regimen"] == "volatil"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_perception.py -v`
Expected: FAIL con `ModuleNotFoundError: optionwright.agent.perception`

- [ ] **Step 3: Implement `compute_signals`**

```python
# optionwright/agent/perception.py
"""
Percepción de mercado: el CÓDIGO (no el LLM) resume la acción del precio en
señales numéricas + banderas categóricas. El LLM lee categorías ("alza"/"baja")
para no caer en comparaciones numéricas alucinadas. Función pura y testeable.
"""
from __future__ import annotations

import statistics


def compute_signals(
    closes: list[float],
    spot: float,
    *,
    trend_flat_pct: float = 1.0,
    vol_high_pct: float = 1.2,
    sma_short: int = 5,
    sma_medium: int = 20,
) -> dict:
    """Resume cierres diarios (viejo→nuevo) + spot en señales. {} si faltan datos."""
    if not closes or len(closes) < sma_short + 1 or spot <= 0:
        return {}

    ref = closes[-sma_short]
    pct_5d = (spot - ref) / ref * 100 if ref else 0.0
    sma_c = sum(closes[-sma_short:]) / sma_short
    med_n = min(sma_medium, len(closes))
    sma_m = sum(closes[-med_n:]) / med_n
    rets = [
        (closes[i] - closes[i - 1]) / closes[i - 1]
        for i in range(1, len(closes))
        if closes[i - 1]
    ]
    vol = statistics.pstdev(rets) * 100 if len(rets) >= 2 else 0.0

    if pct_5d >= trend_flat_pct:
        tendencia = "alza"
    elif pct_5d <= -trend_flat_pct:
        tendencia = "baja"
    else:
        tendencia = "lateral"

    return {
        "pct_5d": round(pct_5d, 2),
        "sma_corta": round(sma_c, 2),
        "sma_media": round(sma_m, 2),
        "vol_realizada_pct": round(vol, 2),
        "tendencia_5d": tendencia,
        "momentum_positivo": spot > sma_c,
        "regimen": "volatil" if vol > vol_high_pct else "tranquilo",
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_perception.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add optionwright/agent/perception.py tests/test_perception.py
git -c user.name="Ricardo" -c user.email="ricardogs26@gmail.com" commit -m "feat(agent): perception layer — market signals as categorical flags"
```

---

### Task 2: Resúmenes de memoria y portafolio (`store.py`)

**Files:**
- Modify: `optionwright/storage/store.py`
- Test: `tests/test_store_logic.py`

**Interfaces:**
- Produces (puras, testeables): `_summarize_outcomes(rows: list[dict]) -> dict` con keys `cerradas`, `ganadas_bajista`, `ganadas_alcista`, `perdidas`; y `_summarize_book(open_rows: list[dict], pnl_dia: float, consec_losses: int) -> dict` con keys `abiertas`, `por_subyacente` (dict), `por_direccion` (dict), `concentracion` (str|None), `pnl_dia` (float), `perdidas_consecutivas` (int).
- Produces (wrappers DB): `recent_outcomes(underlying: str, limit: int = 5) -> dict`, `book_summary() -> dict`.
- Consumes: filas con keys `underlying`, `option_right`, `realized_pnl`.

- [ ] **Step 1: Write the failing tests**

```python
# añadir a tests/test_store_logic.py
from optionwright.storage.store import _summarize_outcomes, _summarize_book


def test_summarize_outcomes_counts_by_direction():
    rows = [
        {"underlying": "SPY", "option_right": "call", "realized_pnl": 200.0},  # bajista win
        {"underlying": "SPY", "option_right": "call", "realized_pnl": 150.0},  # bajista win
        {"underlying": "SPY", "option_right": "put", "realized_pnl": -80.0},   # alcista loss
        {"underlying": "SPY", "option_right": "call", "realized_pnl": None},   # sin cerrar -> ignora
    ]
    s = _summarize_outcomes(rows)
    assert s["cerradas"] == 3
    assert s["ganadas_bajista"] == 2
    assert s["ganadas_alcista"] == 0
    assert s["perdidas"] == 1


def test_summarize_book_groups_and_flags_concentration():
    rows = [
        {"underlying": "SPY", "option_right": "call"},
        {"underlying": "SPY", "option_right": "call"},
        {"underlying": "QQQ", "option_right": "put"},
    ]
    b = _summarize_book(rows, pnl_dia=1098.5, consec_losses=0)
    assert b["abiertas"] == 3
    assert b["por_subyacente"]["SPY"] == 2
    assert b["por_direccion"]["bajista"] == 2
    assert b["por_direccion"]["alcista"] == 1
    assert b["concentracion"] == "SPY"
    assert b["pnl_dia"] == 1098.5


def test_summarize_book_empty():
    b = _summarize_book([], pnl_dia=0.0, consec_losses=0)
    assert b["abiertas"] == 0
    assert b["concentracion"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_store_logic.py -k summarize -v`
Expected: FAIL con `ImportError: cannot import name '_summarize_outcomes'`

- [ ] **Step 3: Implement the pure helpers and DB wrappers**

Añadir a `optionwright/storage/store.py` (tras `_consecutive_losses`):

```python
def _summarize_outcomes(rows: list[dict]) -> dict:
    """Resumen de trades cerrados por dirección (call=bajista, put=alcista)."""
    closed = [r for r in rows if r.get("realized_pnl") is not None]

    def won(r):
        return r["realized_pnl"] > 0

    return {
        "cerradas": len(closed),
        "ganadas_bajista": sum(1 for r in closed if r["option_right"] == "call" and won(r)),
        "ganadas_alcista": sum(1 for r in closed if r["option_right"] == "put" and won(r)),
        "perdidas": sum(1 for r in closed if not won(r)),
    }


def _summarize_book(open_rows: list[dict], pnl_dia: float, consec_losses: int) -> dict:
    """Resumen legible del libro abierto para el contexto del LLM."""
    from collections import Counter

    by_u = Counter(r["underlying"] for r in open_rows)
    by_dir = Counter(
        "bajista" if r["option_right"] == "call" else "alcista" for r in open_rows
    )
    conc = by_u.most_common(1)[0][0] if by_u else None
    return {
        "abiertas": len(open_rows),
        "por_subyacente": dict(by_u),
        "por_direccion": dict(by_dir),
        "concentracion": conc,
        "pnl_dia": round(pnl_dia, 2),
        "perdidas_consecutivas": consec_losses,
    }


def recent_outcomes(underlying: str, limit: int = 5) -> dict:
    """Resumen de los últimos `limit` trades cerrados del subyacente."""
    with _conn() as c:
        cur = c.execute(
            "SELECT underlying, option_right, realized_pnl FROM positions"
            " WHERE status='closed' AND underlying=%s ORDER BY ts_close DESC LIMIT %s",
            (underlying, limit),
        )
        rows = _rows(cur)
    return _summarize_outcomes(rows)


def book_summary() -> dict:
    """Resumen del libro abierto + P&L del día + racha de pérdidas."""
    with _conn() as c:
        open_cur = c.execute(
            "SELECT underlying, option_right FROM positions WHERE status='open'"
        )
        open_rows = _rows(open_cur)
        pnl_dia = c.execute(
            "SELECT coalesce(sum(realized_pnl),0) FROM positions"
            " WHERE status='closed' AND ts_close::date = now()::date"
        ).fetchone()[0]
        pnls = c.execute(
            "SELECT realized_pnl FROM positions WHERE status='closed'"
            " ORDER BY ts_close DESC LIMIT 20"
        ).fetchall()
    consec = _consecutive_losses([p[0] for p in pnls])
    return _summarize_book(open_rows, float(pnl_dia), consec)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_store_logic.py -k summarize -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add optionwright/storage/store.py tests/test_store_logic.py
git -c user.name="Ricardo" -c user.email="ricardogs26@gmail.com" commit -m "feat(store): recent_outcomes + book_summary for agent memory/portfolio context"
```

---

### Task 3: Enriquecer el contexto del ciclo (`loop.py`)

**Files:**
- Modify: `optionwright/agent/loop.py`
- Test: `tests/test_loop.py`

**Interfaces:**
- Consumes: `compute_signals` (Task 1), `recent_outcomes`/`book_summary` (Task 2) — inyectados como callables.
- Produces: `Deps` gana campos `signals: Callable[[str, str], dict] = None`, `memory: Callable[[str], dict] = None`, `book: Callable[[], dict] = None`, `rich_context: bool = False`. `run_cycle` agrega al context las keys `signals`, `memoria`, `portafolio` cuando `rich_context` está activo, cada una degradando a `{}` si su callable lanza.

- [ ] **Step 1: Write the failing tests**

```python
# añadir a tests/test_loop.py
def test_rich_context_injects_signals_memory_book():
    rec = _Recorder()
    captured = {}
    deps = rec.deps(Proposal(Direction.ABSTAIN, 0.5, "x"))
    deps.propose = lambda ctx: captured.update(ctx) or Proposal(Direction.ABSTAIN, 0.5, "x")
    deps.signals = lambda u, e: {"tendencia_5d": "baja"}
    deps.memory = lambda u: {"cerradas": 3}
    deps.book = lambda: {"abiertas": 2, "concentracion": "SPY"}
    deps.rich_context = True
    run_cycle("SPY", deps)
    assert captured["signals"] == {"tendencia_5d": "baja"}
    assert captured["memoria"] == {"cerradas": 3}
    assert captured["portafolio"]["concentracion"] == "SPY"


def test_rich_context_off_adds_no_keys():
    rec = _Recorder()
    captured = {}
    deps = rec.deps(Proposal(Direction.ABSTAIN, 0.5, "x"))
    deps.propose = lambda ctx: captured.update(ctx) or Proposal(Direction.ABSTAIN, 0.5, "x")
    run_cycle("SPY", deps)
    assert "signals" not in captured


def test_rich_context_degrades_on_error():
    rec = _Recorder()
    captured = {}
    deps = rec.deps(Proposal(Direction.ABSTAIN, 0.5, "x"))
    deps.propose = lambda ctx: captured.update(ctx) or Proposal(Direction.ABSTAIN, 0.5, "x")
    def boom(*a):
        raise RuntimeError("alpaca down")
    deps.signals = boom
    deps.memory = lambda u: {"cerradas": 0}
    deps.book = lambda: {"abiertas": 0}
    deps.rich_context = True
    run_cycle("SPY", deps)
    assert captured["signals"] == {}          # degradó, no rompió
    assert captured["memoria"] == {"cerradas": 0}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_loop.py -k rich_context -v`
Expected: FAIL (`Deps` sin campo `signals`, o keys ausentes)

- [ ] **Step 3: Add Deps fields + a `_safe` helper + enrichment in `run_cycle`**

En `optionwright/agent/loop.py`, añadir a la dataclass `Deps` (tras `rules`):

```python
    signals: Callable[[str, str], dict] = None       # (underlying, expiry) -> señales
    memory: Callable[[str], dict] = None             # (underlying) -> resultados recientes
    book: Callable[[], dict] = None                  # -> resumen de portafolio
    rich_context: bool = False
```

Añadir helper a nivel de módulo (tras `_SIZE_CEILING`):

```python
def _safe(fn) -> dict:
    """Ejecuta un proveedor de contexto; degrada a {} ante cualquier fallo."""
    try:
        out = fn()
        return out if isinstance(out, dict) else {}
    except Exception as exc:  # el contexto enriquecido nunca rompe el ciclo
        logger.warning("rich context provider failed: %s", exc)
        return {}
```

En `run_cycle`, justo después de construir `context = {...}` y ANTES de `proposal = deps.propose(context)`:

```python
    if deps.rich_context and deps.signals and deps.memory and deps.book:
        context["signals"] = _safe(lambda: deps.signals(underlying, expiry))
        context["memoria"] = _safe(lambda: deps.memory(underlying))
        context["portafolio"] = _safe(deps.book)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_loop.py -v`
Expected: PASS (incluye los 3 nuevos y los existentes sin romper — los campos nuevos tienen default)

- [ ] **Step 5: Commit**

```bash
git add optionwright/agent/loop.py tests/test_loop.py
git -c user.name="Ricardo" -c user.email="ricardogs26@gmail.com" commit -m "feat(loop): inject rich context (signals/memory/book) behind flag, degrade-safe"
```

---

### Task 4: Prompt agéntico, settings, barras y cableado real

**Files:**
- Modify: `optionwright/agent/analyzer.py` (system prompt)
- Modify: `optionwright/settings.py` (flag + umbrales)
- Modify: `optionwright/broker/alpaca.py` (`recent_bars`)
- Modify: `optionwright/agent/runner.py` (cablear proveedores reales)
- Modify: `k8s/02-deployment.yaml` (env `AGENT_RICH_CONTEXT`)
- Test: `tests/test_settings_rich.py`

**Interfaces:**
- Consumes: `perception.compute_signals`, `store.recent_outcomes`, `store.book_summary`, `alpaca.recent_bars`, `alpaca.get_spot`.
- Produces: `alpaca.recent_bars(underlying: str, days: int = 30) -> list[float]` (cierres diarios cronológicos); `settings.agent_rich_context: bool`, `settings.perception_trend_flat_pct: float`, `settings.perception_vol_high_pct: float`; `Deps` de `runner._build_deps` con `signals`/`memory`/`book`/`rich_context` cableados.

- [ ] **Step 1: Write the failing test (settings)**

```python
# tests/test_settings_rich.py
from __future__ import annotations

from optionwright.settings import Settings


def test_rich_context_defaults_on():
    s = Settings(_env_file=None)
    assert s.agent_rich_context is True
    assert s.perception_trend_flat_pct == 1.0
    assert s.perception_vol_high_pct == 1.2


def test_rich_context_off_via_env(monkeypatch):
    monkeypatch.setenv("AGENT_RICH_CONTEXT", "false")
    s = Settings(_env_file=None)
    assert s.agent_rich_context is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_settings_rich.py -v`
Expected: FAIL (`AttributeError: agent_rich_context`)

- [ ] **Step 3a: Add settings fields**

En `optionwright/settings.py`, tras `log_level`:

```python
    # Contexto agéntico: percepción + memoria + portafolio inyectados al LLM
    agent_rich_context: bool = Field(default=True, alias="AGENT_RICH_CONTEXT")
    perception_trend_flat_pct: float = Field(default=1.0, alias="PERCEPTION_TREND_FLAT_PCT")
    perception_vol_high_pct: float = Field(default=1.2, alias="PERCEPTION_VOL_HIGH_PCT")
```

- [ ] **Step 3b: Add `recent_bars` to the broker**

En `optionwright/broker/alpaca.py`, añadir:

```python
def recent_bars(underlying: str, days: int = 30) -> list[float]:
    """Cierres diarios cronológicos (viejo→nuevo) de las últimas ~`days` sesiones."""
    from datetime import datetime, timedelta, timezone

    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    start = datetime.now(timezone.utc) - timedelta(days=days * 2)  # holgura fines de semana
    req = StockBarsRequest(symbol_or_symbols=underlying, timeframe=TimeFrame.Day, start=start)
    resp = _stock_data_client().get_stock_bars(req)
    bars = resp.data.get(underlying, []) if hasattr(resp, "data") else []
    return [float(b.close) for b in bars]
```

- [ ] **Step 3c: Reframe the analyzer system prompt**

En `optionwright/agent/analyzer.py`, reemplazar `_SYSTEM` por:

```python
_SYSTEM = (
    "Eres un estratega de opciones. Recibes: señales de mercado ya calculadas en "
    "código (tendencia, momentum, régimen), tu memoria de trades recientes en este "
    "subyacente, un resumen del libro abierto (concentración, dirección), y dos "
    "spreads de crédito de riesgo definido ya construidos (strikes, crédito, max "
    "loss — NO los recalcules). Razona con las señales y el contexto para decidir "
    "si el próximo movimiento favorece el bull put (alcista), el bear call "
    "(bajista), o ninguno (abstain). Considera la concentración del libro: evita "
    "cargar más el mismo lado. El riesgo y el tamaño los maneja el código; tu único "
    'trabajo es la dirección. Responde SOLO como JSON: {"direction":"bullish|'
    'bearish|abstain","confidence":0.0-1.0,"rationale":"una frase corta citando las '
    'señales"}. Abstente cuando no haya ventaja clara.'
)
```

- [ ] **Step 3d: Wire real providers in the runner**

En `optionwright/agent/runner.py`, en `_build_deps`, añadir el import arriba (`from optionwright.agent import perception`) y estos campos al `Deps(...)`:

```python
        signals=lambda u, e: perception.compute_signals(
            alpaca.recent_bars(u), alpaca.get_spot(u),
            trend_flat_pct=s.perception_trend_flat_pct,
            vol_high_pct=s.perception_vol_high_pct,
        ),
        memory=lambda u: store.recent_outcomes(u),
        book=store.book_summary,
        rich_context=s.agent_rich_context,
```

- [ ] **Step 3e: Add the env var to the manifest**

En `k8s/02-deployment.yaml`, junto a las otras env vars de estrategia:

```yaml
            - name: AGENT_RICH_CONTEXT
              value: "true"
```

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS (todo verde, incluidos los tests nuevos y los existentes)

- [ ] **Step 5: Commit**

```bash
git add optionwright/agent/analyzer.py optionwright/settings.py optionwright/broker/alpaca.py optionwright/agent/runner.py k8s/02-deployment.yaml tests/test_settings_rich.py
git -c user.name="Ricardo" -c user.email="ricardogs26@gmail.com" commit -m "feat(agent): agentic prompt + wire perception/memory/book into live runner"
```

---

### Task 5: Build, push y despliegue

**Files:**
- Modify: `k8s/02-deployment.yaml` (bump de imagen)

- [ ] **Step 1: Build & push `0.1.19`**

```bash
cd ~/optionwright
docker build -t registry.richardx.dev/optionwright:0.1.19 .
docker push registry.richardx.dev/optionwright:0.1.19
```

- [ ] **Step 2: Bump image en `k8s/02-deployment.yaml`**

`registry.richardx.dev/optionwright:0.1.18` → `registry.richardx.dev/optionwright:0.1.19`

- [ ] **Step 3: Apply y rollout**

```bash
kubectl apply -f k8s/02-deployment.yaml -n hackathon
kubectl rollout status deployment/optionwright -n hackathon --timeout=120s
```

- [ ] **Step 4: Verificar en vivo**

```bash
POD=$(kubectl get pod -n hackathon -l app=optionwright -o jsonpath='{.items[0].metadata.name}')
# el rationale del stream debe ahora citar señales (tendencia/momentum/libro)
kubectl exec -n hackathon $POD -- python3 -c "import urllib.request,json; d=json.load(urllib.request.urlopen('http://localhost:8080/api/decisions?limit=5',timeout=8)); [print(x['underlying'],x['direction'],x.get('rationale','')[:90]) for x in d]"
```
Expected: los `rationale` mencionan tendencia/momentum/concentración, no "no clear edge" genérico.

- [ ] **Step 5: Commit del bump**

```bash
git add k8s/02-deployment.yaml
git -c user.name="Ricardo" -c user.email="ricardogs26@gmail.com" commit -m "k8s: bump 0.1.19 (contexto agéntico)"
```

---

## Self-Review

**Spec coverage:**
- Percepción → Task 1 (IV/prima diferida, documentado en Global Constraints). ✅
- Memoria → Task 2 (`recent_outcomes`). ✅
- Portafolio → Task 2 (`book_summary`). ✅
- Prompt/salida → Task 4 (Step 3c), parseo intacto. ✅
- Flujo de datos (Deps) → Task 3 + Task 4 (Step 3d). ✅
- Feature flag + degradación → Task 3 (`_safe`) + Task 4 (settings). ✅
- Pruebas → Tasks 1-4. ✅
- Invariantes de seguridad → Global Constraints + gates intactos (no se tocan). ✅

**Type consistency:** `compute_signals`, `recent_outcomes`, `book_summary`, `recent_bars`, campos de `Deps` y de `Settings` coinciden entre las tareas que los definen y las que los consumen.

**Placeholder scan:** sin TBD/TODO; cada step de código trae el código real.
