// Tiny API client for the demo backend.
//
// The one non-obvious piece is streamTurn(): the SSE endpoint is a POST with a
// JSON body and a Bearer header, which EventSource can't do — so we read the
// response body with fetch + a stream reader and parse the `event:`/`data:`
// frames by hand, invoking onEvent(type, data) for each.

// API origin. In dev the Vite server (:5173) and the API (:8000) differ, so
// default to localhost:8000. In a production build we default to same-origin
// ('') so the app calls whatever host served it — a reverse proxy (nginx)
// forwards the API paths to the backend. Override with VITE_API_BASE.
const BASE = import.meta.env.VITE_API_BASE ?? (import.meta.env.DEV ? 'http://localhost:8000' : '')

function authHeaders(token) {
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function jsonOrThrow(res) {
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).detail || detail } catch { /* ignore */ }
    const err = new Error(detail)
    err.status = res.status
    throw err
  }
  return res.status === 204 ? null : res.json()
}

export async function login(username, password) {
  const res = await fetch(`${BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  return jsonOrThrow(res)
}

export async function getModels(token) {
  return jsonOrThrow(await fetch(`${BASE}/models`, { headers: authHeaders(token) }))
}

export async function listSessions(token) {
  return jsonOrThrow(await fetch(`${BASE}/sessions`, { headers: authHeaders(token) }))
}

export async function createSession(token, model) {
  return jsonOrThrow(await fetch(`${BASE}/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders(token) },
    body: JSON.stringify({ model }),
  }))
}

export async function deleteSession(token, sid) {
  return jsonOrThrow(await fetch(`${BASE}/sessions/${sid}`, {
    method: 'DELETE',
    headers: authHeaders(token),
  }))
}

// Upload one or more files into the session's workspace. Let the browser set
// the multipart Content-Type (with its boundary) — don't set it by hand.
export async function uploadFiles(token, sid, fileList) {
  const form = new FormData()
  for (const f of fileList) form.append('files', f)
  return jsonOrThrow(await fetch(`${BASE}/sessions/${sid}/files`, {
    method: 'POST',
    headers: authHeaders(token),
    body: form,
  }))
}

// List the files in a session's workspace (uploaded + agent-created).
export async function listFiles(token, sid) {
  return jsonOrThrow(await fetch(`${BASE}/sessions/${sid}/files`, { headers: authHeaders(token) }))
}

// Fetch a workspace file (with auth) and trigger a browser download.
export async function downloadFile(token, sid, name) {
  const res = await fetch(`${BASE}/sessions/${sid}/files/${encodeURIComponent(name)}`, {
    headers: authHeaders(token),
  })
  if (!res.ok) throw new Error('download failed')
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = name
  document.body.appendChild(a); a.click(); a.remove()
  URL.revokeObjectURL(url)
}

// Ask the server to stop the session's currently running turn.
export async function cancelTurn(token, sid) {
  await fetch(`${BASE}/sessions/${sid}/cancel`, { method: 'POST', headers: authHeaders(token) })
}

export async function getMessages(token, sid) {
  return jsonOrThrow(await fetch(`${BASE}/sessions/${sid}/messages`, {
    headers: authHeaders(token),
  }))
}

// --- admin (role=admin only) ---

export async function getAdminStats(token) {
  return jsonOrThrow(await fetch(`${BASE}/admin/stats`, { headers: authHeaders(token) }))
}

export async function adminCreateUser(token, { username, password, role }) {
  return jsonOrThrow(await fetch(`${BASE}/admin/users`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders(token) },
    body: JSON.stringify({ username, password, role }),
  }))
}

export async function adminSetRole(token, username, role) {
  return jsonOrThrow(await fetch(`${BASE}/admin/users/${username}/role`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...authHeaders(token) },
    body: JSON.stringify({ role }),
  }))
}

export async function adminDeleteUser(token, username) {
  return jsonOrThrow(await fetch(`${BASE}/admin/users/${username}`, {
    method: 'DELETE',
    headers: authHeaders(token),
  }))
}

// --- MCP tool servers (admin) ---

export async function getMcpServers(token) {
  return jsonOrThrow(await fetch(`${BASE}/admin/mcp`, { headers: authHeaders(token) }))
}

export async function adminCreateMcp(token, server) {
  return jsonOrThrow(await fetch(`${BASE}/admin/mcp`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders(token) },
    body: JSON.stringify(server),
  }))
}

export async function adminDeleteMcp(token, name) {
  return jsonOrThrow(await fetch(`${BASE}/admin/mcp/${encodeURIComponent(name)}`, {
    method: 'DELETE',
    headers: authHeaders(token),
  }))
}

// Stream a turn. Calls onEvent(type, data) for every SSE frame; returns a
// controller with abort(). Resolves the returned promise when the stream ends.
export function streamTurn(token, sid, message, model, onEvent) {
  const ctrl = new AbortController()
  const done = (async () => {
    let res
    try {
      res = await fetch(`${BASE}/sessions/${sid}/messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders(token) },
        body: JSON.stringify({ message, model }),
        signal: ctrl.signal,
      })
    } catch (e) {
      if (ctrl.signal.aborted) return
      onEvent('error', { message: 'connection lost' })
      return
    }
    if (!res.ok || !res.body) {
      onEvent('error', { message: `server returned ${res.status}` })
      return
    }
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    try {
      while (true) {
        const { value, done: streamDone } = await reader.read()
        if (streamDone) break
        buf += decoder.decode(value, { stream: true })
        // SSE frames are separated by a blank line.
        let sep
        while ((sep = buf.indexOf('\n\n')) !== -1) {
          const frame = buf.slice(0, sep)
          buf = buf.slice(sep + 2)
          let ev = 'message'
          const dataLines = []
          for (const line of frame.split('\n')) {
            if (line.startsWith('event:')) ev = line.slice(6).trim()
            else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
          }
          if (dataLines.length) {
            let data = {}
            try { data = JSON.parse(dataLines.join('\n')) } catch { /* ignore */ }
            onEvent(ev, data)
          }
        }
      }
    } catch (e) {
      if (!ctrl.signal.aborted) onEvent('error', { message: 'connection lost' })
    }
  })()
  return { abort: () => ctrl.abort(), done }
}
