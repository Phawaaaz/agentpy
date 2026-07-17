import { useEffect, useRef, useState } from 'react'
import Sidebar from './Sidebar.jsx'
import TopBar from './TopBar.jsx'
import Message from './Message.jsx'
import AdminDashboard from './AdminDashboard.jsx'
import {
  getModels, listSessions, createSession, deleteSession, getMessages, streamTurn,
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
  const isAdmin = user.role === 'admin'

  const chatRef = useRef(null)
  const streamRef = useRef(null)

  // --- initial load: models + sessions (+ resume last session) ---
  useEffect(() => {
    (async () => {
      try {
        const m = await getModels(token)
        setModels(m.models)
        setModel(m.default || m.models[0])
      } catch (e) { handleAuthError(e) }
      await refreshSessions(true)
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

  async function newSession() {
    if (streaming) return
    try {
      const s = await createSession(token, model)
      setSessions((prev) => [...prev, s])
      setActiveId(s.session_id)
      localStorage.setItem(LAST_SID_KEY, s.session_id)
      setModel(s.model)
      setMessages([])
      setError('')
    } catch (e) { handleAuthError(e) }
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

  // --- send a turn and consume the SSE stream ---
  async function send() {
    const text = input.trim()
    if (!text || streaming || !activeId) return
    setInput('')
    setError('')
    setStreaming(true)

    setMessages((prev) => [
      ...prev,
      { role: 'user', text, tools: [] },
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

    const ctrl = streamTurn(token, activeId, text, model, onEvent)
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
                <Message key={i} msg={m}
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
          <div className="composer-inner">
            <textarea
              rows={1} value={input} placeholder={activeId ? 'Message the agent…' : 'Create a session first'}
              disabled={!activeId || streaming}
              onChange={(e) => setInput(e.target.value)} onKeyDown={onKeyDown}
            />
            <button className="btn-send" onClick={send} disabled={!activeId || streaming || !input.trim()}>
              {streaming ? '…' : 'Send'}
            </button>
          </div>
        </div>
      </div>
      )}
    </div>
  )
}
