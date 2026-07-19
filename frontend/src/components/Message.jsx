import { useState } from 'react'
import ToolCard from './ToolCard.jsx'
import { renderMarkdown } from '../markdown.js'

// One chat turn. User turns are a flat bubble. Assistant turns render their
// tool cards followed by markdown text with a model chip.
//
// When `hideTools` (focus mode) is on, the individual tool cards are replaced
// by one quiet line: a "Thinking…" indicator while the turn is still working,
// then a collapsed "N steps" summary you can click to expand. Blocked/error
// steps still surface so failures and the sandbox block stay visible.
export default function Message({ msg, streaming, hideTools }) {
  const [showSteps, setShowSteps] = useState(false)

  if (msg.role === 'user') {
    return (
      <div className="msg">
        <div className="msg-role">You</div>
        <div className="bubble-user">{msg.text}</div>
      </div>
    )
  }

  const tools = msg.tools || []
  const hasFlag = tools.some((t) => t.blocked || t.error)  // always worth showing
  const working = streaming && !msg.text  // tools running, no answer text yet
  const collapse = hideTools && !hasFlag && !showSteps

  return (
    <div className="msg">
      <div className="msg-role">
        Assistant
        {msg.model && <span className="model-chip">{msg.model}</span>}
      </div>

      {collapse ? (
        (working || tools.length > 0) && (
          <button className="steps-line" onClick={() => !working && setShowSteps(true)}
                  disabled={working} title={working ? '' : 'Show steps'}>
            {working ? (
              <><span className="spinner" /> Thinking…</>
            ) : (
              <>🔧 {tools.length} step{tools.length !== 1 ? 's' : ''} <span className="chev">▾</span></>
            )}
          </button>
        )
      ) : (
        tools.map((t) => <ToolCard key={t.id} tool={t} />)
      )}

      {(msg.text || (streaming && !working)) && (
        <div className="bubble-assistant">
          <span dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.text) }} />
          {streaming && <span className="cursor" />}
        </div>
      )}
    </div>
  )
}
