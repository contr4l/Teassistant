import { useCallback, useEffect, useRef, useState } from 'react'

export type View = 'home' | 'students' | 'exam' | 'practice' | 'me' | 'admin'

/**
 * App location kept in the URL hash so the phone back button, refresh and copied links work.
 *   #/                         工作台
 *   #/exam?s=<student>         考试列表        #/exam/<exam>?s=…  考试详情     #/exam/new?s=…  新建考试
 *   #/students                 学生列表        #/students/<id>    学生详情     #/students/new  新增学生
 *   #/practice?s=…&e=<exam>    组卷设置        #/practice/result?s=…  练习卷结果
 *   #/me                       我的            #/admin            管理设置
 */
export type Route = { view: View; item?: string; s?: string; e?: string }

const VIEWS: View[] = ['home', 'students', 'exam', 'practice', 'me', 'admin']

export function parseRoute(hash: string): Route {
  const raw = hash.replace(/^#\/?/, '')
  const [path, query = ''] = raw.split('?')
  const [first = '', second] = path.split('/').map((part) => decodeURIComponent(part))
  const view = (VIEWS as string[]).includes(first) ? first as View : 'home'
  const params = new URLSearchParams(query)
  const route: Route = { view }
  if (second && view !== 'home' && view !== 'me' && view !== 'admin') route.item = second
  const s = params.get('s'); if (s) route.s = s
  const e = params.get('e'); if (e) route.e = e
  return route
}

export function formatRoute(route: Route): string {
  const path = route.view === 'home' ? '' : route.view + (route.item ? `/${encodeURIComponent(route.item)}` : '')
  const params = new URLSearchParams()
  if (route.s) params.set('s', route.s)
  if (route.e) params.set('e', route.e)
  const query = params.toString()
  return `#/${path}${query ? `?${query}` : ''}`
}

type HistoryState = { teaDepth?: number } | null

let navigationBlocker: (() => boolean) | null = null
// URL of the screen currently rendered; updated by in-app navigation and by accepted back/forward steps.
let lastHash = ''
/** Register a check run before the browser/phone back button leaves the current screen; return false to stay. */
export function setNavigationBlocker(blocker: (() => boolean) | null) { navigationBlocker = blocker }
const currentDepth = () => Number((window.history.state as HistoryState)?.teaDepth ?? 0)

export function useHashRoute() {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash))
  useEffect(() => {
    lastHash = window.location.hash
    let skipNext = false
    const onChange = () => {
      const hash = window.location.hash
      if (skipNext) { skipNext = false; lastHash = hash; return }
      // Same URL means a dialog step was popped (useBackToClose); that is not leaving the page.
      if (hash !== lastHash && navigationBlocker && !navigationBlocker()) {
        // Teacher chose to stay: step forward again to where they were, without re-rendering.
        skipNext = true
        window.history.forward()
        return
      }
      lastHash = hash
      setRoute(parseRoute(hash))
    }
    // popstate alone: browsers fire it for back/forward and for fragment changes typed by hand (hashchange would double-fire).
    window.addEventListener('popstate', onChange)
    return () => window.removeEventListener('popstate', onChange)
  }, [])
  const go = useCallback((next: Route, options: { replace?: boolean } = {}) => {
    const target = formatRoute(next)
    if (target === window.location.hash) return
    if (options.replace) window.history.replaceState({ teaDepth: currentDepth() }, '', target)
    else window.history.pushState({ teaDepth: currentDepth() + 1 }, '', target)
    lastHash = target
    setRoute(parseRoute(target))
  }, [])
  /** In-app “返回”: use history only when the previous entry is inside the app, otherwise go to the list. */
  const back = useCallback((fallback: Route) => {
    if (currentDepth() > 0) window.history.back()
    else go(fallback, { replace: true })
  }, [go])
  return { route, go, back }
}

/**
 * Dialogs behave like app sheets: opening one adds a history step, so the phone back button closes the dialog
 * instead of leaving the page underneath. Closing by button removes that step again.
 */
export function useBackToClose(onClose: () => void) {
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  useEffect(() => {
    window.history.pushState({ teaDepth: currentDepth() + 1, teaModal: true }, '', window.location.href)
    let closedByBack = false
    const onPop = () => { closedByBack = true; closeRef.current() }
    window.addEventListener('popstate', onPop)
    return () => {
      window.removeEventListener('popstate', onPop)
      if (!closedByBack && (window.history.state as { teaModal?: boolean } | null)?.teaModal) window.history.back()
    }
  }, [])
}
