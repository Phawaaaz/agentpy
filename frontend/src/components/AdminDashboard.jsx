import { useEffect, useState } from 'react'
import {
  getAdminStats, adminCreateUser, adminSetRole, adminDeleteUser,
  getMcpServers, adminCreateMcp, adminDeleteMcp,
} from '../api.js'

const MCP_BLANK = { name: '', transport: 'http', url: '', command: '', args: '' }

// Read-mostly admin view: live per-user usage (sessions, messages, model
// calls, tokens, cost) with a global totals row, plus lightweight user
// management (create, promote/demote, delete). Admin-only; the backend also
// 403s non-admins, so this is defence in depth, not the only gate.
export default function AdminDashboard({ token, me, onClose }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState({ username: '', password: '', role: 'user' })
  const [mcp, setMcp] = useState([])
  const [mcpForm, setMcpForm] = useState(MCP_BLANK)

  async function refresh() {
    setError('')
    try {
      setData(await getAdminStats(token))
    } catch (e) { setError(e.message || 'Failed to load stats') }
    try {
      setMcp(await getMcpServers(token))
    } catch { /* MCP is optional; ignore */ }
  }

  useEffect(() => { refresh() /* eslint-disable-next-line */ }, [])

  async function createMcp(e) {
    e.preventDefault()
    const isUrl = mcpForm.transport !== 'stdio'
    if (!mcpForm.name.trim()) return
    if (isUrl && !mcpForm.url.trim()) { setError('That transport needs a URL'); return }
    if (!isUrl && !mcpForm.command.trim()) { setError('stdio needs a command'); return }
    setBusy(true); setError('')
    try {
      const payload = {
        name: mcpForm.name.trim(), transport: mcpForm.transport,
        url: mcpForm.url.trim(), command: mcpForm.command.trim(),
        args: mcpForm.args.trim() ? mcpForm.args.trim().split(/\s+/) : [],
      }
      const res = await adminCreateMcp(token, payload)
      if (!res.connected) setError(`Saved "${res.name}", but couldn't connect: ${res.error || 'unknown error'}`)
      setMcpForm(MCP_BLANK)
      await refresh()
    } catch (e) { setError(e.message || 'Could not add MCP server') }
    finally { setBusy(false) }
  }

  async function removeMcp(name) {
    if (!confirm(`Disconnect and remove MCP server "${name}"?`)) return
    setBusy(true); setError('')
    try {
      await adminDeleteMcp(token, name)
      await refresh()
    } catch (e) { setError(e.message || 'Could not remove MCP server') }
    finally { setBusy(false) }
  }

  async function createUser(e) {
    e.preventDefault()
    if (!form.username.trim() || !form.password) return
    setBusy(true); setError('')
    try {
      await adminCreateUser(token, { ...form, username: form.username.trim() })
      setForm({ username: '', password: '', role: 'user' })
      await refresh()
    } catch (e) { setError(e.message || 'Could not create user') }
    finally { setBusy(false) }
  }

  async function toggleRole(u) {
    setBusy(true); setError('')
    try {
      await adminSetRole(token, u.username, u.role === 'admin' ? 'user' : 'admin')
      await refresh()
    } catch (e) { setError(e.message || 'Could not change role') }
    finally { setBusy(false) }
  }

  async function removeUser(u) {
    if (!confirm(`Delete user "${u.username}" and all their sessions? This cannot be undone.`)) return
    setBusy(true); setError('')
    try {
      await adminDeleteUser(token, u.username)
      await refresh()
    } catch (e) { setError(e.message || 'Could not delete user') }
    finally { setBusy(false) }
  }

  const t = data?.totals

  return (
    <div className="admin">
      <div className="admin-head">
        <div>
          <h1>Admin</h1>
          <p className="admin-sub">Live usage & user management</p>
        </div>
        <div className="admin-head-actions">
          <button className="btn-ghost" onClick={refresh}>↻ Refresh</button>
          <button className="btn-ghost" onClick={onClose}>← Back to chat</button>
        </div>
      </div>

      {error && <div className="admin-error">{error}</div>}

      {t && (
        <div className="stat-row">
          <Stat label="Users" value={t.users} />
          <Stat label="Sessions" value={t.sessions} />
          <Stat label="Messages" value={t.messages} />
          <Stat label="Model calls" value={t.calls} />
          <Stat label="Total tokens" value={t.total_tokens.toLocaleString()} />
          <Stat label="Est. cost" value={`$${t.cost_usd.toFixed(4)}`} />
        </div>
      )}

      <div className="admin-card">
        <div className="admin-card-title">Users</div>
        <div className="table-scroll">
          <table className="admin-table">
            <thead>
              <tr>
                <th>User</th><th>Role</th>
                <th className="num">Sessions</th><th className="num">Messages</th>
                <th className="num">Calls</th><th className="num">Prompt</th>
                <th className="num">Completion</th><th className="num">Tokens</th>
                <th className="num">Cost</th><th className="actions-col">Actions</th>
              </tr>
            </thead>
            <tbody>
              {(data?.users || []).map((u) => (
                <tr key={u.username}>
                  <td className="uname-cell">{u.username}{u.username === me ? ' (you)' : ''}</td>
                  <td><span className={'role-pill ' + u.role}>{u.role}</span></td>
                  <td className="num">{u.sessions}</td>
                  <td className="num">{u.messages}</td>
                  <td className="num">{u.calls}</td>
                  <td className="num">{u.prompt_tokens.toLocaleString()}</td>
                  <td className="num">{u.completion_tokens.toLocaleString()}</td>
                  <td className="num strong">{u.total_tokens.toLocaleString()}</td>
                  <td className="num">${u.cost_usd.toFixed(4)}</td>
                  <td className="actions-col">
                    <button className="btn-mini" disabled={busy} onClick={() => toggleRole(u)}>
                      {u.role === 'admin' ? 'Make user' : 'Make admin'}
                    </button>
                    <button className="btn-mini danger" disabled={busy || u.username === me}
                            onClick={() => removeUser(u)}>Delete</button>
                  </td>
                </tr>
              ))}
              {t && (
                <tr className="totals-row">
                  <td>Totals</td><td></td>
                  <td className="num">{t.sessions}</td>
                  <td className="num">{t.messages}</td>
                  <td className="num">{t.calls}</td>
                  <td className="num">{t.prompt_tokens.toLocaleString()}</td>
                  <td className="num">{t.completion_tokens.toLocaleString()}</td>
                  <td className="num strong">{t.total_tokens.toLocaleString()}</td>
                  <td className="num">${t.cost_usd.toFixed(4)}</td>
                  <td></td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="admin-card">
        <div className="admin-card-title">MCP tool servers</div>
        <p className="admin-hint">
          Connect external <strong>MCP</strong> servers to give every session new tools.
          Their tools appear namespaced as <code>mcp__server__tool</code>. Org-wide and
          persisted — they reconnect on restart.
        </p>
        {mcp.length > 0 && (
          <div className="mcp-list">
            {mcp.map((s) => (
              <div className="mcp-row" key={s.name}>
                <div className="mcp-row-text">
                  <span className="mcp-row-name">
                    {s.name}
                    <span className={'mcp-dot ' + (s.connected ? 'ok' : 'down')}
                          title={s.connected ? 'connected' : 'not connected'} />
                    <span className="mcp-transport">{s.transport}</span>
                  </span>
                  <span className="mcp-row-target">{s.url || s.command}</span>
                  {s.connected
                    ? <span className="mcp-row-tools">{s.tools.length} tool{s.tools.length !== 1 ? 's' : ''}: {s.tools.join(', ')}</span>
                    : <span className="mcp-row-err">{s.error || 'not connected'}</span>}
                </div>
                <button className="btn-mini danger" disabled={busy}
                        onClick={() => removeMcp(s.name)}>Remove</button>
              </div>
            ))}
          </div>
        )}
        <form className="new-mcp-form" onSubmit={createMcp}>
          <div className="mcp-form-row">
            <input placeholder="name (e.g. search)" value={mcpForm.name}
                   onChange={(e) => setMcpForm({ ...mcpForm, name: e.target.value })} />
            <select value={mcpForm.transport}
                    onChange={(e) => setMcpForm({ ...mcpForm, transport: e.target.value })}>
              <option value="http">http</option>
              <option value="sse">sse</option>
              <option value="stdio">stdio</option>
            </select>
          </div>
          {mcpForm.transport === 'stdio' ? (
            <div className="mcp-form-row">
              <input placeholder="command (e.g. npx)" value={mcpForm.command}
                     onChange={(e) => setMcpForm({ ...mcpForm, command: e.target.value })} />
              <input placeholder="args (space-separated, e.g. -y @modelcontextprotocol/server-filesystem .)"
                     value={mcpForm.args}
                     onChange={(e) => setMcpForm({ ...mcpForm, args: e.target.value })} />
            </div>
          ) : (
            <input placeholder="server URL (e.g. https://example.com/mcp)" value={mcpForm.url}
                   onChange={(e) => setMcpForm({ ...mcpForm, url: e.target.value })} />
          )}
          <button className="btn-primary compact" disabled={busy || !mcpForm.name.trim()}>
            {busy ? 'Connecting…' : 'Add & connect'}
          </button>
        </form>
      </div>

      <div className="admin-card">
        <div className="admin-card-title">Add user</div>
        <form className="new-user-form" onSubmit={createUser}>
          <input placeholder="username" value={form.username}
                 onChange={(e) => setForm({ ...form, username: e.target.value })} />
          <input placeholder="password" type="password" value={form.password}
                 onChange={(e) => setForm({ ...form, password: e.target.value })} />
          <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
            <option value="user">user</option>
            <option value="admin">admin</option>
          </select>
          <button className="btn-primary compact" disabled={busy || !form.username.trim() || !form.password}>
            Create
          </button>
        </form>
      </div>
    </div>
  )
}

function Stat({ label, value }) {
  return (
    <div className="stat">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  )
}
