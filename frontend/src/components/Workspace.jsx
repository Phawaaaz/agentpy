import { useEffect, useRef, useState } from 'react'
import Sidebar from './Sidebar.jsx'
import TopBar from './TopBar.jsx'
import Message from './Message.jsx'
import AdminDashboard from './AdminDashboard.jsx'
import {
  getModels, listSessions, createSession, deleteSession, getMessages, streamTurn, uploadFiles,
} from '../api.js'

const LAST_SID_KEY = 'harness_demo_last_sid'

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
  const isAdmin = user.role === 'admin'

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
      } else if (ev === 'error') {
        setError(data.message === 'connection lost'
          ? 'Connection lost — the stream was interrupted. Retry your message.'
          : (data.message || 'Stream error'))
      }
    }

    const ctrl = streamTurn(token, activeId, shown + note, model, onEvent)
    streamRef.current = ctrl
    await ctrl.done
    streamRef.current = null
    setStreaming(false)
    patchAssistant((a) => { const { _streaming, ...rest } = a; return rest })
  }

  // auto-scroll to the newest content
  useEffect(() => {
    if (chatRef.current) chatRef.current.scrollTop = chatRef.current.scrollHeight
  }, [messages, streaming])

  function onKeyDown(e) {
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
        />

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
          <div className="composer-inner">
            <input ref={fileRef} type="file" multiple hidden onChange={onFilesPicked} />
            <button className="btn-attach" title="Upload files or media to this session"
                    onClick={() => fileRef.current?.click()}
                    disabled={!activeId || streaming || uploading}>
              {uploading ? '…' : '📎'}
            </button>
            <textarea
              ref={inputRef}
              rows={1} value={input} placeholder={activeId ? 'Message the agent…' : 'Create a session first'}
              disabled={!activeId || streaming}
              onChange={(e) => setInput(e.target.value)} onKeyDown={onKeyDown}
            />
            <button className="btn-send" onClick={send}
                    disabled={!activeId || streaming || (!input.trim() && attached.length === 0)}>
              {streaming ? '…' : 'Send'}
            </button>
          </div>
        </div>
      </div>
      )}
    </div>
  )
}
