# optionwright agéntico — percepción + razonamiento real

**Fecha:** 2026-09-01
**Estado:** aprobado, pendiente de plan de implementación
**Contexto:** el LLM hoy recibe solo `{underlying, expiry, dos spreads}` y decide
dirección sin ninguna señal de mercado, así que abstiene con 0.5 casi siempre. Se
comporta como un clasificador hambriento de contexto, no como un agente. Este
diseño enriquece lo que el LLM percibe y cómo razona, sin darle control del riesgo.

## Principio invariante (NO se toca)

El LLM propone `direction + confidence + rationale`. El **código** decide strikes,
tamaño, gates y ejecución. Este cambio enriquece la ENTRADA del LLM y la
profundidad de su razonamiento; jamás le entrega el sizing ni la gestión de riesgo.

Reglas heredadas que se conservan:
- El código calcula todos los números; el LLM nunca recalcula.
- Respuesta malformada / degenerada → ABSTAIN, nunca un trade fabricado.
- Solo paper. Gates deterministas vetan o encogen, nunca agrandan.

## Componentes nuevos

### 1. Percepción de mercado — `agent/perception.py`

Función pura `compute_signals(underlying, bars, short_leg_iv) -> dict`. El código
(no el LLM) calcula, a partir de las barras diarias de Alpaca ya disponibles:

- `pct_5d`, `pct_desde_apertura` — cambio porcentual (números crudos)
- `precio_vs_sma_corta`, `precio_vs_sma_media` — momentum
- `vol_realizada` — desviación estándar de retornos diarios recientes
- `iv_short_leg` — nivel de volatilidad implícita del short leg (de los greeks)

**Anti-alucinación (regla dura del proyecto):** además de los números crudos, el
código resuelve **banderas categóricas** para que el LLM lea categorías y no
compare números:
- `tendencia_5d`: `"alza" | "baja" | "lateral"` (umbrales en código)
- `momentum_positivo`: `bool`
- `regimen`: `"tranquilo" | "volatil"` (según vol_realizada vs umbral)
- `prima`: `"rica" | "normal" | "barata"` (según iv_short_leg)

Umbrales configurables por env, con defaults sensatos. Si las barras no se pueden
leer (Alpaca falla), `compute_signals` devuelve `{}` y el ciclo degrada al
comportamiento actual (nunca rompe el agente).

### 2. Memoria — `store.recent_outcomes(underlying, limit)`

Resumen en código de los últimos N trades cerrados del subyacente desde Postgres:
- por dirección: cuántas ganadas/perdidas, `% capturado` promedio
- racha de abstenciones recientes (de `decisions`)

Salida compacta, p.ej.:
`{"cerradas": 5, "ganadas_bajista": 4, "ganadas_alcista": 0, "captura_prom_pct": 55, "abstenciones_seguidas": 3}`

Da continuidad entre ciclos: "lo bajista viene funcionando en SPY".

### 3. Portafolio — `store.book_summary()`

Ya casi existe en `build_policy_state`. Se agrega un resumen legible para el LLM:
- abiertas por subyacente y por dirección (call=bajista, put=alcista)
- concentración (subyacente con más abiertas)
- P&L realizado del día, pérdidas consecutivas, cupos restantes (global y por símbolo)

El LLM razona sobre concentración: "el libro ya está 100% bajista → abstenerme".

### 4. Prompt y contrato de salida — `agent/analyzer.py`

- **System prompt** reencuadrado: de "módulo de dirección" a "estratega de opciones
  que percibe señales, recuerda resultados recientes y considera el libro actual
  antes de decidir dirección para ESTE subyacente; el riesgo y el tamaño los maneja
  el código downstream".
- **Contexto de usuario** ampliado:
  `{underlying, expiry, signals, bull_put_spread, bear_call_spread, memoria, portafolio}`
- **Salida sin cambios de forma**: `{direction, confidence, rationale}`. El `rationale`
  pasa a ser razonamiento real de 1-2 frases que cita las señales. `_parse_proposal`
  se conserva igual (colapsa a ABSTAIN ante cualquier malformación).
- **Una sola llamada** al LLM (sin ReAct). Menor riesgo, cabe en el deadline.

### 5. Flujo de datos — `agent/loop.py` y `agent/runner.py`

`run_cycle` arma el contexto rico usando tres nuevos callables inyectados vía
`Deps` (siguen siendo testeables con fakes, sin Alpaca/LLM/Postgres reales):
- `signals: Callable[[str, str], dict]` (underlying, expiry) → dict de percepción
- `memory: Callable[[str], dict]` (underlying) → resumen de resultados
- `book: Callable[[], dict]` → resumen de portafolio

`runner._build_deps` los cablea a las implementaciones reales (Alpaca bars, Postgres).
El resto de `run_cycle` —selección de spread, gates, ejecución, registro— **no cambia**.

### 6. Feature flag y degradación

- `AGENT_RICH_CONTEXT` (env, default `true`). En `false`, el contexto vuelve al de
  hoy (solo spreads) → rollback instantáneo sin redeploy de código, solo el env.
- Cualquier fallo en percepción/memoria/portafolio se captura y degrada a `{}`
  parcial; el ciclo nunca aborta por el contexto enriquecido.

## Fuera de alcance (YAGNI)

- ReAct / herramientas que el LLM invoque por sí mismo.
- APIs externas de noticias o sentimiento.
- Decisión única de portafolio por ciclo (se mantiene por-subyacente con contexto
  de libro).
- Aprendizaje/ajuste automático de parámetros.

## Pruebas

- `compute_signals`: dado un set de barras fijas, produce las banderas categóricas
  correctas en cada umbral (alza/baja/lateral, momentum, régimen, prima).
- `recent_outcomes` / `book_summary`: conteos y promedios correctos sobre filas fake.
- `analyzer`: el contexto incluye señales/memoria/portafolio; parseo sigue colapsando
  malformaciones a ABSTAIN.
- `run_cycle` end-to-end con LLM fake y `AGENT_RICH_CONTEXT` on/off: en ambos casos
  la decisión pasa por los gates y el sizing sale del código.
- Degradación: percepción que lanza excepción → ciclo completa con contexto parcial.

## Invariantes de seguridad (checklist de revisión)

- [ ] El LLM nunca recibe control de tamaño ni de ejecución.
- [ ] Todos los números y comparaciones los resuelve el código; el LLM lee categorías.
- [ ] Respuesta malformada → ABSTAIN.
- [ ] Gates deterministas intactos; sizing emerge de los gates.
- [ ] Fallo de contexto degrada, no rompe.
- [ ] Solo paper.
