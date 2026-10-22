// The whole page. It computes NO statistics: every number comes from the API's JSON.
// Parts: helpers, charts (inline SVG), detail panels, then App and the mount at the bottom.
import { useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'

// ---------- helpers (formatting and log handling only) ----------
const ICON = { OK: '✔', WATCH: '◉', MAINTENANCE: '⚙', URGENT: '▲', CRITICAL: '⛔', 'TASKS COMPLETE': '✓' }
const COLS = ['equipment_id', 'service_date', 'service_type', 'technician', 'notes']
const LOG_KEY = 'bbm-local-log:' // + scenario key: one local log per scenario
const SC_KEY = 'bbm-scenario', SEED_KEY = 'bbm-seed'
const BLUE = '#2a78d6', RED = '#d03b3b', AMBER = '#fab219', MUTED = '#898781', GRID = '#e1e0d9', AXIS = '#c3c2b7', INK2 = '#52514e'
const CHECKLIST = {
  fridge: ['coil cleaning', 'probe calibration', 'full service'],
  generator: ['generator service', 'weekly start', 'monthly load'], // must match the service types in app/rules.py
}

const num = (x, d = 2) => (x == null ? 'n/a' : Number(x).toFixed(d))
const when = (s) => (s || '').replace('T', ' ').slice(0, 16)

function nowIso() { // local time, no "Z": the API reads it as the same clock as the data
  const d = new Date(), p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

function toCsv(rows) {
  const q = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`
  return [COLS.join(','), ...rows.map((r) => COLS.map((c) => q(r[c])).join(','))].join('\n') + '\n'
}

function parseCsv(text) { // small CSV reader: quotes, doubled quotes, commas and newlines inside quotes
  const rows = []
  let row = [], cell = '', inQ = false
  for (let i = 0; i < text.length; i++) {
    const c = text[i]
    if (inQ) {
      if (c === '"' && text[i + 1] === '"') { cell += '"'; i++ }
      else if (c === '"') inQ = false
      else cell += c
    } else if (c === '"') inQ = true
    else if (c === ',') { row.push(cell); cell = '' }
    else if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i++
      row.push(cell); cell = ''
      if (row.some((v) => v !== '')) rows.push(row)
      row = []
    } else cell += c
  }
  row.push(cell)
  if (row.some((v) => v !== '')) rows.push(row)
  const head = (rows.shift() || []).map((h) => h.trim())
  if (!COLS.every((c) => head.includes(c))) throw new Error('CSV needs columns: ' + COLS.join(', '))
  return rows.map((r) => Object.fromEntries(COLS.map((c) => [c, (r[head.indexOf(c)] ?? '').trim()])))
}

async function call(url, options) {
  const res = await fetch(url, options)
  if (!res.ok) throw new Error(`${url}: ${res.status} ${await res.text()}`)
  return res.json()
}

function loadSaved(key) {
  try { return JSON.parse(localStorage.getItem(LOG_KEY + key)) || [] } catch { return [] }
}
function store(k, v) { try { localStorage.setItem(k, v) } catch { /* storage may be blocked: fine */ } }
function recall(k) { try { return localStorage.getItem(k) } catch { return null } }

const newSeed = () => Math.floor(Math.random() * 1000000)
// the query string every data request carries: which scenario (and seed, for the random one)
const qs = (sc) => 'scenario=' + sc.key + (sc.key === 'random' ? '&seed=' + sc.seed : '')

// ---------- chart pieces ----------
function Axis({ W, H, m, y0, y1, fmt }) {
  const sy = (v) => H - m.b - ((v - y0) / (y1 - y0)) * (H - m.t - m.b)
  const ticks = [0, 1, 2, 3, 4].map((i) => y0 + ((y1 - y0) * i) / 4)
  return (
    <g fontSize="11" fill={MUTED}>
      {ticks.map((v) => (
        <g key={v}>
          <line x1={m.l} x2={W - m.r} y1={sy(v)} y2={sy(v)} stroke={GRID} />
          <text x={m.l - 4} y={sy(v) + 4} textAnchor="end">{fmt(v)}</text>
        </g>
      ))}
      <line x1={m.l} x2={W - m.r} y1={H - m.b} y2={H - m.b} stroke={AXIS} />
    </g>
  )
}

function TableToggle({ head, rows }) {
  const [on, setOn] = useState(false)
  return (
    <div>
      <button onClick={() => setOn(!on)}>{on ? 'Hide table' : 'Show as table'}</button>
      {on && (
        <div className="scroll">
          <table>
            <thead><tr>{head.map((h) => <th key={h}>{h}</th>)}</tr></thead>
            <tbody>{rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>)}</tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function FridgeChart({ f }) {
  const [hov, setHov] = useState(null)
  const c = f.imr, v = c.values || [], n = v.length
  if (c.message) return <p className="note">{c.message}</p>
  if (!n) return <p className="note">No hourly means to plot.</p>
  const W = 640, H = 270, m = { l: 44, r: 84, t: 12, b: 30 }
  const lo = Math.min(...v, c.lcl), hi = Math.max(...v, c.ucl), pad = (hi - lo) * 0.08
  const y0 = lo - pad, y1 = hi + pad
  const sx = (i) => m.l + (n === 1 ? 0 : (i / (n - 1)) * (W - m.l - m.r))
  const sy = (val) => H - m.b - ((val - y0) / (y1 - y0)) * (H - m.t - m.b)
  const kind = (i) => (c.beyond_limits[i] ? 'beyond limits' : c.run_flags[i] ? 'run of 8 on one side' : 'in control')
  const band0 = Math.max(1, y0), band1 = Math.min(6, y1)
  const line = (val, label, dash) => (
    <g key={label}>
      <line x1={m.l} x2={W - m.r} y1={sy(val)} y2={sy(val)} stroke={INK2} strokeDasharray={dash} />
      <text x={W - m.r + 4} y={sy(val) + 4} fontSize="11" fill={INK2}>{label} {num(val, 2)}</text>
    </g>
  )
  return (
    <div>
      <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Individuals chart of hourly mean temperature">
        <Axis W={W} H={H} m={m} y0={y0} y1={y1} fmt={(x) => x.toFixed(2)} />
        {band1 > band0 && <rect x={m.l} y={sy(band1)} width={W - m.l - m.r} height={sy(band0) - sy(band1)} fill="rgba(12,163,12,0.08)" />}
        {line(c.ucl, 'UCL', '6 4')}{line(c.center, 'center', '')}{line(c.lcl, 'LCL', '6 4')}
        <text x={m.l + 4} y={m.t + 10} fontSize="11" fill={MUTED}>shaded: spec 1-6 °C</text>
        <polyline fill="none" stroke={BLUE} strokeWidth="2" points={v.map((val, i) => `${sx(i)},${sy(val)}`).join(' ')} />
        {v.map((val, i) => !c.flags[i] ? <circle key={i} cx={sx(i)} cy={sy(val)} r="2" fill={BLUE} /> : null)}
        {v.map((val, i) => c.beyond_limits[i]
          ? <circle key={'b' + i} cx={sx(i)} cy={sy(val)} r="5" fill={RED} />
          : c.run_flags[i] ? <rect key={'r' + i} x={sx(i) - 4.5} y={sy(val) - 4.5} width="9" height="9" fill={AMBER} transform={`rotate(45 ${sx(i)} ${sy(val)})`} /> : null)}
        {v.map((val, i) => (
          <circle key={'h' + i} cx={sx(i)} cy={sy(val)} r="9" fill="transparent" onMouseEnter={() => setHov(i)} onClick={() => setHov(i)}>
            <title>{`${when(c.hours[i])}: ${num(val, 2)} °C, ${kind(i)}`}</title>
          </circle>
        ))}
        <text x={m.l} y={H - 8} fontSize="11" fill={MUTED}>{when(c.hours[0])}</text>
        <text x={W - m.r} y={H - 8} fontSize="11" fill={MUTED} textAnchor="end">{when(c.hours[n - 1])}</text>
        <text x={(m.l + W - m.r) / 2} y={H - 8} fontSize="11" fill={MUTED} textAnchor="middle">hour (x) vs hourly mean °C (y)</text>
      </svg>
      <div className="legend">
        <span><svg width="14" height="14"><circle cx="7" cy="7" r="5" fill={RED} /></svg> beyond limits</span>
        <span><svg width="14" height="14"><rect x="3" y="3" width="8" height="8" fill={AMBER} transform="rotate(45 7 7)" /></svg> run of 8 (rule)</span>
        <span><svg width="14" height="14"><circle cx="7" cy="7" r="3" fill={BLUE} /></svg> in control</span>
      </div>
      <div className="hover">{hov == null ? 'Hover or tap a point for its hour, value and flag.' : `${when(c.hours[hov])}: ${num(v[hov], 3)} °C, ${kind(hov)}`}</div>
      <TableToggle head={['hour', 'mean °C', 'flag']} rows={v.map((val, i) => [when(c.hours[i]), num(val, 3), kind(i)])} />
    </div>
  )
}

function PChart({ g }) {
  const [hov, setHov] = useState(null)
  const p = g.p_chart, n = p.months.length
  const W = 640, H = 240, m = { l: 44, r: 12, t: 12, b: 30 }
  const top = Math.max(0.1, ...p.p, ...(p.ucl || [0])) * 1.15
  const slot = (W - m.l - m.r) / n
  const sy = (val) => H - m.b - (val / top) * (H - m.t - m.b)
  const kind = (i) => (!p.judged[i] ? p.notes[i] : p.flags[i] ? 'signal (flagged)' : 'in control')
  const color = (i) => (!p.judged[i] ? AXIS : p.flags[i] ? RED : BLUE)
  const step = p.ucl ? p.ucl.map((u, i) => `${i ? 'L' : 'M'}${m.l + i * slot},${sy(u)} L${m.l + (i + 1) * slot},${sy(u)}`).join(' ') : null
  return (
    <div>
      <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="p-chart of monthly start failure rate">
        <Axis W={W} H={H} m={m} y0={0} y1={top} fmt={(x) => x.toFixed(2)} />
        {p.months.map((mo, i) => {
          const x = m.l + i * slot + slot * 0.2
          return (
            <g key={mo}>
              <rect x={x} y={sy(p.p[i])} width={slot * 0.6} height={Math.max(sy(0) - sy(p.p[i]), 1)} fill={color(i)} />
              {p.flags[i] && <text x={x + slot * 0.3} y={sy(p.p[i]) - 4} textAnchor="middle" fontSize="12" fontWeight="700" fill={RED}>▲ signal</text>}
              <text x={x + slot * 0.3} y={H - 12} textAnchor="middle" fontSize="11" fill={MUTED}>{mo}</text>
              <rect x={m.l + i * slot} y={m.t} width={slot} height={H - m.t - m.b} fill="transparent" onMouseEnter={() => setHov(i)} onClick={() => setHov(i)}>
                <title>{`${mo}: p = ${num(p.p[i], 3)} (${p.failures[i]} of ${p.tests[i]} tests), ${kind(i)}`}</title>
              </rect>
            </g>
          )
        })}
        {step && <path d={step} fill="none" stroke={INK2} strokeDasharray="6 4" strokeWidth="1.5" />}
      </svg>
      <div className="legend">
        <span>bars: failure rate p per month</span>
        <span>blue = in control</span><span>red + ▲ = signal</span><span>grey = too few tests to judge</span>
        {step && <span>dashed = UCL per month</span>}
      </div>
      {p.message && <p className="note">{p.message}{p.any_signal ? ' - a failure was seen: signal.' : ''}</p>}
      {p.small_n_warning && <p className="note">Warning: only a few tests per month, so these limits are rough. Every failed start also raises its own ticket.</p>}
      <div className="hover">{hov == null ? 'Hover or tap a bar for the month, rate and flag.' : `${p.months[hov]}: p = ${num(p.p[hov], 3)} (${p.failures[hov]} of ${p.tests[hov]} tests), ${kind(hov)}`}</div>
      <TableToggle head={['month', 'tests', 'failures', 'p', 'UCL', 'flag']}
        rows={p.months.map((mo, i) => [mo, p.tests[i], p.failures[i], num(p.p[i], 3), p.ucl ? num(p.ucl[i], 3) : 'none', kind(i)])} />
    </div>
  )
}

// ---------- detail panels ----------
// Tile colours use only thresholds already in SPEC (display rules, not statistics):
// temperatures vs the 1-6 C spec and the 1.5 / 5.5 C near-limit rule; capability vs 1.33 and 1.00;
// dead-time U vs the 1% flag line; T_max vs the test interval. No level = no colour.
const LEVEL_WORD = { green: '✓ ok', yellow: '⚠ watch', red: '▲ out' }
const tempLevel = (x) => (x == null ? null : x < 1 || x > 6 ? 'red' : x <= 1.5 || x >= 5.5 ? 'yellow' : 'green')
const capLevel = (x) => (x == null ? null : x >= 1.33 ? 'green' : x >= 1 ? 'yellow' : 'red')
const riskLevel = (u) => (u == null ? null : u <= 0.01 ? 'green' : 'yellow')
function Tile({ label, value, level }) {
  return (
    <div className={'tile' + (level ? ' lvl-' + level : '')}>
      <span>{label}</span><b>{value}</b>
      {level && <small>{LEVEL_WORD[level]}</small>}
    </div>
  )
}

function FridgeDetail({ f }) {
  const c = f.imr, k = f.capability, s = f.stuck_probe
  return (
    <div>
      <h3>Individuals chart: hourly mean temperature, latest week (limits from the baseline week)</h3>
      <FridgeChart f={f} />
      <h3>Numbers</h3>
      <div className="tiles">
        <Tile label="center °C" value={num(c.center, 3)} level={tempLevel(c.center)} />
        <Tile label="sigma (MR-bar/1.128)" value={num(c.sigma, 3)} />
        <Tile label="UCL °C" value={num(c.ucl, 3)} level={tempLevel(c.ucl)} />
        <Tile label="LCL °C" value={num(c.lcl, 3)} level={tempLevel(c.lcl)} />
        <Tile label="Cp (within)" value={num(k.cp)} level={capLevel(k.cp)} />
        <Tile label="Cpk (within)" value={num(k.cpk)} level={capLevel(k.cpk)} />
        <Tile label="Pp (overall)" value={num(k.pp)} level={capLevel(k.pp)} />
        <Tile label="Ppk (overall)" value={num(k.ppk, 4)} level={capLevel(k.ppk)} />
      </div>
      <p className="note">Ppk uses overall spread; the flag uses Ppk because 15-min readings are autocorrelated (Cpk looks optimistic). Flag when Ppk is below 1.33. {k.message}</p>
      <h3>Stuck probe</h3>
      <p>{s.stuck ? 'YES: ' + s.message : 'No'} (longest run of identical readings: {s.longest_run})</p>
      <h3>Readings outside 1-6 °C this week</h3>
      {f.out_of_spec.length ? <ul>{f.out_of_spec.map((o) => <li key={o.timestamp}>{when(o.timestamp)}: {num(o.temp_c, 1)} °C</li>)}</ul> : <p>None</p>}
    </div>
  )
}

function DaysSince({ d }) {
  // d comes from the attention list, which already counts the log entries made on this page
  return (
    <div>
      <h3>Days since each service</h3>
      <ul>{Object.entries(d).map(([k, v]) => <li key={k}>{k}: {v == null ? 'no record' : v + ' days'}</li>)}</ul>
    </div>
  )
}

function GeneratorDetail({ g }) {
  const d = g.dead_time
  return (
    <div>
      <h3>p-chart: start-test failure rate per month</h3>
      <PChart g={g} />
      <h3>Dead time (standby unit that is only tested every {d.T} days)</h3>
      {d.u == null ? <p>{d.message}. {g.generator_id} has no failures, so there is no signal.</p> : (
        <div>
          <div className="tiles">
            <Tile label="U (exact)" value={num(d.u, 4)} level={riskLevel(d.u)} />
            <Tile label="lambda*T/2 (approx)" value={num(d.approx, 4)} level={riskLevel(d.approx)} />
            <Tile label="U at 95% upper lambda" value={num(d.u_upper, 4)} level={riskLevel(d.u_upper)} />
            <Tile label="failures N" value={d.n_failures} level={d.n_failures > 0 ? 'yellow' : 'green'} />
            <Tile label="T_max days (exact)" value={num(d.t_max_exact, 2)}
              level={d.t_max_exact == null ? null : d.t_max_exact >= d.T ? 'green' : 'yellow'} />
          </div>
          <p>{d.flag ? 'Flag: U is above 1%. ' : 'U is within 1%. '}{d.caveat && d.caveat[0].toUpperCase() + d.caveat.slice(1) + '.'}</p>
          <p className="note">The longest test interval that keeps U at or under 1% is about {num(d.t_max_exact, 2)} days; the unit is tested every {d.T}.</p>
        </div>
      )}
    </div>
  )
}

function Checklist({ unit, onYes }) {
  // a box is ticked (and greyed) when the API says that service was done today (0 days since)
  return (
    <div>
      <h3>Routine tasks (tick when done)</h3>
      {CHECKLIST[unit.kind].map((t) => {
        const done = unit.days_since[t] === 0
        return (
          <div className="row" key={t}>
            <label><input type="checkbox" checked={done} disabled={done} onChange={() => onYes(unit.id, t)} /> {t}: done, yes</label>
            {done && <span className="note">logged</span>}
          </div>
        )
      })}
    </div>
  )
}

// Shown inline under its unit in the attention list.
function Detail({ unit, query, onYes }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    setData(null); setErr('')
    call(`${unit.kind === 'fridge' ? 'fridges' : 'generators'}/${unit.id}?${query}`)
      .then((d) => setData({ id: unit.id, d })).catch((e) => setErr(String(e.message)))
  }, [unit.id, query])
  // until the new unit's data arrives, `data` still holds the previous unit's: never draw it under the new kind
  const shown = data && data.id === unit.id ? data.d : null
  return (
    <section className="detail" aria-label={'Detail for ' + unit.id}>
      <h2>{unit.id} detail</h2>
      {err && <div className="err">{err}</div>}
      {!shown && !err && <p>Loading...</p>}
      {shown && (unit.kind === 'fridge' ? <FridgeDetail f={shown} /> : <GeneratorDetail g={shown} />)}
      <DaysSince d={unit.days_since} />
      <Checklist unit={unit} onYes={onYes} />
    </section>
  )
}

// ---------- attention list ----------
function Chip({ status }) { return <span className={'chip ' + status.replace(' ', '_')}>{ICON[status]} {status}</span> }

// Status bar: one box per unit in a fixed order, so the board never jumps around.
// green = working, yellow = maintenance needed, red = act now (fridges: move the blood)
const LIGHT = { OK: 'green', WATCH: 'green', 'TASKS COMPLETE': 'green', MAINTENANCE: 'yellow', URGENT: 'red', CRITICAL: 'red' }
// Where each fridge's blood is, from the log: "blood moved" (notes "FR-02 -> FR-04") and
// "blood returned" (notes "FR-04 -> FR-02"). Returns {source fridge: fridge now holding its blood}.
function bloodAway(local) {
  const away = {}
  for (const e of local) {
    const m = /^(\S+) -> (\S+)$/.exec(e.notes || '')
    if (!m) continue
    if (e.service_type === 'blood moved') away[m[1]] = m[2]
    if (e.service_type === 'blood returned') delete away[m[2]]
  }
  return away
}

// Backup fridge for a red fridge's blood: a green fridge first, else a yellow one; never an emptied
// fridge; among equals prefer one not already holding other blood, then the lowest id.
function pickBackup(att, source, away) {
  const holding = new Set(Object.values(away))
  const ok = att.units.filter((u) => u.kind === 'fridge' && u.id !== source && !(u.id in away))
  const rank = (u) => [LIGHT[u.status] === 'green' ? 0 : LIGHT[u.status] === 'yellow' ? 1 : 9, holding.has(u.id) ? 1 : 0]
  const best = ok.filter((u) => rank(u)[0] < 9)
    .sort((a, b) => rank(a)[0] - rank(b)[0] || rank(a)[1] - rank(b)[1] || a.id.localeCompare(b.id))
  return best[0] || null
}

function Occupancy({ u, away }) {
  if (u.kind !== 'fridge') return null
  const guests = Object.keys(away).filter((s) => away[s] === u.id)
  if (u.id in away) return <span className="occ empty">EMPTY: blood in {away[u.id]}</span>
  return <span className="occ">IN USE{guests.length ? ' · also holding ' + guests.join(', ') + "'s blood" : ''}</span>
}

// The move-blood step for one fridge: move out while red, move back once every task is done.
function BloodMove({ u, att, away, onMove, onReturn }) {
  const [asking, setAsking] = useState(false)
  if (u.kind !== 'fridge') return null
  if (u.id in away) {
    const holder = away[u.id]
    if (u.alerts.length > 0) return <div className="blood">Blood is in {holder} until {u.id}'s tasks are complete.</div>
    return <div className="blood"><button className="go" onClick={() => onReturn(u.id, holder)}>All tasks complete: move blood back from {holder} to {u.id}</button></div>
  }
  if (LIGHT[u.status] !== 'red') return null
  const to = pickBackup(att, u.id, away)
  if (!to) return <div className="blood warn">No green or yellow fridge is free: call for a backup fridge now.</div>
  if (!asking) return <div className="blood"><button className="go" onClick={() => setAsking(true)}>Move {u.id}'s blood to {to.id}</button> <span className="note">({to.id} is {to.status})</span></div>
  return (
    <div className="blood confirm">Moved {u.id}'s blood to {to.id}?{' '}
      <button onClick={() => { setAsking(false); onMove(u.id, to.id) }}>Yes, moved</button>{' '}
      <button onClick={() => setAsking(false)}>Cancel</button>
    </div>
  )
}

function StatusBar({ att, pinned, onPick, away }) {
  const units = [...att.units].sort((a, b) => a.id.localeCompare(b.id))
  return (
    <section className="statusbar" aria-label="Status of every unit">
      {units.map((u) => (
        <button key={u.id} className={'light ' + LIGHT[u.status] + (pinned === u.id ? ' picked' : '')} onClick={() => onPick(u)}
          aria-pressed={pinned === u.id} title={pinned === u.id ? 'Unpin ' + u.id : 'Show ' + u.id + ' first'}>
          <b>{u.id}</b>
          <span>{ICON[u.status]} {u.status}</span>
          {LIGHT[u.status] === 'red' && u.kind === 'fridge' && !(u.id in away) && <span className="act">move blood</span>}
          <Occupancy u={u} away={away} />
        </button>
      ))}
      <p className="sub legend">green = working · yellow = maintenance needed · red = urgent: act now (fridge: move blood to backup)</p>
    </section>
  )
}

// Two steps, because a finished alert disappears from the list and a quick double click
// would otherwise land on the next alert's button.
function TaskButton({ alert, onDone }) {
  const [asking, setAsking] = useState(false)
  if (!asking) return <button onClick={() => setAsking(true)}>Task complete</button>
  return (
    <span className="confirm">
      Done: {alert.task.replace(/\.$/, '')}?{' '}
      <button onClick={() => { setAsking(false); onDone(alert) }}>Yes</button>{' '}
      <button onClick={() => setAsking(false)}>Cancel</button>
    </span>
  )
}

// A finished task stays on the list as a green DONE row, so the unit's colour change has a visible reason.
function DoneRow({ alert, local }) {
  const row = local.filter((e) => e.service_type === 'task complete' && e.notes.startsWith(alert.id)).pop()
  return (
    <div className="alert done">
      <span className="chip TASKS_COMPLETE">✓ DONE</span> {alert.message}
      {row && <span className="note"> (completed by {row.technician} at {when(row.service_date)})</span>}
    </div>
  )
}

function Attention({ att, pinned, local, onTask, open, onToggle, detail, away, onMove, onReturn }) {
  // a unit picked in the status bar goes first; the rest keep the API's most-urgent-first order
  const units = pinned ? [...att.units].sort((a, b) => (b.id === pinned) - (a.id === pinned)) : att.units
  return (
    <section className="panel" id="attention">
      <h2>Today's attention list</h2>
      {units.map((u) => (
        <div className={'unit' + (u.id === pinned ? ' picked' : '')} key={u.id} id={'unit-' + u.id}>
          {u.id === pinned && <div className="note">Picked from the status bar (click its box again to unpin)</div>}
          <div className="unit-head">
            <Chip status={u.status} />
            <b>{u.id}</b> <span className="sub">{u.kind}</span>
            <button onClick={() => onToggle(u)}>{open === u.id ? 'Close detail' : 'Open detail'}</button>
            <Occupancy u={u} away={away} />
          </div>
          <BloodMove u={u} att={att} away={away} onMove={onMove} onReturn={onReturn} />
          {u.alerts.length === 0 && u.resolved_alerts.length === 0 && <div className="alert">No alerts.</div>}
          {u.alerts.map((a) => (
            <div className="alert" key={a.id}>
              <Chip status={a.tier} /> {a.message}
              {a.escalated && <span className="note"> (escalated from {a.base_tier})</span>}
              {a.retriggered && <span className="note"> (re-triggered after a task was completed)</span>}
              <div className="task">Task: {a.task}</div>
              <div className="row">
                <TaskButton alert={a} onDone={(al) => onTask(u.id, al)} />
              </div>
            </div>
          ))}
          {u.resolved_alerts.map((a) => <DoneRow key={a.id} alert={a} local={local} />)}
          {open === u.id && detail(u)}
        </div>
      ))}
    </section>
  )
}

function AnswerKey({ planted }) {
  return (
    <section className="panel">
      <h2>Answer key (what was planted)</h2>
      <p className="sub">Compare this with the status bar above. The tool never sees this list.</p>
      <ul>{Object.entries(planted).sort().map(([id, text]) => <li key={id}><b>{id}</b>: {text}</li>)}</ul>
    </section>
  )
}

function LogPanel({ local, bundled, onReplace, setErr }) {
  const download = () => {
    const url = URL.createObjectURL(new Blob([toCsv(local)], { type: 'text/csv' }))
    const a = document.createElement('a')
    a.href = url; a.download = 'maintenance_log_local.csv'; a.click()
    URL.revokeObjectURL(url)
  }
  const upload = async (ev) => {
    const file = ev.target.files[0]
    if (!file) return
    try { onReplace(parseCsv(await file.text())) } catch (e) { setErr(String(e.message)) }
    ev.target.value = ''
  }
  return (
    <section className="panel">
      <h2>Log (new entries from this page)</h2>
      <p className="sub">The bundled history ({bundled} entries) is always included. Only the entries below are sent with each request.</p>
      <div className="row">
        <button onClick={download}>Download log CSV</button>
        <label>Upload log CSV <input type="file" accept=".csv,text/csv" onChange={upload} /></label>
      </div>
      {local.length === 0 ? <p className="note">No new entries yet.</p> : (
        <div className="scroll"><table>
          <thead><tr>{COLS.map((c) => <th key={c}>{c}</th>)}</tr></thead>
          <tbody>{local.map((e, i) => <tr key={i}>{COLS.map((c) => <td key={c}>{e[c]}</td>)}</tr>)}</tbody>
        </table></div>
      )}
    </section>
  )
}

// ---------- App ----------
function App() {
  const [name, setName] = useState('manager')
  const [scenarios, setScenarios] = useState([])
  const [sc, setSc] = useState(null) // { key, seed }: the scenario on screen (seed only for 'random')
  const [local, setLocal] = useState([])
  const [att, setAtt] = useState(null)
  const [bundled, setBundled] = useState(0)
  const [open, setOpen] = useState(null) // id of the unit whose detail is open under its row
  const [pinned, setPinned] = useState(null)
  const [err, setErr] = useState('')
  const latest = useRef(0) // numbers the requests, so a late answer for an old scenario is ignored

  const refresh = (log, s) => {
    store(LOG_KEY + s.key, JSON.stringify(log))
    const id = ++latest.current
    call('attention?' + qs(s), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ log }) })
      .then((a) => { if (id === latest.current) { setAtt(a); setErr('') } })
      .catch((e) => { if (id === latest.current) setErr(String(e.message)) })
  }
  // Show a scenario: its own saved log, no open detail, no pin. A new random seed starts with an empty log.
  const enter = (key, seedIn, fresh) => {
    const s = { key, seed: key === 'random' ? (fresh || seedIn == null ? newSeed() : seedIn) : null }
    const log = key === 'random' && fresh ? [] : loadSaved(key)
    store(SC_KEY, key)
    if (s.seed != null) store(SEED_KEY, String(s.seed))
    setSc(s); setLocal(log); setOpen(null); setPinned(null); setAtt(null); setErr('')
    refresh(log, s)
    call('log?' + qs(s)).then((l) => setBundled(l.length)).catch(() => {})
  }
  const change = (log) => { setLocal(log); refresh(log, sc) }
  useEffect(() => {
    call('scenarios').then((list) => {
      setScenarios(list)
      const saved = recall(SC_KEY), seed = recall(SEED_KEY)
      enter(list.some((x) => x.key === saved) ? saved : 'mixed', seed ? Number(seed) : null, false)
    }).catch((e) => setErr(String(e.message)))
  }, [])

  // status bar: pin the unit to the top and open its detail there; a second click undoes both
  const pick = (u) => {
    if (pinned === u.id) { setPinned(null); setOpen(null); return }
    setPinned(u.id); setOpen(u.id)
  }
  useEffect(() => { // after the pinned unit has moved to the top, scroll its row to the top of the view
    if (pinned) document.getElementById('unit-' + pinned)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [pinned])

  const away = bloodAway(local)
  const add = (equipment_id, service_type, notes) =>
    change([...local, { equipment_id, service_date: nowIso(), service_type, technician: name || 'manager', notes: notes || '' }])

  return (
    <main>
      <h1>Blood bank equipment monitor</h1>
      <p className="sub">As of {att ? when(att.as_of) : '...'} (the last reading in the data).</p>
      <p className="sub">How to read this: start at the top and work down; the most urgent unit is first. Open a unit for its chart and numbers. Task complete logs who and when and resolves that one alert (it turns into a green DONE row and the unit's colour is recalculated from what is left); a new flag afterwards brings it back. When every alert of a unit is done it shows TASKS COMPLETE.</p>
      {sc && (
        <section className="panel">
          <div className="row">
            <label>Test dataset:{' '}
              <select value={sc.key} onChange={(e) => enter(e.target.value, null, true)}>
                {scenarios.map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
              </select>
            </label>
          </div>
          <p className="sub">{scenarios.find((x) => x.key === sc.key)?.description}</p>
          {sc.key === 'random' && (
            <div className="row">
              <span>Seed {sc.seed} · </span>
              <button onClick={() => enter('random', null, true)}>New random data</button>
            </div>
          )}
          <p className="sub">
            Download this dataset (CSV):{' '}
            {['fridge_telemetry', 'generator_tests', 'maintenance_log'].map((f, i) => (
              <span key={f}>{i ? ' · ' : ''}<a href={`data/${f}.csv?${qs(sc)}`} download>{f}.csv</a></span>
            ))}
          </p>
        </section>
      )}
      <div className="row"><label>Your name <input type="text" value={name} onChange={(e) => setName(e.target.value)} /></label></div>
      {err && <div className="err">{err}</div>}
      {att && <StatusBar att={att} pinned={pinned} onPick={pick} away={away} />}
      {att && att.answer_key && <AnswerKey planted={att.answer_key} />}
      {att ? <Attention att={att} pinned={pinned} local={local} open={open}
        onTask={(id, a) => add(id, 'task complete', a.id + ': ' + a.task)}
        away={away}
        onMove={(from, to) => add(from, 'blood moved', from + ' -> ' + to)}
        onReturn={(back, holder) => add(back, 'blood returned', holder + ' -> ' + back)}
        onToggle={(u) => setOpen(open === u.id ? null : u.id)}
        detail={(u) => <Detail unit={u} query={qs(sc)} onYes={(id, t) => add(id, 'task complete', id + ': ' + t)} />} /> : !err && <p>Loading...</p>}
      <LogPanel local={local} bundled={bundled} onReplace={change} setErr={setErr} />
    </main>
  )
}

createRoot(document.getElementById('root')).render(<App />)
