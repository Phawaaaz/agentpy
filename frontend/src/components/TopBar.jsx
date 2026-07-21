export default function TopBar({ models, model, onModelChange, disabled, sandboxOn, title,
                                hideTools, onToggleHideTools,
                                fileCount, filesOpen, onToggleFiles,
                                skillCount, skillsOpen, onToggleSkills }) {
  return (
    <div className="topbar">
      <div className="title">{title}</div>

      <button className={'focus-toggle' + (skillsOpen ? ' on' : '')} onClick={onToggleSkills}
              title="Install and manage agent skills (SKILL.md folders the agent can run)">
        🧩 Skills{skillCount ? ` (${skillCount})` : ''}
      </button>

      <button className={'focus-toggle' + (filesOpen ? ' on' : '')} onClick={onToggleFiles}
              title="Files in this session's workspace">
        📁 Files{fileCount ? ` (${fileCount})` : ''}
      </button>

      <button className={'focus-toggle' + (hideTools ? ' on' : '')}
              onClick={onToggleHideTools}
              title={hideTools ? 'Showing a compact "thinking" line — click to show every tool step'
                               : 'Show each tool step — click to collapse into a thinking line'}>
        {hideTools ? '👁 Steps hidden' : '🔧 Steps shown'}
      </button>

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
