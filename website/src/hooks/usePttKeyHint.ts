import { useEffect, useState } from 'react'
import {
  bindingAriaKeyshortcuts,
  bindingLabel,
  loadPttConfig,
  PTT_CHANGED_EVENT,
  type PttConfig,
} from '../lib/pushToTalk'

/** The push-to-talk key, as a control that starts dictation should name it. */
export interface PttKeyHint {
  /** The spelled-out name, e.g. "Right Option ⌥" or "Alt + Shift + Space". */
  name: string
  /** The same key as an `aria-keyshortcuts` value, e.g. "Alt+Shift+Space". */
  ariaKeyshortcuts: string
}

function hintFor(cfg: PttConfig): PttKeyHint | null {
  if (!cfg.enabled) return null
  return { name: bindingLabel(cfg.binding), ariaKeyshortcuts: bindingAriaKeyshortcuts(cfg.binding) }
}

/**
 * The push-to-talk key the user has bound, or null when the key trigger is off.
 *
 * Read from the same localStorage config `usePushToTalk` reads, and re-read on
 * the same two signals: `PTT_CHANGED_EVENT` for a rebind in this window, and
 * `storage` for one in another dashboard window.
 */
export function usePttKeyHint(): PttKeyHint | null {
  // The config is the state, and the name is derived on every render, so a
  // language switch re-renders it in the new language like every other label.
  const [cfg, setCfg] = useState<PttConfig>(() => loadPttConfig())
  useEffect(() => {
    const onChange = () => setCfg(loadPttConfig())
    window.addEventListener(PTT_CHANGED_EVENT, onChange)
    window.addEventListener('storage', onChange)
    return () => {
      window.removeEventListener(PTT_CHANGED_EVENT, onChange)
      window.removeEventListener('storage', onChange)
    }
  }, [])
  return hintFor(cfg)
}
