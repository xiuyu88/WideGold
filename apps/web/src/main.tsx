import React, { useEffect, useRef, useState } from 'react'
import * as echarts from 'echarts'
import { createRoot } from 'react-dom/client'
import './style.css'

type Confidence = { confidence: number; coverage_score: number }
type Asset = {
  asset_id: string
  asset_name: string
  score: number
  label: string
  confidence: Confidence
  top_positive: string[]
  top_negative: string[]
  risk_flags: string[]
}
type Snapshot = {
  analysis_run_id: string
  analysis_date: string
  as_of: string
  status: string
  published: boolean
  assets: Asset[]
  warnings: string[]
  quality_gate: { overall_weighted_coverage?: number; passed_for_publish?: boolean }
  factor_resolution: { total_factors?: number; valid?: number; stale?: number; unavailable?: number }
}
type AdminRun = { analysis_run_id: string; status: string; snapshot?: Snapshot | null }
type Principal = { user_id?: string | null; username: string; roles: string[] }
type Diagnostics = {
  config: {
    valid: boolean
    factor_count: number
    calculator_count: number
    indicator_count: number
    required_indicator_count: number
    external_indicator_count: number
    issues: {level:string; code:string; message:string; resource?:string|null}[]
  }
  readiness?: {
    ready: boolean
    degraded: boolean
    checks: {name:string; status:string; required:boolean; latency_ms:number; message?:string|null}[]
  }
  external_bridge_configured?: boolean
  external_summary?: {total:number; available:number; stale:number; missing:number}
  recent_runs?: {analysis_run_id:string; analysis_date?:string|null; status:string; run_mode?:string|null; started_at?:string|null; error_code?:string|null}[]
  external_indicators?: {
    indicator_id:string; asset_id?:string|null; name:string; capability?:string|null;
    unit?:string|null; semantic?:string|null; expected_range?:number[]|null; asset_scoped?:boolean;
    operational_status:string; latest_observation_date?:string|null; latest_release_ts?:string|null;
    age_hours?:number|null; bridge_configured:boolean; last_source_id?:string|null
  }[]
  providers: {provider_key:string; status:string; latency_ms?:number|null; consecutive_failures?:number; details?:Record<string,unknown>}[]
  latest_published_run_id?: string | null
  latest_analysis_date?: string | null
}


type HistoryPoint = {
  analysis_run_id: string
  analysis_date: string
  as_of: string
  score: number
  label: string
  confidence: number
  tactical_score: number
  swing_score: number
  strategic_score: number
}
type EventItem = {
  event_id: string
  canonical_title: string
  event_type: string
  published_at: string
  source_tier: string
  strength: number
  confidence: number
  factor_ids: string[]
  reason_tags: string[]
}

type ConfigVersion = {
  config_version_id: string
  config_type: string
  version: string
  status: 'DRAFT' | 'APPROVED' | 'ACTIVE' | 'DEPRECATED' | string
  content_hash: string
  effective_from: string
  effective_to?: string | null
  created_at: string
  approved_at?: string | null
}
type ConfigVersionDetail = ConfigVersion & { content: Record<string, unknown> }


type FactorHealthState = {
  factor_id: string
  asset_id?: string | null
  as_of_ts: string
  state: number
  reliability: number
  coverage: number
  status: string
  quality_flags: string[]
  evidence_count: number
  event_count: number
}
type FactorHealthItem = {
  factor_id: string
  name: string
  family: string
  scope: string
  evidence?: string | null
  aggregate_status: string
  min_reliability: number
  min_coverage: number
  states: FactorHealthState[]
  quality_flags: string[]
}
type FactorHealth = {
  analysis_run_id: string
  analysis_date?: string | null
  status?: string | null
  source: string
  summary: {
    logical_factors: number
    physical_states: number
    valid: number
    stale: number
    partial: number
    unavailable: number
    conflicted: number
    average_reliability: number
    average_coverage: number
  }
  factors: FactorHealthItem[]
}

type AuditItem = {
  audit_id: string
  actor_user_id?: string | null
  action: string
  resource_type: string
  resource_id: string
  before?: Record<string, unknown> | null
  after?: Record<string, unknown> | null
  request_id?: string | null
  created_at: string
}

function HistoryChart({points}:{points:HistoryPoint[]}) {
  const ref = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    if (!ref.current) return
    const chart = echarts.init(ref.current)
    chart.setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: ['综合评分','Confidence'] },
      xAxis: { type: 'category', data: points.map(p => p.analysis_date), boundaryGap: false },
      yAxis: { type: 'value', min: 0, max: 100 },
      series: [
        { name:'综合评分', type:'line', smooth:true, data:points.map(p=>Number(p.score.toFixed(2))) },
        { name:'Confidence', type:'line', smooth:true, data:points.map(p=>Number(p.confidence.toFixed(2))) },
      ],
    })
    const resize = () => chart.resize()
    window.addEventListener('resize', resize)
    return () => { window.removeEventListener('resize', resize); chart.dispose() }
  }, [points])
  return <div className="historychart" ref={ref} />
}

type TraceEvent = {
  trace_seq: number
  analysis_run_id: string
  stage: string
  event_type: string
  status: string
  level: string
  message: string
  progress?: number | null
  details: Record<string, unknown>
  created_at: string
}

function App() {
  const [data, setData] = useState<Snapshot | null>(null)
  const [preview, setPreview] = useState<Snapshot | null>(null)
  const [trace, setTrace] = useState<TraceEvent[]>([])
  const [runStatus, setRunStatus] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [principal, setPrincipal] = useState<Principal | null>(null)
  const [diagnostics, setDiagnostics] = useState<Diagnostics | null>(null)
  const [selectedAsset, setSelectedAsset] = useState<string | null>(null)
  const [history, setHistory] = useState<HistoryPoint[]>([])
  const [events, setEvents] = useState<EventItem[]>([])
  const [configVersions, setConfigVersions] = useState<ConfigVersion[]>([])
  const [configDetail, setConfigDetail] = useState<ConfigVersionDetail | null>(null)
  const [configBusy, setConfigBusy] = useState(false)
  const [auditItems, setAuditItems] = useState<AuditItem[]>([])
  const [factorHealth, setFactorHealth] = useState<FactorHealth | null>(null)
  const [loginName, setLoginName] = useState('admin')
  const [loginPassword, setLoginPassword] = useState('')
  const streamRef = useRef<EventSource | null>(null)

  async function loadMe() {
    const res = await fetch('/api/v1/auth/me')
    if (res.ok) setPrincipal(await res.json())
    else setPrincipal(null)
  }

  async function login() {
    const res = await fetch('/api/v1/auth/login', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({username: loginName, password: loginPassword})
    })
    if (!res.ok) throw new Error('登录失败')
    setPrincipal(await res.json())
    setLoginPassword('')
    Promise.all([loadDiagnostics(), loadConfigVersions(), loadAudit(), loadFactorHealth()]).catch(() => undefined)
  }

  async function logout() {
    await fetch('/api/v1/auth/logout', {method:'POST'})
    setPrincipal(null)
  }

  async function loadDiagnostics() {
    const res = await fetch('/api/v1/admin/system/diagnostics')
    if (!res.ok) return
    setDiagnostics(await res.json())
  }

  async function loadConfigVersions() {
    const res = await fetch('/api/v1/admin/config/versions')
    if (!res.ok) return
    setConfigVersions((await res.json()).items ?? [])
  }

  async function loadFactorHealth(runId?: string) {
    const suffix = runId ? `?run_id=${encodeURIComponent(runId)}` : ''
    const res = await fetch(`/api/v1/admin/factors/health${suffix}`)
    if (!res.ok) return
    setFactorHealth(await res.json())
  }

  async function loadAudit() {
    const res = await fetch('/api/v1/admin/audit?limit=80')
    if (!res.ok) return
    setAuditItems((await res.json()).items ?? [])
  }

  async function loadConfigDetail(item: ConfigVersion) {
    const res = await fetch(`/api/v1/admin/config/versions/${encodeURIComponent(item.config_type)}/${encodeURIComponent(item.version)}`)
    if (!res.ok) throw new Error(await res.text())
    setConfigDetail(await res.json())
  }

  async function activateConfig(item: ConfigVersion) {
    if (item.status !== 'APPROVED') return
    setConfigBusy(true); setError('')
    try {
      const res = await fetch('/api/v1/admin/config/activate', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({config_type:item.config_type, version:item.version})
      })
      if (!res.ok) throw new Error(await res.text())
      await Promise.all([loadConfigVersions(), loadDiagnostics()])
      if (configDetail?.config_type === item.config_type) setConfigDetail(null)
    } finally { setConfigBusy(false) }
  }

  async function loadEvents() {
    const res = await fetch('/api/v1/events?limit=12')
    if (res.ok) setEvents((await res.json()).items ?? [])
  }

  async function loadAssetHistory(assetId: string) {
    setSelectedAsset(assetId)
    const res = await fetch(`/api/v1/assets/${assetId}/history?limit=120`)
    if (!res.ok) throw new Error(await res.text())
    setHistory((await res.json()).points ?? [])
  }

  async function loadCurrent() {
    const res = await fetch('/api/v1/dashboard/current')
    if (!res.ok) throw new Error(await res.text())
    setData(await res.json())
  }

  async function runMock() {
    setBusy(true); setError('')
    try {
      const res = await fetch('/api/v1/mock/run', { method: 'POST' })
      if (!res.ok) throw new Error(await res.text())
      setData(await res.json())
    } finally { setBusy(false) }
  }

  function openTraceStream(runId: string) {
    streamRef.current?.close()
    setTrace([])
    const es = new EventSource(`/api/v1/admin/runs/${runId}/trace/stream`)
    streamRef.current = es
    es.addEventListener('trace', (raw) => {
      const event = JSON.parse((raw as MessageEvent).data) as TraceEvent
      setTrace(items => [...items, event].slice(-120))
    })
    es.addEventListener('done', () => es.close())
    es.onerror = () => {
      // Status polling remains authoritative, so a dropped SSE connection is non-fatal.
      es.close()
    }
  }

  async function adminAnalyze() {
    setBusy(true); setError(''); setPreview(null); setRunStatus('PENDING')
    try {
      const res = await fetch('/api/v1/admin/analysis/run', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({run_mode: 'FULL_REFRESH', publish_mode: 'PREVIEW_ONLY', force_refresh: true})
      })
      if (!res.ok) throw new Error(await res.text())
      const start = await res.json()
      const runId = start.analysis_run_id
      openTraceStream(runId)
      for (let i = 0; i < 90; i++) {
        const statusRes = await fetch(`/api/v1/admin/runs/${runId}`)
        if (statusRes.ok) {
          const run: AdminRun = await statusRes.json()
          setRunStatus(run.status)
          if (run.snapshot) setPreview(run.snapshot)
          if (['PREVIEW_READY','QUALITY_FAILED','PUBLISHED','FAILED','DATA_READY','CANCELLED','SKIPPED'].includes(run.status)) {
            if (run.snapshot) await loadFactorHealth(runId)
            return
          }
        }
        await new Promise(r => setTimeout(r, 2000))
      }
      throw new Error('分析仍在运行，请稍后刷新。')
    } finally {
      streamRef.current?.close()
      setBusy(false)
    }
  }

  async function publishPreview() {
    if (!preview) return
    const res = await fetch(`/api/v1/admin/runs/${preview.analysis_run_id}/publish`, {method:'POST'})
    if (!res.ok) throw new Error(await res.text())
    const published = await res.json()
    setData(published); setPreview(null); setRunStatus('PUBLISHED')
  }

  useEffect(() => {
    loadMe().then(() => Promise.all([loadDiagnostics(), loadConfigVersions(), loadAudit(), loadFactorHealth()])).catch(() => undefined)
    loadCurrent().then(() => loadEvents()).catch(() => setError('尚无正式 Published Snapshot。'))
    return () => streamRef.current?.close()
  }, [])
  const shown = preview || data
  const latestProgress = trace.length ? trace[trace.length - 1].progress : null

  return <main>
    <header>
      <div><h1>WideGold</h1><p>宽基 + 黄金智能研判 · V1</p></div>
      <div className="actions">
        {principal?.roles.includes('ADMIN') ? <>
          <button disabled={busy} onClick={() => adminAnalyze().catch(e => setError(String(e)))}>更新并分析</button>
          <button className="secondary" disabled={busy} onClick={() => runMock().catch(e => setError(String(e)))}>Mock演示</button>
          <button className="secondary" onClick={() => logout().catch(e => setError(String(e)))}>退出 {principal.username}</button>
        </> : <div className="loginbox">
          <input value={loginName} onChange={e=>setLoginName(e.target.value)} placeholder="管理员账号" />
          <input type="password" value={loginPassword} onChange={e=>setLoginPassword(e.target.value)} placeholder="密码" />
          <button onClick={() => login().catch(e=>setError(String(e)))}>管理员登录</button>
        </div>}
      </div>
    </header>

    {error && <div className="notice">{error}</div>}
    {(busy || trace.length > 0) && <section className="tracebox">
      <div className="tracehead">
        <b>运行 Trace · {runStatus || 'RUNNING'}</b>
        {latestProgress != null && <span>{Math.round(latestProgress * 100)}%</span>}
      </div>
      <div className="traceevents">
        {trace.map(item => <div className={`traceevent ${item.level.toLowerCase()}`} key={item.trace_seq}>
          <time>{new Date(item.created_at).toLocaleTimeString()}</time>
          <code>{item.stage}</code>
          <span>{item.message}</span>
        </div>)}
      </div>
      <small>这里只显示低频业务阶段事件；完整 Docker/Prefect 技术日志不会传到浏览器。</small>
    </section>}

    {principal?.roles.includes('ADMIN') && diagnostics && <section className="diagnostics">
      <div className="diaghead">
        <b>系统诊断</b>
        <button className="secondary" onClick={() => loadDiagnostics().catch(() => undefined)}>刷新</button>
      </div>
      <div className="diaggrid">
        <span>配置：<b>{diagnostics.config.valid ? 'VALID' : 'INVALID'}</b></span>
        <span>Readiness：<b>{diagnostics.readiness?.ready ? 'READY' : 'NOT READY'}</b></span>
        <span>Factor：{diagnostics.config.factor_count}/{diagnostics.config.calculator_count}</span>
        <span>Required Indicator：{diagnostics.config.required_indicator_count}</span>
        <span>External/MCP：{diagnostics.config.external_indicator_count}</span>
        <span>External可用：{diagnostics.external_summary?.available ?? '-'} / {diagnostics.external_summary?.total ?? '-'}</span>
        <span>External Bridge：{diagnostics.external_bridge_configured ? 'ON' : 'OFF'}</span>
      </div>
      {diagnostics.readiness && <details>
        <summary>Runtime Dependencies</summary>
        <div className="providerlist">{diagnostics.readiness.checks.map(c => <div key={c.name}>
          <code>{c.name}{c.required ? ' *' : ''}</code><span>{c.status}</span><span>{c.latency_ms.toFixed(1)} ms</span>
        </div>)}</div>
      </details>}
      {diagnostics.external_indicators && diagnostics.external_indicators.length > 0 && <details open>
        <summary>External / MCP 指标状态（{diagnostics.external_indicators.length} 个实际 scope）</summary>
        <div className="externalgrid">{diagnostics.external_indicators.map(x => <div key={`${x.indicator_id}:${x.asset_id ?? ''}`}>
          <code>{x.indicator_id}{x.asset_id ? ` · ${x.asset_id}` : ''}</code>
          <span>{x.operational_status}</span>
          <span>{x.latest_observation_date ?? '无数据'}{x.age_hours != null ? ` · ${x.age_hours.toFixed(0)}h` : ''}</span>
          <small>
            <b>{x.capability ?? '-'}</b> · unit={x.unit ?? '-'}
            {x.expected_range?.length === 2 ? ` · range=[${x.expected_range[0]}, ${x.expected_range[1]}]` : ''}
            {x.last_source_id ? ` · source=${x.last_source_id}` : ''}
            {x.semantic ? <><br/>{x.semantic}</> : null}
          </small>
        </div>)}</div>
      </details>}
      {diagnostics.config.issues.filter(x => x.level === 'WARNING').length > 0 && <details>
        <summary>External / 配置告警（{diagnostics.config.issues.filter(x => x.level === 'WARNING').length}）</summary>
        {diagnostics.config.issues.filter(x => x.level === 'WARNING').map((x,i) => <div key={i}><code>{x.resource}</code> · {x.message}</div>)}
      </details>}
      {diagnostics.providers.length > 0 && <details>
        <summary>Provider Health（{diagnostics.providers.length}）</summary>
        <div className="providerlist">{diagnostics.providers.map(p => <div key={p.provider_key}>
          <code>{p.provider_key}</code><span>{p.status}</span><span>{p.latency_ms ?? '-'} ms</span>
        </div>)}</div>
      </details>}
      {diagnostics.recent_runs && diagnostics.recent_runs.length > 0 && <details>
        <summary>最近运行（{diagnostics.recent_runs.length}）</summary>
        <div className="runlist">{diagnostics.recent_runs.map(r => <div key={r.analysis_run_id}>
          <code>{r.analysis_date ?? '-'}</code><span>{r.status}</span><span>{r.run_mode ?? '-'}</span><small>{r.analysis_run_id.slice(0,8)}</small>
        </div>)}</div>
      </details>}
    </section>}

    {principal?.roles.includes('ADMIN') && factorHealth && <section className="factorhealth">
      <div className="panelhead">
        <div><b>Factor Health</b><small>Run {factorHealth.analysis_run_id.slice(0,8)} · {factorHealth.source} · 逻辑因子 {factorHealth.summary.logical_factors} / 状态行 {factorHealth.summary.physical_states}</small></div>
        <button className="secondary" onClick={() => loadFactorHealth().catch(e=>setError(String(e)))}>刷新</button>
      </div>
      <div className="factorstats">
        <span>VALID <b>{factorHealth.summary.valid}</b></span>
        <span>PARTIAL <b>{factorHealth.summary.partial}</b></span>
        <span>STALE <b>{factorHealth.summary.stale}</b></span>
        <span>UNAVAILABLE <b>{factorHealth.summary.unavailable}</b></span>
        <span>CONFLICTED <b>{factorHealth.summary.conflicted}</b></span>
        <span>Avg Reliability <b>{Math.round(factorHealth.summary.average_reliability*100)}%</b></span>
        <span>Avg Coverage <b>{Math.round(factorHealth.summary.average_coverage*100)}%</b></span>
      </div>
      <div className="factorhealthlist">{factorHealth.factors.map(f => <details key={f.factor_id}>
        <summary>
          <code>{f.factor_id}</code>
          <strong>{f.name}</strong>
          <span className={`factorstatus ${f.aggregate_status.toLowerCase()}`}>{f.aggregate_status}</span>
          <span>Rel {Math.round(f.min_reliability*100)}%</span>
          <span>Cov {Math.round(f.min_coverage*100)}%</span>
        </summary>
        <div className="factorstates">{f.states.length === 0 ? <p className="muted">本次 Run 没有该因子状态。</p> : f.states.map((state, i) => <div key={`${state.asset_id ?? 'GLOBAL'}:${i}`}>
          <code>{state.asset_id ?? 'GLOBAL'}</code>
          <span>{state.status}</span>
          <span>state={state.state.toFixed(1)}</span>
          <span>rel={Math.round(state.reliability*100)}%</span>
          <span>cov={Math.round(state.coverage*100)}%</span>
          <small>{state.quality_flags.join(' / ') || '无质量告警'}</small>
        </div>)}</div>
        {f.quality_flags.length > 0 && <p className="risk"><b>Flags：</b>{f.quality_flags.join(' / ')}</p>}
      </details>)}</div>
    </section>}

    {principal?.roles.includes('ADMIN') && configVersions.length > 0 && <section className="configpanel">
      <div className="panelhead">
        <div><b>Runtime Config Versions</b><small>生产只读取 ACTIVE；APPROVED 可激活，DRAFT 不可直接进入生产。</small></div>
        <button className="secondary" disabled={configBusy} onClick={() => loadConfigVersions().catch(e=>setError(String(e)))}>刷新</button>
      </div>
      <div className="configtable">
        {[...new Set(configVersions.map(x=>x.config_type))].sort().map(type => {
          const versions = configVersions.filter(x=>x.config_type===type)
          const active = versions.find(x=>x.status==='ACTIVE')
          return <div className="configrow" key={type}>
            <code>{type}</code>
            <span className="activeversion">ACTIVE {active?.version ?? '缺失'}</span>
            <div className="versionchips">{versions.slice(0,6).map(v => <button
              key={v.config_version_id}
              className={`versionchip ${v.status.toLowerCase()}`}
              title={`${v.status} · ${v.content_hash.slice(0,12)}`}
              onClick={() => loadConfigDetail(v).catch(e=>setError(String(e)))}
            >{v.version} · {v.status}</button>)}</div>
            <div className="configactions">{versions.filter(v=>v.status==='APPROVED').slice(0,2).map(v => <button
              key={v.config_version_id}
              disabled={configBusy}
              onClick={() => activateConfig(v).catch(e=>setError(String(e)))}
            >激活 {v.version}</button>)}</div>
          </div>
        })}
      </div>
      {configDetail && <div className="configdetail">
        <div className="panelhead"><b>{configDetail.config_type} · {configDetail.version} · {configDetail.status}</b><button className="secondary" onClick={()=>setConfigDetail(null)}>关闭</button></div>
        <pre>{JSON.stringify(configDetail.content, null, 2)}</pre>
      </div>}
    </section>}

    {principal?.roles.includes('ADMIN') && <section className="auditpanel">
      <div className="panelhead">
        <div><b>Audit Log</b><small>只展示管理员治理动作摘要；完整技术日志仍保留在 Docker / Prefect。</small></div>
        <button className="secondary" onClick={() => loadAudit().catch(e=>setError(String(e)))}>刷新</button>
      </div>
      {auditItems.length === 0 ? <p className="muted">暂无审计记录。</p> : <div className="auditlist">
        {auditItems.map(item => <details key={item.audit_id}>
          <summary>
            <time>{new Date(item.created_at).toLocaleString()}</time>
            <code>{item.action}</code>
            <span>{item.resource_type} · {item.resource_id}</span>
          </summary>
          <pre>{JSON.stringify({before:item.before ?? null, after:item.after ?? null, request_id:item.request_id ?? null}, null, 2)}</pre>
        </details>)}
      </div>}
    </section>}

    {preview && <div className="previewbar">
      <span>管理员 Preview · {preview.status}</span>
      {preview.quality_gate?.passed_for_publish && <button onClick={() => publishPreview().catch(e => setError(String(e)))}>Publish</button>}
    </div>}

    {shown && <>
      <section className="meta">
        <b>{shown.published ? '正式结果' : '预览结果'}</b> · 分析日 {shown.analysis_date} · {new Date(shown.as_of).toLocaleString()}
        {shown.quality_gate?.overall_weighted_coverage !== undefined &&
          <> · 数据覆盖 {(shown.quality_gate.overall_weighted_coverage * 100).toFixed(0)}%</>}
      </section>
      <section className="grid">
        {shown.assets.map(asset => <article className="assetcard" key={asset.asset_id} onClick={() => loadAssetHistory(asset.asset_id).catch(e=>setError(String(e)))}>
          <div className="top"><h2>{asset.asset_name}</h2><strong>{asset.score.toFixed(1)}</strong></div>
          <div className="label">{asset.label} · 信心 {asset.confidence.confidence.toFixed(0)}%</div>
          <p><b>利多：</b>{asset.top_positive.join(' / ') || '无明显主导因素'}</p>
          <p><b>利空：</b>{asset.top_negative.join(' / ') || '无明显主导因素'}</p>
          {asset.risk_flags.length > 0 && <p className="risk"><b>风险：</b>{asset.risk_flags.join(' / ')}</p>}
        </article>)}
      </section>
      {selectedAsset && <section className="historypanel">
        <div className="panelhead"><b>{shown.assets.find(a=>a.asset_id===selectedAsset)?.asset_name ?? selectedAsset} · 历史评分</b><button className="secondary" onClick={()=>setSelectedAsset(null)}>关闭</button></div>
        {history.length > 0 ? <HistoryChart points={history} /> : <p>暂无 Published 历史评分。</p>}
      </section>}
      {events.length > 0 && <section className="eventpanel">
        <div className="panelhead"><b>本次分析的重要事件</b><span>{events.length} 条</span></div>
        <div className="eventlist">{events.map(e => <div key={e.event_id}>
          <time>{new Date(e.published_at).toLocaleString()}</time>
          <strong>{e.canonical_title}</strong>
          <span>{e.event_type} · 强度 {e.strength}/5 · 信心 {Math.round(e.confidence*100)}%</span>
          <small>{e.factor_ids.join(' / ')}</small>
        </div>)}</div>
      </section>}
      {shown.factor_resolution?.total_factors && <section className="quality">
        因子状态：有效 {shown.factor_resolution.valid ?? 0} / 陈旧 {shown.factor_resolution.stale ?? 0} / 不可用 {shown.factor_resolution.unavailable ?? 0}
      </section>}
      {shown.warnings.length > 0 && <details className="warning"><summary>运行告警（{shown.warnings.length}）</summary>{shown.warnings.map((w,i)=><div key={i}>{w}</div>)}</details>}
    </>}
  </main>
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>)
