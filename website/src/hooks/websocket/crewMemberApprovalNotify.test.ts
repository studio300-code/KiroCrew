/** A Crew Mode crew member's tool approval reaches the person while its conductor
 *  is open but is not the chat on screen (docs/decisions/2026-10-29-crew-member-
 *  approvals-notify.md). The alerts point at the conductor, whose approval card is
 *  the only place the approval is decided, and carry the crew member's name. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import { QueryClient } from '@tanstack/react-query'
import { store } from '../../store'
import { sseSlots } from '../../store/dashboardSlice'
import { setActiveSlot } from '../../store/chatSlice'
import { clearAllNotifications } from '../../store/notificationsSlice'
import { MC_NOTIFICATION_EVENT, MC_LIVE_NOTIFICATION_EVENT, APPROVAL_KIND } from '../notificationEvent'
import type { ChatSlot, Notification } from '../../types'
import { useApprovalRegistry } from './approvals'
import { useChatStream } from './chatStream'
import type { StreamBuffers } from './streamBuffers'
import type { VoicePlayback } from './voicePlayback'

const toasts = vi.hoisted(() => ({ posted: [] as { title: string; tag?: string }[] }))
vi.mock('../../lib/nativeNotify', async importOriginal => ({
  ...(await importOriginal<typeof import('../../lib/nativeNotify')>()),
  nativeNotificationPermitted: () => true,
  postNativeNotification: (title: string, opts?: { tag?: string }) => { toasts.posted.push({ title, tag: opts?.tag }) },
}))
vi.mock('../windowAway', () => ({ isWindowAway: () => true }))

const row = (key: string, extra: Partial<ChatSlot> = {}): ChatSlot =>
  ({ key, title: key, messages: 1, running: false, ...extra }) as ChatSlot

describe("a crew member's approval alerts the person, pointed at its conductor", () => {
  const chimes: string[] = []
  const banners: Notification[] = []
  const onChime = (e: Event) => { chimes.push((e as CustomEvent).detail?.kind) }
  const onBanner = (e: Event) => { banners.push((e as CustomEvent).detail?.note) }

  beforeEach(() => {
    chimes.length = 0
    banners.length = 0
    toasts.posted.length = 0
    // The conductor is live, but the person is in another chat.
    store.dispatch(sseSlots([
      row('root', { title: 'Paper Flags', crew_mode_root: true }),
      row('w1', { title: 'Flag A', created_by: 'root', crew_root: 'root' }),
      row('other', { title: 'Other chat' }),
    ]))
    store.dispatch(setActiveSlot('other'))
    window.addEventListener(MC_NOTIFICATION_EVENT, onChime)
    window.addEventListener(MC_LIVE_NOTIFICATION_EVENT, onBanner)
  })
  afterEach(() => {
    window.removeEventListener(MC_NOTIFICATION_EVENT, onChime)
    window.removeEventListener(MC_LIVE_NOTIFICATION_EVENT, onBanner)
    store.dispatch(clearAllNotifications())
    store.dispatch(setActiveSlot(null))
    store.dispatch(sseSlots([]))
  })

  it('an approval frame chimes and adds a bell note and banner for the conductor, named for the crew member', () => {
    const { result } = renderHook(() => useApprovalRegistry(store.dispatch, new QueryClient()))
    const owner = result.current.onApprovalFrame({ id: 'ap-w1', slot: 'w1', source: 'agent', tool: 'Shell', tool_input: 'ls', ts: 5 }, false)
    // The permission row still goes to the crew member's own transcript.
    expect(owner).toBe('w1')
    expect(chimes).toEqual([APPROVAL_KIND])
    const note = store.getState().notifications.items.find(n => n.approval_id === 'ap-w1')
    expect(note).toMatchObject({ kind: 'approval', slot: 'root', crew_member: 'w1', title: 'Flag A needs approval' })
    expect(banners.map(n => n.approval_id)).toEqual(['ap-w1'])
  })

  it("a crew member's native permission row chimes and posts an OS toast in its name", () => {
    const buffers = { flushChunks: vi.fn(), bufferSlotActivity: vi.fn() } as unknown as StreamBuffers
    const voice = { resetForNewTurn: vi.fn() } as unknown as VoicePlayback
    const { result } = renderHook(() => useChatStream({ dispatch: store.dispatch, buffers, voice, reconnectingRef: { current: false } }))
    result.current.onChatMessage({ slot: 'w1', role: 'permission', content: 'Shell', ts: '2026-10-29T16:10:00Z', meta: { approval_id: 'r1' } })
    expect(chimes).toEqual([APPROVAL_KIND])
    expect(toasts.posted).toEqual([{ title: 'Flag A', tag: 'kirocrew-approval:w1:r1' }])
  })
})
