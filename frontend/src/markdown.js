import { marked } from 'marked'
import hljs from 'highlight.js'

// Markdown -> HTML with syntax-highlighted code blocks. Used for assistant
// text. The backend content is our own agent's output (not third-party), but
// marked still escapes HTML by default, which is what we want.
marked.setOptions({
  breaks: true,
  highlight(code, lang) {
    if (lang && hljs.getLanguage(lang)) {
      try { return hljs.highlight(code, { language: lang }).value } catch { /* ignore */ }
    }
    try { return hljs.highlightAuto(code).value } catch { return code }
  },
})

export function renderMarkdown(text) {
  return marked.parse(text || '')
}
