import ToolCard from './ToolCard.jsx'
import { renderMarkdown } from '../markdown.js'

// One chat turn. User turns are a flat bubble. Assistant turns render their
// tool cards (in arrival order) followed by markdown text with a model chip,
// plus a blinking cursor while still streaming.
export default function Message({ msg, streaming }) {
  if (msg.role === 'user') {
    return (
      <div className="msg">
        <div className="msg-role">You</div>
        <div className="bubble-user">{msg.text}</div>
        {msg.files && msg.files.length > 0 && (
          <div className="msg-files">
            {msg.files.map((f, i) => <span className="attach-chip static" key={i}>📎 {f.name}</span>)}
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="msg">
      <div className="msg-role">
        Assistant
        {msg.model && <span className="model-chip">{msg.model}</span>}
      </div>

      {(msg.tools || []).map((t) => <ToolCard key={t.id} tool={t} />)}

      {(msg.text || streaming) && (
        <div className="bubble-assistant">
          <span dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.text) }} />
          {streaming && <span className="cursor" />}
        </div>
      )}
    </div>
  )
}
