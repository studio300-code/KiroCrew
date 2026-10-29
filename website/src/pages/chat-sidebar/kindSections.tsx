/** "Group by kind": the filter menu's view option that splits each session list into
 *  sections -- Crew Mode, Normal, From crewmates. View only: it reorders and labels the
 *  rows a list already draws, and never changes a session's folder, tags or order. */
import type { Slot } from './types'
import { i18nT } from '../../i18n/t'

/** A session's kind, in section order. */
export type SessionKind = 'crew' | 'normal' | 'mate'

export const SESSION_KIND_ORDER: readonly SessionKind[] = ['crew', 'normal', 'mate']

const KIND_LABEL_KEY: Record<SessionKind, string> = {
  crew: 'pages.chatSidebar.kind_section_crew',
  normal: 'pages.chatSidebar.kind_section_normal',
  mate: 'pages.chatSidebar.kind_section_mate',
}

/** Whether a row is a crewmate's own session (its chat runs a crewmate, or it lives on
 *  the Members surface). */
function isCrewmateRow(s: Pick<Slot, 'agent_kind' | 'surface'>): boolean {
  return s.agent_kind === 'member' || s.surface === 'member'
}

/**
 * The kind reader for one render.
 *
 * - `crew`: a Crew Mode chat (`crew_mode_root`), or a Crew Mode worker that is still a
 *   row because its root closed (`crew_root`).
 * - `mate`: a crewmate's chat, or a session a crewmate opened (`session_create`), read
 *   up the creator chain through the live slots. Only local rows are walked: a peer
 *   row's `parent.key` is in its own gateway's key space.
 * - `normal`: everything else.
 */
export function makeKindOf(liveSlots: readonly Slot[]): (s: Slot) => SessionKind {
  const byKey = new Map<string, Slot>()
  for (const s of liveSlots) if (s.key && !s.peer_id) byKey.set(s.key, s)
  const cache = new Map<Slot, SessionKind>()
  return (s: Slot) => {
    const hit = cache.get(s)
    if (hit) return hit
    let kind: SessionKind = 'normal'
    if (s.crew_mode_root || s.crew_root) kind = 'crew'
    else if (isCrewmateRow(s)) kind = 'mate'
    else if (!s.peer_id) {
      // Bounded by the live population; `seen` stops a cycle.
      const seen = new Set<string>([s.key])
      let cited = s.parent?.key ?? null
      while (cited && !seen.has(cited)) {
        seen.add(cited)
        const creator = byKey.get(cited)
        if (!creator) break
        if (isCrewmateRow(creator)) { kind = 'mate'; break }
        cited = creator.parent?.key ?? null
      }
    }
    cache.set(s, kind)
    return kind
  }
}

/** The grouping a lane applies, or null when the option is off (or a search is
 *  ranking the list, whose order is the answer). */
export interface KindSections {
  kindOf: (s: Slot) => SessionKind
}

/**
 * Reorder one list into its sections: pinned rows stay first in their own order (the
 * pinned band is not split), then the unpinned rows by kind, each kind keeping the
 * list's order. Returns the list itself when it holds fewer than two kinds, so a list
 * with nothing to split is drawn exactly as before.
 */
export function sectionRows<T extends Slot>(rows: T[], isPinned: (s: Slot) => boolean, sections: KindSections | null): T[] {
  if (!sections) return rows
  const { kindOf } = sections
  const pinned: T[] = []
  const buckets: Record<SessionKind, T[]> = { crew: [], normal: [], mate: [] }
  for (const s of rows) {
    if (isPinned(s)) pinned.push(s)
    else buckets[kindOf(s)].push(s)
  }
  const used = SESSION_KIND_ORDER.filter(k => buckets[k].length > 0)
  if (used.length < 2) return rows
  return [...pinned, ...SESSION_KIND_ORDER.flatMap(k => buckets[k])]
}

/**
 * For each row of a drawn list, the section a header opens before it, or null. A
 * header opens at the first unpinned row of each kind, and only in a list with two or
 * more kinds among its unpinned rows. All nulls when `sections` is null.
 */
export function sectionHeaders(rows: readonly Slot[], isPinned: (s: Slot) => boolean, sections: KindSections | null): (SessionKind | null)[] {
  const out: (SessionKind | null)[] = rows.map(() => null)
  if (!sections) return out
  const { kindOf } = sections
  const kinds = new Set<SessionKind>()
  for (const s of rows) if (!isPinned(s)) kinds.add(kindOf(s))
  if (kinds.size < 2) return out
  rows.forEach((row, i) => {
    if (isPinned(row)) return
    const kind = kindOf(row)
    const prev = i > 0 ? rows[i - 1] : null
    if (prev && !isPinned(prev) && kindOf(prev) === kind) return
    out[i] = kind
  })
  return out
}

/** A row's place in the section order, for the hover hold (pinned rows first, then
 *  each kind), or undefined when the option is off. */
export function sectionRankOf(isPinned: (s: Slot) => boolean, sections: KindSections | null): ((s: Slot) => number) | undefined {
  if (!sections) return undefined
  const { kindOf } = sections
  return s => (isPinned(s) ? 0 : 1 + SESSION_KIND_ORDER.indexOf(kindOf(s)))
}

/**
 * The manual drawn order (`manualOrder.ts` `drawnOrder`, keys) as the grouped lists
 * draw it: stably by kind, so Alt+Arrow picks the row the person sees next. The
 * order itself when `kindOf` is null.
 */
export function groupDrawnOrder(order: string[], slots: readonly Slot[], kindOf: ((s: Slot) => SessionKind) | null): string[] {
  if (!kindOf) return order
  const byKey = new Map(slots.map(s => [s.key, s] as const))
  const rank = (k: string) => { const s = byKey.get(k); return s ? SESSION_KIND_ORDER.indexOf(kindOf(s)) : 0 }
  return order.map((k, i) => [k, i] as const).sort((x, y) => rank(x[0]) - rank(y[0]) || x[1] - y[1]).map(([k]) => k)
}

/**
 * Whether a manual move of `activeKey` onto `overKey` crosses a section. Such a
 * move is refused: the row would land in an order its own section never draws, so
 * it would be saved without moving on screen.
 */
export function movesAcrossSections(activeKey: string, overKey: string, slots: readonly Slot[], kindOf: ((s: Slot) => SessionKind) | null): boolean {
  if (!kindOf) return false
  const a = slots.find(s => s.key === activeKey)
  const b = slots.find(s => s.key === overKey)
  return !!a && !!b && kindOf(a) !== kindOf(b)
}

/** The label a section header reads. */
export function kindSectionLabel(kind: SessionKind): string {
  return i18nT(KIND_LABEL_KEY[kind])
}

/** One section header. A heading, so a screen reader can jump between sections. */
export function KindSectionHeader({ kind }: { kind: SessionKind }) {
  return (
    <div
      role="heading"
      aria-level={3}
      data-kind-section={kind}
      data-testid="kind-section-header"
      className="px-3 pt-3 pb-1 text-[11px] font-semibold text-muted uppercase tracking-[.06em] select-none first:pt-1"
    >
      {kindSectionLabel(kind)}
    </div>
  )
}
