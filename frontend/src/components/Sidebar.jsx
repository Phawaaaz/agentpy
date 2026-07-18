export default function Sidebar({ user, sessions, activeId, onSelect, onNew, onDelete, onLogout,
                                  isAdmin, onAdmin, adminActive }) {
  const initial = (user.username || '?')[0].toUpperCase()

  return (
    <aside className="sidebar">
      <div className="sidebar-head">
        <div className="brand">Agent Harness<span className="dot">.</span></div>
      </div>

      <button className="btn-new" onClick={onNew}>+ New session</button>

      <div className="session-list">
        {sessions.length === 0 ? (
          <div className="session-empty">
            No sessions yet.<br />Start one to begin chatting.
          </div>
        ) : (
          sessions.map((s) => (
            <div key={s.session_id}
                 className={'session-item' + (s.session_id === activeId ? ' active' : '')}
                 onClick={() => onSelect(s.session_id)}>
              <div style={{ minWidth: 0 }}>
                <div className="sname">Session {shortId(s.session_id)}</div>
                <div className="smodel">{s.model}</div>
              </div>
              <button className="sdel" title="Delete session"
                      onClick={(e) => { e.stopPropagation(); onDelete(s.session_id) }}>×</button>
            </div>
          ))
        )}
      </div>

      {isAdmin && (
        <button className={'btn-admin' + (adminActive ? ' active' : '')} onClick={onAdmin}>
          ⚙ Admin dashboard
        </button>
      )}

      <div className="user-badge">
        <div className="avatar">{initial}</div>
        <div className="uinfo">
          <div className="uname">{user.username}</div>
          <div className="urole">{user.role}</div>
        </div>
        <button className="btn-logout" onClick={onLogout}>Log out</button>
      </div>
    </aside>
  )
}

function shortId(id) {
  // "20260717-100715-446863" -> "100715"
  const parts = id.split('-')
  return parts.length >= 2 ? parts[1] : id.slice(-6)
}
