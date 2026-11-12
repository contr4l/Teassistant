import { useMemo } from 'react'
import katex from 'katex'
import 'katex/dist/katex.min.css'

type Segment = { kind: 'text'; value: string } | { kind: 'math'; value: string; display: boolean }

const DELIMITERS: { open: string; close: string; display: boolean }[] = [
  { open: '$$', close: '$$', display: true },
  { open: '\\[', close: '\\]', display: true },
  { open: '\\(', close: '\\)', display: false },
  { open: '$', close: '$', display: false },
]

/** Split model text into plain text and TeX segments. Unclosed delimiters stay as plain text; `\$` is a literal dollar. */
export function splitMath(source: string): Segment[] {
  const segments: Segment[] = []
  let text = ''
  let index = 0
  while (index < source.length) {
    if (source.startsWith('\\$', index)) { text += '$'; index += 2; continue }
    const delimiter = DELIMITERS.find((item) => source.startsWith(item.open, index))
    if (delimiter) {
      let end = index + delimiter.open.length
      while (end < source.length) {
        if (source.startsWith('\\$', end)) { end += 2; continue }
        if (source.startsWith(delimiter.close, end)) break
        end += 1
      }
      const body = source.slice(index + delimiter.open.length, end)
      if (end < source.length && body.trim()) {
        if (text) segments.push({ kind: 'text', value: text })
        text = ''
        segments.push({ kind: 'math', value: body, display: delimiter.display })
        index = end + delimiter.close.length
        continue
      }
    }
    text += source[index]
    index += 1
  }
  if (text) segments.push({ kind: 'text', value: text })
  return segments
}

const OPTIONS = {
  // trust:false blocks \href, \url, \includegraphics and \html* commands.
  trust: false,
  strict: 'ignore' as const,
  output: 'htmlAndMathml' as const,
  maxExpand: 1000,
  maxSize: 20,
  errorColor: '#b42318',
}

function renderTex(tex: string, display: boolean): string | null {
  try {
    // Exam stems use full-size fractions inline, as printed papers do; \textstyle in the source still overrides it.
    return katex.renderToString(display ? tex : `\\displaystyle ${tex}`, { ...OPTIONS, displayMode: display, throwOnError: true })
  } catch {
    try {
      // Parse errors render visibly in red, showing only the original source.
      return katex.renderToString(tex, { ...OPTIONS, displayMode: display, throwOnError: false })
    } catch {
      return null
    }
  }
}

/** Plain text with inline/display TeX rendered by KaTeX. Falls back to visible source when a formula cannot be rendered. */
export function MathText({ text, className }: { text: string; className?: string }) {
  const segments = useMemo(() => splitMath(text), [text])
  return <span className={className}>{segments.map((segment, index) => {
    if (segment.kind === 'text') return <span key={index}>{segment.value}</span>
    const html = renderTex(segment.value, segment.display)
    return html === null
      ? <code key={index} className="math-fallback" title="公式无法渲染，显示原文">{segment.value}</code>
      : <span key={index} className={segment.display ? 'math-display' : 'math-inline'} dangerouslySetInnerHTML={{ __html: html }} />
  })}</span>
}

/** Render a bare TeX string (no delimiters), e.g. structured formula nodes. */
export function MathBlock({ tex }: { tex: string }) {
  const html = useMemo(() => renderTex(tex, true), [tex])
  return html === null
    ? <code className="math-fallback" title="公式无法渲染，显示原文">{tex}</code>
    : <div className="math-display" dangerouslySetInnerHTML={{ __html: html }} />
}
