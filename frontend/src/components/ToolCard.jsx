import { useState } from 'react'

// A single tool call. Starts as a one-line collapsed row with a spinner;
// fills in when the result arrives. Blocked/error results turn the card red
// with a shield label. Click the header to expand input + output.
export default function ToolCard({ tool }) {
  const [open, setOpen] = useState(false)
  const { name, input, output, blocked, error, running } = tool

  const cls = blocked ? 'blocked' : error ? 'error' : ''
  const status = running
    ? <span className="tool-status running">running</span>
    : blocked
      ? <span className="tool-status blocked">BLOCKED</span>
      : error
        ? <span className="tool-status error">error</span>
        : <span className="tool-status ok">done</span>

  const summary = summarize(name, input)

  return (
    <div className={'tool-card ' + cls}>
      <div className="tool-head" onClick={() => setOpen((o) => !o)}>
        {running ? <div className="spinner" /> : null}
        <span className="tname">{name}</span>
        <span className="tsummary">{summary}</span>
        {status}
        <span className="chev">{open ? '▲' : '▼'}</span>
      </div>

      {open && (
        <div className="tool-body">
          <div className="label">Input</div>
          <pre>{JSON.stringify(input, null, 2)}</pre>
          {output !== undefined && output !== null && (
            <>
              <div className="label">Output</div>
              <pre className={blocked ? 'out-blocked' : ''}>{output}</pre>
            </>
          )}
          {blocked && (
            <div className="shield-note">🛡 Blocked by the workspace sandbox — the agent cannot escape its per-session directory.</div>
          )}
        </div>
      )}
    </div>
  )
}

function summarize(name, input) {
  if (!input || typeof input !== 'object') return ''
  if (input.path) return input.path
  if (input.command) return input.command
  if (input.query) return input.query
  const keys = Object.keys(input)
  return keys.length ? `${keys[0]}: ${String(input[keys[0]]).slice(0, 60)}` : ''
}
