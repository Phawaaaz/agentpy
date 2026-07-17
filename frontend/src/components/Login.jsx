import { useState } from 'react'
import { login } from '../api.js'

export default function Login({ onLogin }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e) {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      const auth = await login(username.trim(), password)
      onLogin(auth)
    } catch (err) {
      setError(err.message || 'Login failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-screen">
      <form className="login-card" onSubmit={submit}>
        <h1>Agent Harness</h1>
        <p className="sub">Sign in to your workspace</p>

        {error && <div className="login-error">{error}</div>}

        <div className="field">
          <label htmlFor="u">Username</label>
          <input id="u" autoFocus value={username}
                 onChange={(e) => setUsername(e.target.value)}
                 placeholder="alice" autoComplete="username" />
        </div>
        <div className="field">
          <label htmlFor="p">Password</label>
          <input id="p" type="password" value={password}
                 onChange={(e) => setPassword(e.target.value)}
                 placeholder="••••••••" autoComplete="current-password" />
        </div>

        <button className="btn-primary" disabled={busy || !username || !password}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>

        <p className="login-hint">Demo accounts: alice / bob</p>
      </form>
    </div>
  )
}
