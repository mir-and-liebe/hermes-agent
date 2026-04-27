import { Box, Text, useInput, useStdout } from '@hermes/ink'
import { useEffect, useMemo, useState } from 'react'

import type { GatewayClient } from '../gatewayClient.js'
import { rpcErrorMessage } from '../lib/rpc.js'
import type { Theme } from '../theme.js'

import { OverlayHint, windowItems, windowOffset } from './overlayControls.js'

const VISIBLE = 12
const MIN_WIDTH = 52
const MAX_WIDTH = 104

const typeIcon: Record<string, string> = {
  integration: '◇',
  memory: '◆',
  recall: '↺',
  skill: '✦',
  'skill-use': '↻',
  user: '●'
}

const fmtTime = (ts?: null | number) => {
  if (!ts) {
    return ''
  }

  const days = Math.floor((Date.now() - ts * 1000) / 86_400_000)

  return days <= 0 ? 'today' : `${days}d ago`
}

export function LearningLedger({ gw, onClose, t }: LearningLedgerProps) {
  const [ledger, setLedger] = useState<LearningLedgerResponse | null>(null)
  const [idx, setIdx] = useState(0)
  const [expanded, setExpanded] = useState(false)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)
  const { stdout } = useStdout()
  const width = Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, (stdout?.columns ?? 80) - 6))

  useEffect(() => {
    gw.request<LearningLedgerResponse>('learning.ledger', { limit: 120 })
      .then(r => {
        setLedger(r)
        setErr('')
      })
      .catch((e: unknown) => setErr(rpcErrorMessage(e)))
      .finally(() => setLoading(false))
  }, [gw])

  const items = ledger?.items ?? []
  const selected = items[idx]
  const counts = useMemo(
    () =>
      Object.entries(ledger?.counts ?? {})
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([k, v]) => `${k}:${v}`)
        .join(' · '),
    [ledger?.counts]
  )

  useInput((ch, key) => {
    if (key.escape || ch.toLowerCase() === 'q') {
      onClose()

      return
    }

    if (key.upArrow && idx > 0) {
      setIdx(v => v - 1)

      return
    }

    if (key.downArrow && idx < items.length - 1) {
      setIdx(v => v + 1)

      return
    }

    if (key.return || ch === ' ') {
      setExpanded(v => !v)

      return
    }

    const n = ch === '0' ? 10 : parseInt(ch, 10)
    if (!Number.isNaN(n) && n >= 1 && n <= Math.min(10, items.length)) {
      const next = windowOffset(items.length, idx, VISIBLE) + n - 1

      if (items[next]) {
        setIdx(next)
      }
    }
  })

  if (loading) {
    return <Text color={t.color.dim}>indexing learning ledger…</Text>
  }

  if (err) {
    return (
      <Box flexDirection="column" width={width}>
        <Text color={t.color.label}>learning ledger error: {err}</Text>
        <OverlayHint t={t}>Esc/q close</OverlayHint>
      </Box>
    )
  }

  if (!items.length) {
    return (
      <Box flexDirection="column" width={width}>
        <Text bold color={t.color.amber}>
          Learning Ledger
        </Text>
        <Text color={t.color.dim}>no memories, skills, recalls, or integrations found yet</Text>
        <OverlayHint t={t}>Esc/q close</OverlayHint>
      </Box>
    )
  }

  const { items: visible, offset } = windowItems(items, idx, VISIBLE)

  return (
    <Box flexDirection="column" width={width}>
      <Text bold color={t.color.amber}>
        Learning Ledger
      </Text>
      <Text color={t.color.dim}>
        {ledger?.total ?? items.length} traces · {counts}
      </Text>
      {offset > 0 && <Text color={t.color.dim}> ↑ {offset} more</Text>}

      {visible.map((item, i) => {
        const absolute = offset + i
        const active = absolute === idx
        const when = fmtTime(item.last_used_at ?? item.learned_at)
        const count = item.count ? ` ×${item.count}` : ''
        const icon = typeIcon[item.type] ?? '•'

        return (
          <Text
            bold={active}
            color={active ? t.color.amber : t.color.dim}
            inverse={active}
            key={`${item.type}:${item.name}:${i}`}
            wrap="truncate-end"
          >
            {active ? '▸ ' : '  '}
            {i + 1}. {icon} {item.name}
            <Text color={active ? t.color.amber : t.color.dim}>
              {' '}
              · {item.type}
              {count}
              {when ? ` · ${when}` : ''}
            </Text>
          </Text>
        )
      })}

      {offset + VISIBLE < items.length && <Text color={t.color.dim}> ↓ {items.length - offset - VISIBLE} more</Text>}

      {selected && expanded ? (
        <Box borderColor={t.color.dim} borderStyle="single" flexDirection="column" marginTop={1} paddingX={1}>
          <Text color={t.color.gold}>{selected.summary}</Text>
          <Text color={t.color.dim}>source: {selected.source}</Text>
        </Box>
      ) : null}

      <OverlayHint t={t}>↑/↓ select · Enter/Space details · 1-9,0 quick · Esc/q close</OverlayHint>
    </Box>
  )
}

interface LearningLedgerItem {
  count?: number
  last_used_at?: null | number
  learned_at?: null | number
  name: string
  source: string
  summary: string
  type: string
}

interface LearningLedgerResponse {
  counts?: Record<string, number>
  generated_at?: number
  home?: string
  items?: LearningLedgerItem[]
  total?: number
}

interface LearningLedgerProps {
  gw: GatewayClient
  onClose: () => void
  t: Theme
}
