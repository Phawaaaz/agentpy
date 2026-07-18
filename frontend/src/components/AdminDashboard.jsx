import { useEffect, useState } from 'react'
import { getAdminStats, adminCreateUser, adminSetRole, adminDeleteUser } from '../api.js'

// Read-mostly admin view: live per-user usage (sessions, messages, model
// calls, tokens, cost) with a global totals row, plus lightweight user
// management (create, promote/demote, delete). Admin-only; the backend also
// 403s non-admins, so this is defence in depth, not the only gate.
export default function AdminDashboard({ token, me, onClose }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState({ username: '', password: '', role: 'user' })

  async function refresh() {
    setError('')
    try {
      setData(await getAdminStats(token))
    } catch (e) { setError(e.message || 'Failed to load stats') }
  }

  useEffect(() => { refresh() /* eslint-disable-next-line */ }, [])

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
