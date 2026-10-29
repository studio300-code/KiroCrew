import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { screen, act } from '@testing-library/react'
import { renderWithProviders } from './helpers'
import ChatInput from '../components/ChatInput'
import { ComposerVoiceSliceOverride } from '../chat-core/composer/Composer'
import { createAudioSample } from '../hooks/mic'
import { bindingAriaKeyshortcuts, defaultConfig, savePttConfig, type PttConfig } from '../lib/pushToTalk'

/**
 * The composer mic names the push-to-talk key, in its label and title and as
 * `aria-keyshortcuts`, because the key does the same job and nothing else in the
 * composer says so. It says nothing when the key trigger is off in Settings, and
 * follows a rebind without a remount.
 */

vi.mock('../components/Strands', () => ({
  __esModule: true,
  default: () => <div data-testid="strands-stub" />,
  strandsSupported: () => true,
}))

const voiceProps = {
  onVoiceToggle: vi.fn(),
  onVoiceCancel: vi.fn(),
  onVoicePrewarm: vi.fn(),
  onVoiceStart: vi.fn(),
  onVoiceStop: vi.fn(),
  voiceSampleRef: { current: createAudioSample() },
}

const base = { value: '', onChange: vi.fn(), onSend: vi.fn(), connected: true }

/** A fine pointer, so the mic is the record button rather than a mode switch. */
function stubFinePointer() {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: false,
    media: q,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }))
}

/** Non-mac config, as jsdom reports no Mac platform. */
const cfg = (over: Partial<PttConfig> = {}): PttConfig => ({ ...defaultConfig(false), ...over })

function renderComposer() {
  renderWithProviders(
    <ComposerVoiceSliceOverride inputProps={voiceProps}><ChatInput {...base} /></ComposerVoiceSliceOverride>,
  )
}

beforeEach(() => {
  localStorage.clear()
  stubFinePointer()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('ChatInput — the mic names the push-to-talk key', () => {
  it('names a bound key in the label, the title and aria-keyshortcuts', () => {
    savePttConfig(cfg())
    renderComposer()
    const mic = screen.getByRole('button', { name: 'Voice input (Alt + Shift + Space)' })
    expect(mic).toHaveAttribute('title', 'Voice input (Alt + Shift + Space)')
    expect(mic).toHaveAttribute('aria-keyshortcuts', 'Alt+Shift+Space')
  })

  it('says nothing about a key when the key trigger is off', () => {
    savePttConfig(cfg({ enabled: false }))
    renderComposer()
    const mic = screen.getByRole('button', { name: 'Voice input' })
    expect(mic).toHaveAttribute('title', 'Voice input')
    expect(mic).not.toHaveAttribute('aria-keyshortcuts')
  })

  it('follows a rebind in Settings without a remount', () => {
    savePttConfig(cfg())
    renderComposer()
    expect(screen.getByRole('button', { name: 'Voice input (Alt + Shift + Space)' })).toBeTruthy()

    act(() => { savePttConfig(cfg({ binding: { code: 'KeyV', ctrl: true, shift: true } })) })
    const mic = screen.getByRole('button', { name: 'Voice input (Ctrl + Shift + V)' })
    expect(mic).toHaveAttribute('aria-keyshortcuts', 'Control+Shift+V')

    act(() => { savePttConfig(cfg({ enabled: false })) })
    expect(screen.getByRole('button', { name: 'Voice input' })).not.toHaveAttribute('aria-keyshortcuts')
  })
})

describe('bindingAriaKeyshortcuts', () => {
  it('writes a bare modifier as its family and a chord modifiers-first', () => {
    expect(bindingAriaKeyshortcuts({ code: 'AltRight' })).toBe('Alt')
    expect(bindingAriaKeyshortcuts({ code: 'MetaLeft' })).toBe('Meta')
    expect(bindingAriaKeyshortcuts({ code: 'Space', alt: true, shift: true })).toBe('Alt+Shift+Space')
    expect(bindingAriaKeyshortcuts({ code: 'Digit1', ctrl: true })).toBe('Control+1')
  })
})
