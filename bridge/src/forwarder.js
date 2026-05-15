import axios from 'axios'
import { getSocket } from './connection.js'

const BACKEND_URL = process.env.BACKEND_URL || 'http://app:8000'
const WEBHOOK_SECRET = process.env.WEBHOOK_SECRET || ''
const RETRY_ATTEMPTS = 3
const RETRY_DELAY_MS = 1000

async function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function notifyGroup(jid, text) {
  const sock = getSocket()
  if (!sock || !jid) return
  try {
    await sock.sendMessage(jid, { text })
  } catch (err) {
    console.error('Failed to send failure notification to group:', err.message)
  }
}

export async function forwardToBackend(payload) {
  const headers = { 'Content-Type': 'application/json' }
  if (WEBHOOK_SECRET) {
    headers['Authorization'] = `Bearer ${WEBHOOK_SECRET}`
  }

  for (let attempt = 1; attempt <= RETRY_ATTEMPTS; attempt++) {
    try {
      const resp = await axios.post(`${BACKEND_URL}/webhook`, payload, { headers, timeout: 30000 })

      // Backend returns 200 with { ok: true } normally, but rate-limit also
      // returns 200 without queuing the image — surface that to the user.
      if (resp.data?.rate_limited && payload.jid) {
        await notifyGroup(
          payload.jid,
          '⚠️ Too many invoices submitted too quickly. Please wait a moment and resend.'
        )
      }
      return
    } catch (err) {
      const status = err.response?.status
      console.error(`Webhook forward attempt ${attempt}/${RETRY_ATTEMPTS} failed (${status || err.message})`)
      if (attempt < RETRY_ATTEMPTS) await sleep(RETRY_DELAY_MS * attempt)
    }
  }

  // All retries exhausted — tell the group so the user knows to resend
  console.error('All webhook forward attempts exhausted. Message dropped:', payload.messageId)
  if (payload.type === 'image' && payload.jid) {
    await notifyGroup(
      payload.jid,
      "⚠️ Couldn't process invoice — please resend the image."
    )
  }
}
