export default function TopBar({ sandboxOn, title,
                                hideTools, onToggleHideTools,
                                fileCount, filesOpen, onToggleFiles,
                                skillCount, skillsOpen, onToggleSkills,
                                github, onConnectGithub, onUnlinkGithub, onHelp }) {
  return (
    <div className="topbar">
      <div className="title">{title}</div>

      <button className="focus-toggle" onClick={onHelp} title="How to use Floowpay AI">
        ? Help
      </button>

      {github && github.available && (
        github.connected ? (
          <button className="focus-toggle on" onClick={onUnlinkGithub}
                  title={`Connected as @${github.login} — click to disconnect`}>
            🐙 @{github.login}
          </button>
        ) : (
          <button className="focus-toggle" onClick={onConnectGithub}
                  title="Authorize Floowpay AI to act on your GitHub">
            🐙 Connect GitHub
          </button>
        )
      )}

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
    </div>
  )
}
