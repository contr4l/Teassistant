import { useEffect, useRef } from 'react'
import { ArrowRight, X } from 'lucide-react'
import './usage-guide.css'
import { useBackToClose } from './router'

export type UsageGuideView = 'students' | 'exam' | 'practice'

type UsageGuideProps = {
  onClose: () => void
  onNavigate: (view: UsageGuideView) => void
}

const STEPS = [
  {
    number: '1',
    title: '添加学生，勾选已学考点',
    description: '在“学生档案”填写学生信息并勾选已学考点。组卷只从已学考点中选题。',
  },
  {
    number: '2',
    title: '新建考试，上传材料',
    description: '上传试卷和已批改的学生作答，参考答案选传。手机可直接拍照，上传后可调整顺序。',
  },
  {
    number: '3',
    title: '查看分析，修正识别错误',
    description: '逐题对照原图查看结果。作答文字或题号识别错了，可以单独修正，系统会生成新的分析。标为“待核对”的内容请对照原图确认。',
  },
  {
    number: '4',
    title: '组卷、下载、反馈',
    description: '选择参考考试和考点，默认每个考点 10 题，也可按难度分别设置。学生卷和教师卷（含答案）分开下载，用后可提交反馈。',
  },
] as const

export default function UsageGuide({ onClose, onNavigate }: UsageGuideProps) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const skipNextCloseEvent = useRef(false)
  const callbacks = useRef({ onClose, onNavigate })
  callbacks.current = { onClose, onNavigate }
  useBackToClose(() => callbacks.current.onClose())

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return

    const onNativeClose = () => {
      if (dialog.open) return // Ignore an earlier close event after StrictMode reopens it.
      if (skipNextCloseEvent.current) {
        skipNextCloseEvent.current = false
        return
      }
      callbacks.current.onClose()
    }
    const onBackdropClick = (event: MouseEvent) => {
      if (event.target === dialog) close()
    }
    const onTabKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Tab') return
      const buttons = Array.from(dialog.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'))
      const first = buttons[0]
      const last = buttons[buttons.length - 1]
      if (!first || !last) return
      const active = document.activeElement
      if (event.shiftKey && (active === first || active === dialog)) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && (active === last || active === dialog)) {
        event.preventDefault()
        first.focus()
      }
    }
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'

    dialog.addEventListener('close', onNativeClose)
    dialog.addEventListener('click', onBackdropClick)
    dialog.addEventListener('keydown', onTabKeyDown)
    if (!dialog.open) dialog.showModal()

    return () => {
      dialog.removeEventListener('close', onNativeClose)
      dialog.removeEventListener('click', onBackdropClick)
      dialog.removeEventListener('keydown', onTabKeyDown)
      document.body.style.overflow = previousOverflow
      if (dialog.open) dialog.close()
    }
  }, [])

  const close = () => {
    const dialog = dialogRef.current
    if (dialog?.open) {
      skipNextCloseEvent.current = true
      dialog.close()
    }
    callbacks.current.onClose()
  }
  const navigate = (view: UsageGuideView) => {
    callbacks.current.onNavigate(view)
    close()
  }

  return <dialog className="usage-guide-dialog" id="usage-guide" ref={dialogRef} aria-labelledby="usage-guide-title">
    <div className="usage-guide-shell">
      <header className="usage-guide-header">
        <div className="usage-guide-title-wrap"><h2 id="usage-guide-title">使用方式</h2></div>
        <button className="usage-guide-close" type="button" aria-label="关闭使用方式" onClick={close}><X size={19} /></button>
      </header>

      <div className="usage-guide-body">
        <ol className="usage-guide-steps">
          {STEPS.map(({ number, title, description }) => <li className="usage-guide-step" key={number}>
            <span className="usage-guide-step-no">{number}</span>
            <div className="usage-guide-step-copy"><h3>{title}</h3><p>{description}</p></div>
          </li>)}
        </ol>
        <p className="usage-guide-tip">额度不足或分析无法提交时，请联系管理员。</p>
      </div>

      <footer className="usage-guide-footer">
        <span className="usage-guide-footer-label">前往</span>
        <div className="usage-guide-actions">
          <button type="button" onClick={() => navigate('students')}>学生档案 <ArrowRight size={14} /></button>
          <button type="button" onClick={() => navigate('exam')}>考试分析 <ArrowRight size={14} /></button>
          <button type="button" onClick={() => navigate('practice')}>组卷 <ArrowRight size={14} /></button>
        </div>
      </footer>
    </div>
  </dialog>
}
