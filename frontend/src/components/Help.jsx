// A getting-started overlay for new teammates. Opened from the "?" in the top
// bar; closed by the ×, the backdrop, or Esc.
import { useEffect } from 'react'

export default function Help({ onClose }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="help-backdrop" onClick={onClose}>
      <div className="help-card" onClick={(e) => e.stopPropagation()}>
        <div className="help-head">
          <h2>Getting started with Floowpay AI</h2>
          <button className="help-x" onClick={onClose} title="Close">×</button>
        </div>

        <p className="help-lead">
          Floowpay AI is an agent that does real work — it reads and writes files,
          runs commands in a sandbox, and can use tools you connect. Just tell it
          what you want in plain language.
        </p>

        <div className="help-grid">
          <HelpItem icon="💬" title="Chat">
            Type a request and press Enter. Ask it to draft, summarize, analyze a
            file, write code, or run a task. It works in steps and shows you what
            it did.
          </HelpItem>
          <HelpItem icon="📎" title="Attach files">
            Use the 📎 button to upload files (or images) into the session. The
            agent can read them; drop in a screenshot and ask about it.
          </HelpItem>
          <HelpItem icon="📄" title="Get files back">
            Files the agent creates appear as download chips under its reply —
            click to download. The 📁 Files panel lists everything in the session.
          </HelpItem>
          <HelpItem icon="/" title="Saved prompts">
            Press <code>/</code> in the message box to insert a saved prompt
            (skill). Your admin curates these; the ✨ button opens the same menu.
          </HelpItem>
          <HelpItem icon="🧩" title="Add a skill">
            The 🧩 Skills panel installs a SKILL.md folder (as a .zip). The agent
            can then load and run it automatically when it's relevant.
          </HelpItem>
          <HelpItem icon="🐙" title="Connect GitHub">
            If enabled, click Connect GitHub to authorize — the agent can then
            work with your repos, issues, and PRs as you.
          </HelpItem>
          <HelpItem icon="🛡" title="Safe by design">
            The agent is sandboxed to its own workspace — it can't touch the host
            or other people's sessions. Your chats are private to you.
          </HelpItem>
          <HelpItem icon="🔀" title="Switch model">
            Pick the model from the composer any time. Your admin sets which are
            available.
          </HelpItem>
        </div>

        <p className="help-foot">Tip: keep requests specific, and ask follow-ups — it remembers the conversation.</p>
      </div>
    </div>
  )
}

function HelpItem({ icon, title, children }) {
  return (
    <div className="help-item">
      <div className="help-item-title"><span className="help-ico">{icon}</span> {title}</div>
      <div className="help-item-body">{children}</div>
    </div>
  )
}
