export default function TopBar({ models, model, onModelChange, disabled, sandboxOn, title }) {
  return (
    <div className="topbar">
      <div className="title">{title}</div>

      <div className={'sandbox-badge ' + (sandboxOn ? 'on' : 'off')}>
        <span className="glyph">{sandboxOn ? '🛡' : '○'}</span>
        Sandbox {sandboxOn ? 'ON' : 'OFF'}
      </div>

      <select className="model-select" value={model} disabled={disabled}
              onChange={(e) => onModelChange(e.target.value)}>
        {models.map((m) => <option key={m} value={m}>{m}</option>)}
      </select>
    </div>
  )
}
