import { useState } from 'react'
import Login from './components/Login.jsx'
import Workspace from './components/Workspace.jsx'

const AUTH_KEY = 'harness_demo_auth'

function loadAuth() {
  try {
    const raw = localStorage.getItem(AUTH_KEY)
    return raw ? JSON.parse(raw) : null
  } catch { return null }
}

export default function App() {
  // Persist auth so closing/reopening the tab resumes the session (demo step 7).
  const [auth, setAuth] = useState(loadAuth)

  function onLogin(a) {
    localStorage.setItem(AUTH_KEY, JSON.stringify(a))
    setAuth(a)
  }

  function onLogout() {
    localStorage.removeItem(AUTH_KEY)
    localStorage.removeItem('harness_demo_last_sid')
    setAuth(null)
  }

  return auth
    ? <Workspace auth={auth} onLogout={onLogout} />
    : <Login onLogin={onLogin} />
}
