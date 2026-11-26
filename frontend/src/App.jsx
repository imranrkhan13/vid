import { useEffect, useState, useCallback, useRef } from 'react'
import { get, post, patch, put, del } from './api.js'

const useHash = () => { const [h, setH] = useState(location.hash.slice(1) || '/'); useEffect(() => { const f = () => setH(location.hash.slice(1) || '/'); addEventListener('hashchange', f); return () => removeEventListener('hashchange', f) }, []); return h }
const tone = (s) => ({ COMPLETED: 'ok', ACCEPTED: 'ok', READY: 'ok', GENERATING: 'acc', VALIDATING: 'acc', DOWNLOADING: 'acc', FAILED: 'bad', WAITING_FOR_CREDITS: 'warn', WAITING_FOR_PROVIDER: 'warn', MANUAL_ACTION_REQUIRED: 'warn', RETRYING: 'warn' }[s] || '')
const Pill = ({ s, children }) => <span className={'pill ' + tone(s)}>{children || s}</span>
const fmt = (n) => (n == null ? 'unknown' : Number(n).toFixed(1))

function useAsync(fn, deps) {
  const [st, setSt] = useState({ data: null, err: null, loading: true })
  const run = useCallback(() => fn().then((data) => setSt({ data, err: null, loading: false })).catch((err) => setSt((s) => ({ ...s, err, loading: false }))), deps)
  useEffect(() => { run() }, [run])
  return [st, run]
}

function Auth({ onAuth }) {
  const [mode, setMode] = useState('login'), [f, setF] = useState({ email: '', password: '', name: '' }), [err, setErr] = useState('')
  const go = async (e) => { e.preventDefault(); setErr(''); try { await post('/auth/' + mode, f); onAuth() } catch (x) { setErr(x.message) } }
  return <div className="auth"><div className="brand" style={{ fontSize: 28 }}>VGOH</div>
    <p className="mut">AI Video Generation Harness. Bring your own provider keys. Your keys, credits and projects stay private to your account; when one provider runs out of credit the next one continues the same video with full context.</p>
    <form className="card" onSubmit={go}>
      <h2>{mode === 'login' ? 'Sign in' : 'Create account'}</h2>
      {mode === 'register' && <><label>Name</label><input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></>}
      <label>Email</label><input type="email" required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} />
      <label>Password</label><input type="password" required minLength={8} value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} />
      {err && <div className="err">{err}</div>}
      <div className="row" style={{ marginTop: 14 }}><button>{mode === 'login' ? 'Sign in' : 'Create account'}</button>
        <a href="#" onClick={(e) => { e.preventDefault(); setMode(mode === 'login' ? 'register' : 'login') }}>{mode === 'login' ? 'Need an account?' : 'Have an account?'}</a></div>
    </form></div>
}

function Dashboard() {
  const [d] = useAsync(() => Promise.all([get('/dashboard'), get('/projects')]), [])
  if (d.loading) return <p className="mut">Loading…</p>
  if (d.err) return <div className="err">{d.err.message}</div>
  const [dash, projects] = d.data
  return <>
    <h1>Dashboard</h1><p className="mut">{dash.projects.total} projects · {dash.connections} provider connections</p>
    {dash.connections === 0 && <div className="note">No providers connected yet. <a href="#/providers">Connect a provider</a> before generating. Mock providers are available for testing the workflow and are clearly labeled as mock.</div>}
    <div className="row" style={{ margin: '10px 0' }}><a className="btn" href="#/new">New video</a></div>
    <div className="grid">{projects.map((p) => <a key={p.id} href={'#/p/' + p.id} className="card" style={{ color: 'inherit' }}>
      <div className="row"><h3 className="sp">{p.title}</h3><Pill s={p.status} /></div>
      <div className="mut">{p.aspect_ratio} · {p.resolution} · {p.target_duration}s</div>
      <div className="bar" style={{ marginTop: 10 }}><i style={{ width: Math.min(100, 100 * (p.accepted_duration || 0) / p.target_duration) + '%' }} /></div>
      <div className="mut" style={{ marginTop: 4 }}>{fmt(p.accepted_duration)}s of {p.target_duration}s accepted</div></a>)}</div>
    <h2 style={{ marginTop: 20 }}>Credits</h2>
    <div className="card"><table><thead><tr><th>Provider</th><th>Label</th><th>Available</th><th>Source</th><th /></tr></thead><tbody>
      {dash.credits.map((c, i) => <tr key={i}><td>{c.provider}</td><td>{c.label}</td><td>{fmt(c.available)} {c.unit}</td><td>{c.source}</td><td>{c.exhausted && <Pill s="WAITING_FOR_CREDITS">exhausted</Pill>}</td></tr>)}
      {!dash.credits.length && <tr><td colSpan={5} className="mut">None connected.</td></tr>}</tbody></table></div></>
}

function NewProject() {
  const [f, setF] = useState({ title: '', concept: '', script: '', target_duration: 32, aspect_ratio: '9:16', resolution: '1080p', fps: 30, visual_style: 'soft colorful 3D educational animation', mode: 'free_only', require_storyboard_approval: true })
  const [err, setErr] = useState(''), [busy, setBusy] = useState(false)
  const set = (k) => (e) => setF({ ...f, [k]: e.target.type === 'number' ? Number(e.target.value) : e.target.type === 'checkbox' ? e.target.checked : e.target.value })
  const go = async (e) => { e.preventDefault(); setBusy(true); setErr('')
    try { const p = await post('/projects', f); let llm = null; try { const pv = await get('/providers'); const c = pv.connections.find((x) => x.provider_id === 'llm_openai_compat'); if (c) llm = { model: (c.config || {}).model || 'default' } } catch (_) { /* heuristic planner */ } await post(`/projects/${p.id}/plan`, llm ? { llm } : {}); location.hash = '/p/' + p.id } catch (x) { setErr(x.message); setBusy(false) } }
  return <form onSubmit={go}><h1>New video</h1>
    <div className="card"><label>Title</label><input required value={f.title} onChange={set('title')} />
      <label>Concept / brief</label><textarea value={f.concept} onChange={set('concept')} placeholder="What is the video about? Who is it for?" />
      <label>Script or narration (optional, used for scene timing and subtitles)</label><textarea value={f.script} onChange={set('script')} /></div>
    <div className="card"><div className="grid">
      <div><label>Duration (seconds)</label><input type="number" min={2} max={600} value={f.target_duration} onChange={set('target_duration')} /></div>
      <div><label>Aspect ratio</label><select value={f.aspect_ratio} onChange={set('aspect_ratio')}><option>9:16</option><option>16:9</option><option>1:1</option></select></div>
      <div><label>Resolution</label><select value={f.resolution} onChange={set('resolution')}><option>1080p</option><option>720p</option></select></div>
      <div><label>FPS</label><input type="number" min={12} max={60} value={f.fps} onChange={set('fps')} /></div></div>
      <label>Visual style</label><input value={f.visual_style} onChange={set('visual_style')} />
      <label>Spend mode</label><select value={f.mode} onChange={set('mode')}><option value="free_only">Free credits only (no paid usage)</option></select>
      <label className="row"><input type="checkbox" style={{ width: 'auto' }} checked={f.require_storyboard_approval} onChange={set('require_storyboard_approval')} /> Review the storyboard before generating</label></div>
    {err && <div className="err">{err}</div>}<button disabled={busy}>{busy ? 'Planning…' : 'Create and plan storyboard'}</button></form>
}

function Console({ pid }) {
  const [ev, setEv] = useState([]), ref = useRef()
  useEffect(() => { setEv([]); const es = new EventSource(`/api/v1/projects/${pid}/events`); es.onmessage = (m) => setEv((x) => [...x, JSON.parse(m.data)].slice(-400)); return () => es.close() }, [pid])
  useEffect(() => { if (ref.current) ref.current.scrollTop = ref.current.scrollHeight }, [ev])
  return <div className="console" ref={ref}>{ev.map((e) => <div key={e.id} className={/fail|error|exhaust|timeout/.test(e.kind) ? 'bad' : /handoff|provider_selected|recover/.test(e.kind) ? 'warn' : ''}>
    <span className="t">{new Date(e.at + (e.at.endsWith('Z') ? '' : 'Z')).toLocaleTimeString()}</span> <span className="k">{e.kind}</span> {e.message}</div>)}{!ev.length && <span className="t">waiting for events…</span>}</div>
}

function Project({ pid }) {
  const [st, reload] = useAsync(() => Promise.all([get('/projects/' + pid), get(`/projects/${pid}/scenes`), get(`/projects/${pid}/timeline`), get(`/projects/${pid}/handoffs`), get(`/projects/${pid}/manual-tasks`)]), [pid])
  const [tab, setTab] = useState('board'), [err, setErr] = useState(''), [exp, setExp] = useState(null)
  useEffect(() => { const t = setInterval(reload, 3000); return () => clearInterval(t) }, [reload])
  const act = async (fn) => { setErr(''); try { await fn(); reload() } catch (x) { setErr(x.message) } }
  const doExport = () => act(async () => { const e = await post(`/projects/${pid}/export`, {}); const poll = async () => { const x = await get('/exports/' + e.id); setExp(x); if (!['COMPLETED', 'FAILED'].includes(x.status)) setTimeout(poll, 1500) }; poll() })
  if (st.loading) return <p className="mut">Loading…</p>
  if (st.err) return <div className="err">{st.err.message}</div>
  const [p, scenes, tl, handoffs, manual] = st.data
  const running = ['GENERATING', 'SELECTING_PROVIDER', 'DOWNLOADING', 'VALIDATING', 'COMPOSING'].includes(p.status)
  const colors = { ACCEPTED: '#1f6b52', GENERATING: '#14617a', FAILED: '#6b2a2a', RETRYING: '#6b5513' }
  return <>
    <div className="row"><h1 className="sp">{p.title}</h1><Pill s={p.status} /></div>
    <p className="mut">{p.aspect_ratio} · {p.resolution || p.canonical_state?.resolution || '720p'} · {p.fps}fps · {p.target_duration}s · {fmt(p.accepted_duration)}s accepted</p>
    <div className="row" style={{ marginBottom: 12 }}>
      {p.status === 'STORYBOARD_READY' && <button onClick={() => act(() => post(`/projects/${pid}/approve`))}>Approve storyboard</button>}
      {['STORYBOARD_READY', 'READY', 'APPROVED'].includes(p.status) && <button onClick={() => act(() => post(`/projects/${pid}/generate`))}>Generate</button>}
      {running && <button className="sec" onClick={() => act(() => post(`/projects/${pid}/pause`))}>Pause</button>}
      {p.status === 'PAUSED' && <button onClick={() => act(() => post(`/projects/${pid}/resume`))}>Resume</button>}
      {['WAITING_FOR_CREDITS', 'WAITING_FOR_PROVIDER', 'FAILED'].includes(p.status) && <button onClick={() => act(() => post(`/projects/${pid}/resume`))}>Retry now</button>}
      {!['COMPLETED', 'CANCELLED'].includes(p.status) && <button className="dng" onClick={() => confirm('Cancel this project?') && act(() => post(`/projects/${pid}/cancel`))}>Cancel</button>}
      {tl.accepted >= tl.total - 0.05 && <button onClick={doExport}>Export final video</button>}</div>
    {['WAITING_FOR_CREDITS', 'WAITING_FOR_PROVIDER'].includes(p.status) && <div className="banner">Waiting: no eligible provider has credit. Connect another provider in <a href="#/providers">Providers</a>; generation continues from the same scene automatically.</div>}
    {err && <div className="err">{err}</div>}
    <div className="tabs">{['board', 'timeline', 'console', 'handoffs', 'manual', 'export'].map((t) => <a key={t} href="#" className={tab === t ? 'on' : ''} onClick={(e) => { e.preventDefault(); setTab(t) }}>{{ board: 'Storyboard', timeline: 'Timeline', console: 'Live console', handoffs: 'Handoffs', manual: 'Manual tasks (' + manual.length + ')', export: 'Export' }[t]}</a>)}</div>
    {tab === 'board' && <div className="grid">{scenes.map((s) => <SceneCard key={s.id} s={s} p={p} act={act} />)}</div>}
    {tab === 'timeline' && <div className="card"><div className="tl">{tl.scenes.map((s) => <div key={s.scene_id} title={s.title} style={{ flex: s.duration, background: colors[s.status] || 'var(--panel2)' }}>{s.idx + 1}{s.provider ? ' · ' + s.provider : ''}</div>)}</div>
      <table style={{ marginTop: 12 }}><thead><tr><th>#</th><th>Start</th><th>Dur</th><th>Status</th><th>Provider</th><th>QA</th></tr></thead><tbody>{tl.scenes.map((s) => <tr key={s.scene_id}><td>{s.idx + 1}</td><td>{fmt(s.start)}s</td><td>{fmt(s.duration)}s</td><td><Pill s={s.status} /></td><td>{s.provider || '-'}</td><td>{s.overall ?? '-'}</td></tr>)}</tbody></table></div>}
    {tab === 'console' && <Console pid={pid} />}
    {tab === 'handoffs' && <div>{handoffs.map((h) => <div className="card" key={h.id}><div className="row"><b>{h.from || 'start'} → {h.to}</b><Pill>{h.reason}</Pill></div>
      <details><summary className="mut">Context package carried to the next provider</summary><pre style={{ whiteSpace: 'pre-wrap', fontSize: 12 }}>{JSON.stringify(h.package, null, 2)}</pre></details></div>)}{!handoffs.length && <p className="mut">No handoffs yet.</p>}</div>}
    {tab === 'manual' && <div>{manual.map((m, i) => <div className="card" key={i}><pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(m, null, 2)}</pre></div>)}{!manual.length && <p className="mut">No manual tasks.</p>}</div>}
    {tab === 'export' && <div className="card">{!exp ? <p className="mut">Finish all scenes, then press Export final video.</p> : <><Pill s={exp.status} />
      {exp.status === 'COMPLETED' && <><video controls src={`/api/v1/exports/${exp.id}/files/video`} /><div className="row" style={{ marginTop: 10 }}>
        <a className="btn" href={`/api/v1/exports/${exp.id}/files/video`} download>Download MP4</a>
        {['subtitles', 'report', 'storyboard'].map((k) => <a key={k} className="btn" style={{ background: 'var(--panel2)' }} href={`/api/v1/exports/${exp.id}/files/${k}`}>{k}</a>)}</div></>}
      {exp.status === 'FAILED' && <div className="err">{exp.error}</div>}</>}</div>}
  </>
}

function SceneCard({ s, p, act }) {
  const [edit, setEdit] = useState(false), [pr, setPr] = useState(s.prompt || ''), [dur, setDur] = useState(s.duration)
  const acc = s.candidates?.find((c) => c.id === s.accepted_artifact_id)
  const mock = acc && /^mock/.test(acc.provider || '')
  const short = (s.title || '').length > 48 ? s.title.slice(0, 46) + '…' : s.title
  return <div className="card scene"><div className="row"><b className="sp" title={s.title}>{s.idx + 1}. {short}</b><Pill s={s.status} /></div>
    <div className="mut">{fmt(s.duration)}s{acc ? ' · ' + acc.provider : ''}{mock ? ' · MOCK (simulated, not AI)' : ''}{s.retries ? ' · retries ' + s.retries : ''}</div>
    {acc && <video controls preload="metadata" playsInline poster={'/api/v1/media/' + acc.id + '?frame=first'} src={'/api/v1/media/' + acc.id} style={{ marginTop: 8 }} />}
    {acc?.quality && <div className="mut" style={{ marginTop: 4 }} title="OpenCV sharpness, motion, resolution and continuity heuristics. Not a human review and no character-identity match was tested.">Heuristic QA {acc.quality.overall}{mock ? ' (mock demo)' : ''} · no identity check</div>}
    {s.last_error && <div className="err">{s.last_error}</div>}
    {!edit ? <details><summary className="mut">Prompt details</summary><p style={{ fontSize: 13 }}>{s.prompt}</p></details> : <><textarea value={pr} onChange={(e) => setPr(e.target.value)} /><label>Duration</label><input type="number" value={dur} onChange={(e) => setDur(Number(e.target.value))} /></>}
    <div className="row">{!edit ? <button className="sec" onClick={() => setEdit(true)}>Edit</button> : <button onClick={() => act(async () => { await patch('/scenes/' + s.id, { prompt: pr, duration: dur }); setEdit(false) })}>Save</button>}
      <button className="sec" onClick={() => act(() => post(`/scenes/${s.id}/regenerate`))}>Regenerate</button></div></div>
}

function Providers() {
  const [st, reload] = useAsync(() => get('/providers'), [])
  const [sel, setSel] = useState(null), [f, setF] = useState({ label: 'default', secret: '', balance: '' }), [err, setErr] = useState(''), [led, setLed] = useState(null)
  if (st.loading) return <p className="mut">Loading…</p>
  if (st.err) return <div className="err">{st.err.message}</div>
  const { catalog, connections } = st.data
  const connect = async (e) => { e.preventDefault(); setErr(''); try { await post(`/providers/${sel.id}/connect`, { label: f.label, secret: f.secret || null, balance: f.balance === '' ? null : Number(f.balance), config: sel.id === 'llm_openai_compat' ? { base_url: f.base_url || '', model: f.model || 'default' } : sel.is_mock && f.init !== '' && f.init != null ? { initial_credits: Number(f.init) } : (sel.id === 'replicate' && f.free ? { free_trial_only: true } : {}) }); setSel(null); setF({ label: 'default', secret: '', balance: '' }); reload() } catch (x) { setErr(x.message) } }
  return <><h1>Providers</h1>
    <div className="note">Keys are encrypted on the server and never sent back to the browser. Credits are tracked per connection; a balance is only trusted when the provider reports it, otherwise it is marked as entered by you.</div>
    <h2>Your connections</h2>
    {connections.map((c) => <div className="card" key={c.id}><div className="row"><b className="sp">{c.provider_id} / {c.label}</b><span className="mut">{c.secret_hint}</span>
      <button className="sec" onClick={async () => setLed({ id: c.id, rows: await get(`/credentials/${c.id}/ledger`) })}>Ledger</button><button className="dng" onClick={async () => { await del('/credentials/' + c.id); reload() }}>Remove</button></div>
      {c.pools.map((q) => <div key={q.id} className="mut">{fmt(q.available)} {q.unit} available ({q.balance_source}, confidence {q.confidence}) {q.exhausted && <Pill s="WAITING_FOR_CREDITS">exhausted</Pill>} {q.is_paid && <Pill s="warn">paid</Pill>}</div>)}</div>)}
    {!connections.length && <p className="mut">None yet.</p>}
    {led && <div className="card"><h3>Ledger</h3><table><tbody>{led.rows.map((r, i) => <tr key={i}><td>{r.kind || r.entry_type}</td><td>{r.amount}</td><td className="mut">{r.note || r.reason || ''}</td></tr>)}</tbody></table></div>}
    <h2 style={{ marginTop: 18 }}>Catalog</h2>
    {sel && <form className="card" onSubmit={connect}><h3>Connect {sel.name}</h3>
      <label>Label</label><input value={f.label} onChange={(e) => setF({ ...f, label: e.target.value })} />
      <label>API key (stored encrypted, server side)</label><input type="password" autoComplete="off" value={f.secret} onChange={(e) => setF({ ...f, secret: e.target.value })} />
      {sel.id === 'llm_openai_compat' && <><label>Endpoint base URL (OpenAI-compatible, e.g. https://host/v1)</label><input value={f.base_url || ''} onChange={(e) => setF({ ...f, base_url: e.target.value })} /><label>Model name</label><input value={f.model || ''} onChange={(e) => setF({ ...f, model: e.target.value })} /></>}
      {sel.id === 'replicate' && <label style={{ display: 'flex', gap: 8, alignItems: 'center' }}><input type="checkbox" style={{ width: 'auto' }} checked={!!f.free} onChange={(e) => setF({ ...f, free: e.target.checked })} />Free-trial mode (operator-verified accounts only; the server rejects it for everyone else, who connect as a paid pool that stays blocked until paid spend is approved)</label>}
      <label>Credit balance, if the provider does not report one (optional)</label><input type="number" step="any" value={f.balance} onChange={(e) => setF({ ...f, balance: e.target.value })} />
      {sel.is_mock && <><label>MOCK provider: simulated credits (testing only, no real video is generated by mock providers)</label><input type="number" step="any" value={f.init ?? ''} onChange={(e) => setF({ ...f, init: e.target.value })} /></>}
      {err && <div className="err">{err}</div>}<div className="row" style={{ marginTop: 12 }}><button>Verify and connect</button><button type="button" className="sec" onClick={() => setSel(null)}>Cancel</button></div></form>}
    <div className="grid">{catalog.map((g) => <div className="card" key={g.id}><div className="row"><b className="sp">{g.name}</b>{g.is_mock && <Pill s="warn">MOCK</Pill>}<Pill s={g.implemented ? 'ok' : ''}>{g.implemented ? 'adapter ready' : 'not implemented'}</Pill></div>
      <div className="mut">{g.category} · {g.verification}</div><p style={{ fontSize: 12 }} className="mut">{g.notes}</p>
      <div className="row">{g.implemented && <button className="sec" onClick={() => { setSel(g); setErr('') }}>Connect</button>}{g.docs_url && <a href={g.docs_url} target="_blank" rel="noreferrer">Docs</a>}</div></div>)}</div></>
}

function Characters() {
  const [st, reload] = useAsync(() => get('/characters'), [])
  const [f, setF] = useState({ name: '', description: '', style: '' }), [err, setErr] = useState('')
  const add = async (e) => { e.preventDefault(); setErr(''); try { await post('/characters', f); setF({ name: '', description: '', style: '' }); reload() } catch (x) { setErr(x.message) } }
  const upload = async (c, file) => { const r = await fetch(`/api/v1/characters/${c.id}/references?kind=front&filename=${encodeURIComponent(file.name)}`, { method: 'PUT', headers: { 'X-VGOH': '1' }, body: file, credentials: 'same-origin' }); if (!r.ok) setErr('upload failed'); reload() }
  return <><h1>Character studio</h1><p className="mut">Characters keep the same look across scenes and providers.</p>
    <form className="card" onSubmit={add}><label>Name</label><input required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
      <label>Appearance (be specific: colors, clothing, shapes)</label><textarea value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />{err && <div className="err">{err}</div>}<div style={{ marginTop: 10 }}><button>Add character</button></div></form>
    <div className="grid">{(st.data || []).map((c) => <div className="card" key={c.id}><b>{c.name}</b><p className="mut">{c.description}</p><input type="file" accept="image/*" onChange={(e) => e.target.files[0] && upload(c, e.target.files[0])} />
      <button className="dng" style={{ marginTop: 8 }} onClick={async () => { await del('/characters/' + c.id); reload() }}>Delete</button></div>)}</div></>
}

function Coverage() {
  return <><h1>About</h1><div className="card"><p>VGOH coordinates several AI video providers. It plans the storyboard, picks a provider per scene, tracks each connection's credits, validates every clip, and when a provider runs out it continues on the next one with the style, characters, last frame and narration carried over.</p>
    <p className="mut">Current status is documented in the project repository under docs/. Mock providers exist only to test the workflow and are always labeled MOCK. No generated result in this app is faked.</p></div></>
}

export default function App() {
  const h = useHash(), [user, setUser] = useState(undefined)
  const load = () => get('/auth/me').then(setUser).catch(() => setUser(null))
  useEffect(() => { load() }, [])
  if (user === undefined) return <p className="mut" style={{ padding: 20 }}>Loading…</p>
  if (!user) return <Auth onAuth={load} />
  const m = h.match(/^\/p\/(.+)$/)
  const nav = [['/', 'Dashboard'], ['/new', 'New video'], ['/characters', 'Characters'], ['/providers', 'Providers'], ['/about', 'About']]
  return <div className="shell"><aside className="side"><div className="brand">VGOH</div><nav className="nav">{nav.map(([p, l]) => <a key={p} href={'#' + p} className={h === p ? 'on' : ''}>{l}</a>)}</nav>
    <div className="mut" style={{ marginTop: 20, fontSize: 12 }}>{user.email}<br /><a href="#" onClick={async (e) => { e.preventDefault(); await post('/auth/logout'); setUser(null) }}>Sign out</a></div></aside>
    <main className="main">{m ? <Project pid={m[1]} key={m[1]} /> : h === '/new' ? <NewProject /> : h === '/providers' ? <Providers /> : h === '/characters' ? <Characters /> : h === '/about' ? <Coverage /> : <Dashboard />}</main></div>
}
