import { useMemo } from 'react'

/** Sanitizes server-rendered question HTML before it enters the isolated preview frame. */
export function sanitizeHtmlForPreview(markup: string) {
  const parsed = new DOMParser().parseFromString(markup, 'text/html')
  const htmlNamespace = 'http://www.w3.org/1999/xhtml'
  const mathNamespace = 'http://www.w3.org/1998/Math/MathML'
  const allowedHtml = new Set(['p', 'div', 'span', 'br', 'b', 'strong', 'i', 'em', 'ul', 'ol', 'li', 'table', 'thead', 'tbody', 'tr', 'td', 'th', 'sub', 'sup', 'img'])
  const allowedMath = new Set(['math', 'mrow', 'mi', 'mn', 'mo', 'mtext', 'mfrac', 'msqrt', 'mroot', 'msup', 'msub', 'msubsup', 'munder', 'mover', 'munderover'])
  const allowedMathAttributes: Record<string, Record<string, Set<string>>> = {
    math: { xmlns: new Set([mathNamespace]) },
    mi: { mathvariant: new Set(['normal']) },
    mn: { mathvariant: new Set(['normal']) },
    mo: { mathvariant: new Set(['normal']), fence: new Set(['true', 'false']), stretchy: new Set(['true', 'false']) },
    mtext: { mathvariant: new Set(['normal']) },
    mover: { accent: new Set(['true', 'false']) },
    munder: { accentunder: new Set(['true', 'false']) },
  }
  const mathIsSupported = (element: Element): boolean => {
    const tag = element.localName.toLowerCase()
    if (element.namespaceURI !== mathNamespace || !allowedMath.has(tag)) return false
    const tagAttrs = allowedMathAttributes[tag] ?? {}
    if (Array.from(element.attributes).some((attribute) => !tagAttrs[attribute.name]?.has(attribute.value))) return false
    return Array.from(element.children).every(mathIsSupported)
  }
  for (const math of Array.from(parsed.body.getElementsByTagNameNS(mathNamespace, 'math'))) {
    if (math.isConnected && !mathIsSupported(math)) {
      const marker = parsed.createElement('span')
      marker.textContent = '［此处公式无法在网页预览，请以导出文件为准］'
      math.replaceWith(marker)
    }
  }
  const walk = (parent: ParentNode) => {
    for (const child of Array.from(parent.childNodes)) {
      if (child.nodeType === Node.TEXT_NODE) continue
      if (!(child instanceof Element)) { child.remove(); continue }
      const tag = child.localName.toLowerCase()
      const isMath = child.namespaceURI === mathNamespace
      if (isMath ? !allowedMath.has(tag) : child.namespaceURI !== htmlNamespace || !allowedHtml.has(tag)) {
        child.replaceWith(document.createTextNode(isMath ? '［含暂不支持的公式结构］' : child.textContent ?? ''))
        continue
      }
      const safeAttrs: Record<string, string> = {}
      if (isMath) {
        let valid = true
        const tagAttrs = allowedMathAttributes[tag] ?? {}
        for (const attribute of Array.from(child.attributes)) {
          const values = tagAttrs[attribute.name]
          if (!values || !values.has(attribute.value)) valid = false
          else safeAttrs[attribute.name] = attribute.value
        }
        if (!valid) {
          child.replaceWith(document.createTextNode('［含暂不支持的公式属性］'))
          continue
        }
      } else if (tag === 'img') {
        const src = child.getAttribute('src') ?? ''
        const dataImage = /^data:image\/(?:png|jpeg|webp|gif);base64,[A-Za-z0-9+/=\r\n]+$/i.test(src)
        if (!dataImage) { child.replaceWith(document.createTextNode('[配图无法安全预览]')); continue }
        safeAttrs.src = src.replace(/\s+/g, '')
        safeAttrs.alt = child.getAttribute('alt') ?? '题目配图'
        for (const dimension of ['width', 'height']) {
          const value = child.getAttribute(dimension) ?? ''
          if (/^[1-9]\d{0,3}$/.test(value) && Number(value) <= 4096) safeAttrs[dimension] = value
        }
      }
      for (const attribute of Array.from(child.attributes)) child.removeAttribute(attribute.name)
      for (const [name, value] of Object.entries(safeAttrs)) child.setAttribute(name, value)
      walk(child)
    }
  }
  walk(parsed.body)
  return parsed.body.innerHTML
}

export function SanitizedPreview({ html, title = '题目预览' }: { html: string; title?: string }) {
  const content = useMemo(() => sanitizeHtmlForPreview(html), [html])
  const srcDoc = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'"><style>html,body{font:16px/1.8 'Noto Sans SC',sans-serif;color:#25332f;margin:0;overflow-wrap:anywhere}body{padding:8px;overflow:auto}img{max-width:100%;height:auto;vertical-align:middle}table{border-collapse:collapse;width:100%}td,th{border:1px solid #d9dfdb;padding:6px}p{margin:.4em 0}</style></head><body>${content}</body></html>`
  const resizeToContent = (frame: HTMLIFrameElement) => {
    const doc = frame.contentDocument
    if (!doc) return
    const measure = () => {
      const height = Math.max(doc.body.scrollHeight, doc.documentElement.scrollHeight) + 4
      frame.style.height = `${Math.min(Math.max(height, 96), 520)}px`
    }
    requestAnimationFrame(measure)
    for (const image of Array.from(doc.images)) image.addEventListener('load', measure, { once: true })
  }
  return <iframe className="sanitized-preview-frame" title={title} sandbox="allow-same-origin" srcDoc={srcDoc} onLoad={(event) => resizeToContent(event.currentTarget)} />
}
