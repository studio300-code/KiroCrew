/**
 * The drag a desktop trackpad swipe plays on a side panel anchored to the
 * window's RIGHT edge: the offset under the fingers, and the release.
 *
 * Shared by every page that hosts the swipe (the Mates page, the chat page),
 * so a panel follows the fingers and settles by one rule wherever it is. The
 * run itself (where it starts, where the fingers lift) is `useTrackpadSwipe`'s;
 * this is what a page does with the run's summed `deltaX`.
 *
 * The offset is the panel's distance from its resting place, px: 0 is fully
 * open, `travel` fully out. The release is the phone's rule from
 * `useDrawerSwipe`, so a trackpad and a finger settle a panel the same way.
 */
import { animate, type MotionValue } from 'framer-motion'
import { COMMIT_DRAG_SHARE, COMMIT_VELOCITY, VELOCITY_MIN_SPAN_MS } from './useDrawerSwipe'
import { holdStreamingFlushes, releaseStreamingFlushes } from '../lib/streamHold'

/**
 * How a trackpad drag finishes once it commits (or falls back), on both pages.
 * The phone drawer keeps its own timings (`useDrawerSwipe`); nothing here
 * changes them.
 *
 * The phone's band (0.12-0.45s, tuned on a 350px drawer) reads right under a
 * finger. A desktop panel is wider (460-820px), and a trackpad release usually
 * reads near zero speed, because the fingers slow before they lift. So nearly
 * every release landed on the 0.45s cap: 460px in 450ms is about 1 px/ms, an
 * 820px column about 1.8 px/ms, which reads as a glide.
 *
 * - At most 240ms for any travel: 460px is then about 1.9 px/ms on average,
 *   820px about 3.4 px/ms.
 * - Never slower than 2 px/ms on average: a shorter way is quicker (200px or
 *   less takes the 120ms floor).
 *
 * The curve, `(0.3, 0.3k, 0.9, 1)`, opens at k times the average speed, speeds
 * up, and eases into the edge at the end. Its speed rises once and falls once.
 *
 * - k is the release speed over the average, at least 0.5: a light swipe that
 *   commits moves at once (about 1 px/ms on a 460px panel) and picks up speed.
 *   The phone curves do the opposite here: SETTLE_EASE opens fast and slows,
 *   and the tap's (0.3, 0.3, 0, 1) launches from rest.
 * - At k = 0.5 the panel speeds up for the first ~65% of the time, to about
 *   1.16x the average, and eases in over the rest.
 * - A release faster than the average (a flick) shortens the settle until it
 *   opens at the fingers' own speed (k = 1), so a hard flick ends sooner. At the
 *   120ms floor a still faster one opens at its own speed and only slows.
 *
 * A release that falls back takes the same path, so it eases back as quickly.
 */
const SETTLE_MAX_SECS = 0.24
const SETTLE_MIN_SECS = 0.12
const SETTLE_MIN_PX_PER_MS = 2
const SETTLE_MIN_START = 0.5
const SETTLE_X1 = 0.3
const SETTLE_X2 = 0.9

/** The settle's curve and duration from `from` to `to`, for a release moving at
 *  `velocity` px/ms (positive is out). */
export function trackpadSettleShape(
  to: number, from: number, velocity: number,
): { ease: [number, number, number, number]; secs: number } {
  const distance = Math.abs(to - from)
  const speed = distance > 0 ? Math.max(0, velocity * Math.sign(to - from)) : 0
  let secs = Math.min(SETTLE_MAX_SECS, Math.max(SETTLE_MIN_SECS, distance / SETTLE_MIN_PX_PER_MS / 1000))
  if (speed > 0) secs = Math.max(SETTLE_MIN_SECS, Math.min(secs, distance / speed / 1000))
  const avg = distance > 0 ? distance / (secs * 1000) : 0
  // Up to 3, so the control point stays inside the curve.
  const k = avg > 0 ? Math.min(3, Math.max(SETTLE_MIN_START, speed / avg)) : SETTLE_MIN_START
  return { ease: [SETTLE_X1, SETTLE_X1 * k, SETTLE_X2, 1], secs }
}

/** Settle a trackpad drag to `to`: the one settle both pages use. On the main
 *  thread, so a page reading the offset (its chat spacer) sees every frame.
 *  Interruptible like any settle: `takeOverDrawer(x)` stops it where it is,
 *  and its `onDone` never runs. */
export function settleTrackpadDrag(x: MotionValue<number>, to: number, onDone: () => void, velocity: number): void {
  const { ease, secs } = trackpadSettleShape(to, x.get(), velocity)
  holdStreamingFlushes(secs * 1000 + 100)
  animate(x, to, {
    duration: secs,
    ease,
    onComplete: () => { releaseStreamingFlushes(); onDone() },
    onStop: () => { releaseStreamingFlushes() },
  })
}

/** The release speed is read over the fingers' last this-many ms. */
export const TRACKPAD_VELOCITY_WINDOW_MS = 60

export interface TrackpadPanelDrag {
  on: boolean
  /** The state a release falls back to: where the drag started (or, grabbed
   *  mid-settle, the state that settle was heading for). */
  startedOpen: boolean
  /** How far the panel travels from open to out, px: its own width. */
  travel: number
  /** The offset the fingers' sum is measured from. */
  base: number
  /** (offset, event time) per move, newest last. */
  samples: { x: number; t: number }[]
}

export function idleTrackpadDrag(): TrackpadPanelDrag {
  return { on: false, startedOpen: false, travel: 0, base: 0, samples: [] }
}

/** The offset for the run's summed `deltaX` at `at`, recorded for the release
 *  speed. Fingers moving left report a positive `deltaX`, which pulls the panel
 *  in. */
export function moveTrackpadDrag(d: TrackpadPanelDrag, sumX: number, at: number): number {
  const x = Math.min(d.travel, Math.max(0, d.base - sumX))
  d.samples.push({ x, t: at })
  if (d.samples.length > 32) d.samples.splice(0, d.samples.length - 32)
  return x
}

/**
 * Rule C, the speed half: a drag whose fingers move toward open or closed
 * faster than `COMMIT_VELOCITY` (over the same 60ms window as a release)
 * commits there at once, without waiting for the lift-off. Call after each
 * move; null while it has not.
 *
 * Distance alone never commits mid-drag: a slow drag past the share stays
 * under the fingers and can come back, and the release decides.
 *
 * A single spiky wheel event must not commit, so the speed must hold both over
 * the window and with its largest single step taken out, and the window must
 * span at least `VELOCITY_MIN_SPAN_MS` and two steps.
 */
export function speedCommitTrackpadDrag(d: TrackpadPanelDrag): { open: boolean; velocity: number } | null {
  const s = d.samples
  const last = s[s.length - 1]
  if (!last) return null
  let i0 = s.length - 1
  while (i0 > 0 && last.t - s[i0 - 1].t <= TRACKPAD_VELOCITY_WINDOW_MS) i0 -= 1
  const steps = s.length - 1 - i0
  const span = last.t - s[i0].t
  if (steps < 2 || span < VELOCITY_MIN_SPAN_MS) return null
  const velocity = (last.x - s[i0].x) / span
  let largest = 0
  for (let i = i0 + 1; i < s.length; i++) {
    const step = s[i].x - s[i - 1].x
    if (Math.abs(step) > Math.abs(largest)) largest = step
  }
  const withoutLargest = (last.x - s[i0].x - largest) / span
  // A smaller offset is open (the panel is anchored right).
  if (velocity < -COMMIT_VELOCITY && withoutLargest < -COMMIT_VELOCITY) return { open: true, velocity }
  if (velocity > COMMIT_VELOCITY && withoutLargest > COMMIT_VELOCITY) return { open: false, velocity }
  return null
}

/**
 * Where the panel goes once the fingers are off, and the speed it leaves with
 * (px/ms, positive moving out).
 *
 * `how` is the reader's: `momentum` when the tail was recognised, so the
 * fingers left at the speed of their last moves; `gap` when no event came for
 * the idle window, so they had stopped before they left; `lift` when the desktop
 * app reported the real lift-off at `at`, so the last 60ms are the fingers' own
 * and a pause before lifting carries no speed either. A flick decides; else
 * moving past the share decides by its direction; else the panel goes back to
 * `startedOpen`.
 */
export function releaseTrackpadDrag(
  d: TrackpadPanelDrag, x: number, how: 'momentum' | 'gap' | 'lift', at: number,
): { open: boolean; velocity: number } {
  const last = d.samples[d.samples.length - 1]
  const liftAt = how === 'momentum' && last ? last.t : at
  let first: { x: number; t: number } | undefined
  for (let i = 0; i < d.samples.length; i++) {
    if (liftAt - d.samples[i].t <= TRACKPAD_VELOCITY_WINDOW_MS) {
      first = d.samples[i]
      if (liftAt - first.t < VELOCITY_MIN_SPAN_MS && i > 0) first = d.samples[i - 1]
      break
    }
  }
  const span = first && last ? last.t - first.t : 0
  const velocity = first && last && span > 0 ? (last.x - first.x) / span : 0
  // A smaller offset is open (the panel is anchored right).
  const dragged = Math.abs(x - d.base) / Math.max(1, d.travel)
  let open: boolean
  if (-velocity > COMMIT_VELOCITY) open = true
  else if (velocity > COMMIT_VELOCITY) open = false
  else if (dragged > COMMIT_DRAG_SHARE) open = x < d.base
  else open = d.startedOpen
  return { open, velocity }
}
