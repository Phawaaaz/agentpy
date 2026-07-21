import { useEffect, useRef, useState } from 'react'
import Sidebar from './Sidebar.jsx'
import TopBar from './TopBar.jsx'
import Message from './Message.jsx'
import AdminDashboard from './AdminDashboard.jsx'
import {
  getModels, listSessions, createSession, deleteSession, getMessages, streamTurn, uploadFiles,
  listFiles, downloadFile, cancelTurn, getSkills,
  getInstalledSkills, installSkill, uninstallSkill,
} from '../api.js'

const LAST_SID_KEY = 'harness_demo_last_sid'
const IMAGE_RE = /\.(png|jpe?g|gif|webp)$/i

export default function Workspace({ auth, onLogout }) {
  const { access_token: token } = auth
  const user = { username: auth.username, role: auth.role }

  const [models, setModels] = useState(['demo/scripted'])
  const [sessions, setSessions] = useState([])
  const [activeId, setActiveId] = useState(null)
  const [model, setModel] = useState('demo/scripted')
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [error, setError] = useState('')
  const [view, setView] = useState('chat')  // 'chat' | 'admin'
  // Focus mode: collapse tool cards into one "thinking" line. Persisted.
  const [hideTools, setHideTools] = useState(
    () => localStorage.getItem('harness_hide_tools') === '1')
  const [attached, setAttached] = useState([])  // files uploaded for the next turn
  const [uploading, setUploading] = useState(false)
  const [files, setFiles] = useState([])        // workspace files (for download)
  const [filesOpen, setFilesOpen] = useState(false)
  const [skills, setSkills] = useState([])      // admin-defined prompt presets
  const [skillsOpen, setSkillsOpen] = useState(false)
  const [installed, setInstalled] = useState([])  // agent skills (SKILL.md folders)
  const [installedOpen, setInstalledOpen] = useState(false)
  const [installing, setInstalling] = useState(false)
  const [menuIndex, setMenuIndex] = useState(0)     // keyboard selection in the slash/✨ menu
  const [slashDismissed, setSlashDismissed] = useState(false)  // Esc closes the "/" menu
  const isAdmin = user.role === 'admin'

  async function refreshSkills() {
    try { setSkills(await getSkills(token)) } catch { /* ignore */ }
  }

  async function refreshInstalled() {
    try { setInstalled((await getInstalledSkills(token)).skills) } catch { /* ignore */ }
  }

  async function onSkillPicked(e) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    setInstalling(true); setError('')
    try {
      await installSkill(token, file)
      await refreshInstalled()
    } catch (err) { setError(err.message || 'Could not install skill') }
    finally { setInstalling(false) }
  }

  async function removeInstalled(name) {
    if (!confirm(`Uninstall skill "${name}"?`)) return
    try { await uninstallSkill(token, name); await refreshInstalled() }
    catch (err) { setError(err.message || 'Could not uninstall skill') }
  }
  // Load on mount and whenever we return to chat — an admin may have just
  // added or removed a skill in the dashboard.
  useEffect(() => { if (view === 'chat') { refreshSkills(); refreshInstalled() } /* eslint-disable-next-line */ }, [view])

  async function refreshFiles(sid = activeId) {
    if (!sid) return
    try { setFiles((await listFiles(token, sid)).files) } catch { /* ignore */ }
  }

  function stopTurn() {
    streamRef.current?.abort()          // stop consuming the stream immediately
    if (activeId) cancelTurn(token, activeId).catch(() => {})  // stop the agent server-side
  }

  function toggleHideTools() {
    setHideTools((v) => {
      localStorage.setItem('harness_hide_tools', v ? '0' : '1')
      return !v
    })
  }

  const chatRef = useRef(null)
  const streamRef = useRef(null)
  const inputRef = useRef(null)
  const fileRef = useRef(null)
  const skillRef = useRef(null)
  const initialized = useRef(false)  // guard: StrictMode mounts effects twice in dev

  // Focus the composer whenever a session is ready and we're not streaming,
  // so the user can just start typing on arrival.
  useEffect(() => {
    if (activeId && !streaming) inputRef.current?.focus()
  }, [activeId, streaming])

  // --- initial load: models, then resume the last session or start a fresh
  //     one automatically so the user lands ready to chat (no manual click) ---
  useEffect(() => {
    if (initialized.current) return  // run the bootstrap exactly once
    initialized.current = true
    ;(async () => {
      let defaultModel = 'demo/scripted'
      try {
        const m = await getModels(token)
        setModels(m.models)
        defaultModel = m.default || m.models[0]
        setModel(defaultModel)
      } catch (e) { handleAuthError(e) }
      try {
        const list = await listSessions(token)
        setSessions(list)
        const last = localStorage.getItem(LAST_SID_KEY)
        const pick = list.find((s) => s.session_id === last) || list[0]
        if (pick) {
          selectSession(pick.session_id, pick.model)
        } else {
          await startSession(defaultModel)  // no sessions yet -> open one now
        }
      } catch (e) { handleAuthError(e) }
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function handleAuthError(e) {
    if (e.status === 401) onLogout()
    else setError(e.message || 'Something went wrong')
  }

  async function refreshSessions(resume = false) {
    try {
      const list = await listSessions(token)
      setSessions(list)
      if (resume) {
        const last = localStorage.getItem(LAST_SID_KEY)
        const pick = list.find((s) => s.session_id === last) || list[0]
        if (pick) selectSession(pick.session_id, pick.model)
      }
    } catch (e) { handleAuthError(e) }
  }

  async function selectSession(sid, sidModel) {
    if (streaming) return
    setActiveId(sid)
    localStorage.setItem(LAST_SID_KEY, sid)
    setError('')
    try {
      const data = await getMessages(token, sid)
      setModel(data.model || sidModel || 'demo/scripted')
      setMessages(data.messages.map((m) => ({
        role: m.role, text: m.text, model: m.model, tools: [],
      })))
      refreshFiles(sid)
    } catch (e) { handleAuthError(e) }
  }

  async function startSession(modelToUse) {
    try {
      const s = await createSession(token, modelToUse)
      setSessions((prev) => [...prev, s])
      setActiveId(s.session_id)
      localStorage.setItem(LAST_SID_KEY, s.session_id)
      setModel(s.model)
      setMessages([])
      setError('')
      return s
    } catch (e) { handleAuthError(e) }
  }

  function newSession() {
    if (streaming) return
    startSession(model)
  }

  async function removeSession(sid) {
    try {
      await deleteSession(token, sid)
      setSessions((prev) => prev.filter((s) => s.session_id !== sid))
      if (sid === activeId) {
        setActiveId(null)
        setMessages([])
        localStorage.removeItem(LAST_SID_KEY)
      }
    } catch (e) { handleAuthError(e) }
  }

  function changeModel(m) {
    setModel(m)  // applied on the next turn (POST carries the model)
    setSessions((prev) => prev.map((s) => s.session_id === activeId ? { ...s, model: m } : s))
  }

  // --- file upload into the session workspace ---
  async function onFilesPicked(e) {
    const files = Array.from(e.target.files || [])
    e.target.value = ''  // allow re-picking the same file later
    if (!files.length || !activeId) return
    setUploading(true); setError('')
    try {
      const res = await uploadFiles(token, activeId, files)
      setAttached((prev) => [...prev, ...res.files])
    } catch (err) { handleAuthError(err) }
    finally { setUploading(false) }
  }

  // --- send a turn and consume the SSE stream ---
  async function send() {
    const text = input.trim()
    if ((!text && attached.length === 0) || streaming || !activeId) return
    const files = attached
    // Images are shown to a vision-capable model directly (see-once); the
    // rest are just noted as being in the workspace for the file tools.
    const images = files.filter((f) => IMAGE_RE.test(f.name)).map((f) => f.name)
    // What the user sees, and (with a note about any files) what the agent gets.
    const shown = text || `I've uploaded ${files.length} file${files.length !== 1 ? 's' : ''}.`
    const note = files.length
      ? `\n\n[Files are now in your workspace: ${files.map((f) => f.name).join(', ')}. Read them if relevant to this request.]`
      : ''
    setInput(''); setAttached([])
    setError('')
    setStreaming(true)

    setMessages((prev) => [
      ...prev,
      { role: 'user', text: shown, files, tools: [] },
      { role: 'assistant', text: '', model, tools: [], _streaming: true },
    ])

    const patchAssistant = (fn) => setMessages((prev) => {
      const next = [...prev]
      for (let i = next.length - 1; i >= 0; i--) {
        if (next[i].role === 'assistant') { next[i] = fn({ ...next[i] }); break }
      }
      return next
    })

    const onEvent = (ev, data) => {
      if (ev === 'model_info') {
        patchAssistant((a) => ({ ...a, model: data.model }))
      } else if (ev === 'token') {
        patchAssistant((a) => ({ ...a, text: (a.text || '') + (data.delta || '') }))
      } else if (ev === 'tool_call_started') {
        patchAssistant((a) => ({
          ...a,
          tools: [...a.tools, { id: data.id, name: data.name, input: data.input, running: true }],
        }))
      } else if (ev === 'tool_call_finished') {
        patchAssistant((a) => ({
          ...a,
          tools: a.tools.map((t) => t.id === data.id
            ? { ...t, output: data.output, blocked: data.blocked, error: data.error, running: false }
            : t),
        }))
      } else if (ev === 'assistant_message') {
        patchAssistant((a) => ({ ...a, text: data.text, model: data.model || a.model }))
      } else if (ev === 'usage') {
        patchAssistant((a) => ({ ...a, usage: data }))
      } else if (ev === 'error') {
        setError(data.message === 'connection lost'
          ? 'Connection lost — the stream was interrupted. Retry your message.'
          : (data.message || 'Stream error'))
      }
    }

    // Snapshot the workspace so we can show which files this turn produced.
    const beforeFiles = new Map(files.map((f) => [f.name, f.size]))

    const ctrl = streamTurn(token, activeId, shown + note, model, images, onEvent)
    streamRef.current = ctrl
    await ctrl.done
    streamRef.current = null
    setStreaming(false)

    // Diff the workspace: files that are new or changed this turn become
    // downloadable chips under the reply ("return files").
    let produced = []
    try {
      const after = (await listFiles(token, activeId)).files
      produced = after.filter((f) => beforeFiles.get(f.name) !== f.size)
      setFiles(after)
    } catch { /* ignore */ }
    patchAssistant((a) => {
      const { _streaming, ...rest } = a
      return produced.length ? { ...rest, produced } : rest
    })
  }

  // auto-scroll to the newest content
  useEffect(() => {
    if (chatRef.current) chatRef.current.scrollTop = chatRef.current.scrollHeight
  }, [messages, streaming])

  // --- slash-command menu: typing "/" (optionally + a query, no spaces) at
  //     the start of an empty composer suggests skills to insert. The ✨
  //     button opens the same menu unfiltered. ---
  const slashMatch = /^\/(\S*)$/.exec(input)
  const slashActive = !slashDismissed && slashMatch !== null
  const slashQuery = slashActive ? slashMatch[1].toLowerCase() : ''
  const menuSkills = slashActive
    ? skills.filter((s) => s.name.toLowerCase().includes(slashQuery) ||
                           (s.description || '').toLowerCase().includes(slashQuery))
    : (skillsOpen ? skills : [])
  const menuOpen = menuSkills.length > 0 && (slashActive || skillsOpen)

  function pickSkill(s) {
    setInput(s.template)
    setSkillsOpen(false)
    setSlashDismissed(true)  // don't reopen for the inserted text
    setMenuIndex(0)
    inputRef.current?.focus()
  }

  function onInputChange(e) {
    setInput(e.target.value)
    setMenuIndex(0)
    if (slashDismissed) setSlashDismissed(false)  // let a fresh "/" reopen it
  }

  function onKeyDown(e) {
    if (menuOpen) {
      if (e.key === 'ArrowDown') { e.preventDefault(); setMenuIndex((i) => (i + 1) % menuSkills.length); return }
      if (e.key === 'ArrowUp') { e.preventDefault(); setMenuIndex((i) => (i - 1 + menuSkills.length) % menuSkills.length); return }
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); pickSkill(menuSkills[Math.min(menuIndex, menuSkills.length - 1)]); return }
      if (e.key === 'Escape') { e.preventDefault(); setSkillsOpen(false); setSlashDismissed(true); return }
    }
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
  }

  const title = activeId ? `Session ${activeId.split('-')[1] || ''}` : 'No session selected'

  return (
    <div className="app">
      <Sidebar
        user={user} sessions={sessions} activeId={view === 'chat' ? activeId : null}
        onSelect={(sid) => { setView('chat'); const s = sessions.find((x) => x.session_id === sid); selectSession(sid, s?.model) }}
        onNew={() => { setView('chat'); newSession() }} onDelete={removeSession} onLogout={onLogout}
        isAdmin={isAdmin} onAdmin={() => setView('admin')} adminActive={view === 'admin'}
      />

      {view === 'admin' ? (
        <AdminDashboard token={token} me={user.username} onClose={() => setView('chat')} />
      ) : (
      <div className="main">
        <TopBar
          models={models} model={model} onModelChange={changeModel}
          disabled={streaming} sandboxOn={true} title={title}
          hideTools={hideTools} onToggleHideTools={toggleHideTools}
          fileCount={files.length}
          filesOpen={filesOpen}
          onToggleFiles={() => { const n = !filesOpen; setFilesOpen(n); if (n) refreshFiles() }}
          skillCount={installed.length}
          skillsOpen={installedOpen}
          onToggleSkills={() => { const n = !installedOpen; setInstalledOpen(n); if (n) refreshInstalled() }}
        />

        {filesOpen && (
          <div className="files-panel">
            <div className="files-head">
              <span>Workspace files</span>
              <button className="btn-ghost sm" onClick={() => refreshFiles()}>↻</button>
            </div>
            {files.length === 0 ? (
              <div className="files-empty">No files yet. Upload one, or ask the agent to create one.</div>
            ) : (
              files.map((f) => (
                <button className="file-row" key={f.name} onClick={() => downloadFile(token, activeId, f.name)}
                        title="Download">
                  <span className="fname">{f.name}</span>
                  <span className="fsize">{fmtSize(f.size)} ↓</span>
                </button>
              ))
            )}
          </div>
        )}

        {installedOpen && (
          <div className="files-panel">
            <div className="files-head">
              <span>Agent skills</span>
              <input ref={skillRef} type="file" accept=".zip" hidden onChange={onSkillPicked} />
              <button className="btn-ghost sm" onClick={() => skillRef.current?.click()} disabled={installing}>
                {installing ? '…' : '+ Install .zip'}
              </button>
            </div>
            {installed.length === 0 ? (
              <div className="files-empty">
                No skills installed. Upload a <code>SKILL.md</code> folder as a .zip;
                the agent can then load and run it.
              </div>
            ) : (
              installed.map((s) => (
                <div className="file-row static" key={s.name}>
                  <span className="fname">🧩 {s.name}{s.description ? ` — ${s.description}` : ''}</span>
                  <button className="sdel" title="Uninstall" onClick={() => removeInstalled(s.name)}>×</button>
                </div>
              ))
            )}
          </div>
        )}

        <div className="chat" ref={chatRef}>
          <div className="chat-inner">
            {!activeId ? (
              <div className="empty-chat">
                <h2>Welcome, {user.username}</h2>
                <p>Create a new session to start chatting with the agent.</p>
              </div>
            ) : messages.length === 0 ? (
              <div className="empty-chat">
                <h2>New session</h2>
                <p>Try: <code>create a file listing the planets, then show me its contents</code></p>
                <p>Or: <code>read /etc/passwd</code> to watch the sandbox block it.</p>
              </div>
            ) : (
              messages.map((m, i) => (
                <Message key={i} msg={m} hideTools={hideTools}
                         onDownload={(name) => downloadFile(token, activeId, name)}
                         streaming={m.role === 'assistant' && m._streaming && streaming} />
              ))
            )}
          </div>
        </div>

        {error && (
          <div className="composer" style={{ borderTop: 'none', paddingBottom: 0 }}>
            <div className="banner-error">
              <span>{error}</span>
              <button onClick={() => setError('')}>Dismiss</button>
            </div>
          </div>
        )}

        <div className="composer">
          {menuOpen && (
            <div className="skills-menu">
              {slashActive && <div className="skills-menu-hint">Skills — ↑↓ to move, ↵ to insert, esc to dismiss</div>}
              {menuSkills.map((s, i) => (
                <button className={'skill-item' + (i === Math.min(menuIndex, menuSkills.length - 1) ? ' active' : '')}
                        key={s.name}
                        onMouseEnter={() => setMenuIndex(i)}
                        onClick={() => pickSkill(s)}>
                  <span className="skill-name">/{s.name}</span>
                  {s.description && <span className="skill-desc">{s.description}</span>}
                </button>
              ))}
            </div>
          )}
          <div className="composer-box">
            {attached.length > 0 && (
              <div className="attach-row">
                {attached.map((f, i) => (
                  <span className="attach-chip" key={f.name + i}>
                    📎 {f.name}
                    <button className="attach-x" title="Remove"
                            onClick={() => setAttached((prev) => prev.filter((_, j) => j !== i))}>×</button>
                  </span>
                ))}
              </div>
            )}
            <input ref={fileRef} type="file" multiple hidden onChange={onFilesPicked} />
            <textarea
              ref={inputRef}
              rows={1} value={input}
              placeholder={activeId ? 'Message Floowpay AI…  (press / for skills)' : 'Create a session first'}
              disabled={!activeId || streaming}
              onChange={onInputChange} onKeyDown={onKeyDown}
            />
            <div className="composer-toolbar">
              <div className="composer-tools">
                <button className="tool-btn" title="Attach files or media"
                        onClick={() => fileRef.current?.click()}
                        disabled={!activeId || streaming || uploading}>
                  {uploading ? '…' : '📎'}
                </button>
                {skills.length > 0 && (
                  <button className={'tool-btn' + (skillsOpen ? ' on' : '')} title="Insert a saved prompt (skill)"
                          onClick={() => setSkillsOpen((o) => !o)} disabled={!activeId || streaming}>
                    ✨
                  </button>
                )}
                <select className="model-select mini" value={model} disabled={streaming}
                        onChange={(e) => changeModel(e.target.value)} title="Model">
                  {models.map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              </div>
              {streaming ? (
                <button className="btn-send-circle stop" onClick={stopTurn} title="Stop">■</button>
              ) : (
                <button className="btn-send-circle" onClick={send} title="Send"
                        disabled={!activeId || (!input.trim() && attached.length === 0)}>↑</button>
              )}
            </div>
          </div>
        </div>
      </div>
      )}
    </div>
  )
}

function fmtSize(n) {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}
