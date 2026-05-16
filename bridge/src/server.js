// Express server — receives outbound send commands from the Python backend.
// Endpoints:
//   POST /send       { jid, text }         — requires Bearer BRIDGE_SECRET
//   POST /send-file  { jid, filename, mimeType, base64 } — requires Bearer BRIDGE_SECRET
//   GET  /health

import express from 'express'
import { getSocket } from './connection.js'

const PORT = process.env.BRIDGE_PORT || 3000
const BRIDGE_SECRET = process.env.BRIDGE_SECRET || ''

const app = express()
app.use(express.json({ limit: '50mb' }))

// Middleware: verify Bearer token on protected routes.
// If BRIDGE_SECRET is not configured we log a startup warning but allow requests
// through (backwards-compatible for local dev without secrets).
function requireBridgeAuth(req, res, next) {
  if (!BRIDGE_SECRET) {
    // Secret not configured — skip check (warn logged at startup)
    return next()
  }
  const authHeader = req.headers['authorization'] || ''
  const token = authHeader.startsWith('Bearer ') ? authHeader.slice(7) : ''
  if (token !== BRIDGE_SECRET) {
    return res.status(401).json({ error: 'Unauthorized' })
  }
  next()
}

if (!BRIDGE_SECRET) {
  console.warn(
    '[bridge] WARNING: BRIDGE_SECRET is not set. /send and /send-file are unprotected.'
  )
}

app.get('/health', (_req, res) => {
  const connected = getSocket()?.user != null
  res.json({ status: connected ? 'ok' : 'connecting' })
})

app.post('/send', requireBridgeAuth, async (req, res) => {
  const { jid, text } = req.body
  if (!jid || !text) return res.status(400).json({ error: 'jid and text are required' })

  const sock = getSocket()
  if (!sock) return res.status(503).json({ error: 'WhatsApp not connected' })

  try {
    await sock.sendMessage(jid, { text })
    res.json({ ok: true })
  } catch (err) {
    console.error('Failed to send message:', err.message)
    res.status(500).json({ error: err.message })
  }
})

app.post('/send-file', requireBridgeAuth, async (req, res) => {
  const { jid, filename, mimeType, base64 } = req.body
  if (!jid || !filename || !base64) {
    return res.status(400).json({ error: 'jid, filename, and base64 are required' })
  }

  const sock = getSocket()
  if (!sock) return res.status(503).json({ error: 'WhatsApp not connected' })

  try {
    const buffer = Buffer.from(base64, 'base64')
    const isImage = mimeType?.startsWith('image/')
    const isDocument = !isImage

    if (isDocument) {
      await sock.sendMessage(jid, { document: buffer, mimetype: mimeType, fileName: filename })
    } else {
      await sock.sendMessage(jid, { image: buffer, caption: filename })
    }
    res.json({ ok: true })
  } catch (err) {
    console.error('Failed to send file:', err.message)
    res.status(500).json({ error: err.message })
  }
})

app.get('/groups', async (_req, res) => {
  const sock = getSocket()
  if (!sock) return res.status(503).json({ error: 'WhatsApp not connected' })
  try {
    const groups = await sock.groupFetchAllParticipating()
    const list = Object.values(groups).map(g => ({ jid: g.id, name: g.subject }))
    res.json({ groups: list })
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

// GET /group-meta/:jid — returns the group description for the given group JID.
app.get('/group-meta/:jid', requireBridgeAuth, async (req, res) => {
  const sock = getSocket()
  if (!sock) return res.status(503).json({ error: 'WhatsApp not connected' })
  if (!req.params.jid || !req.params.jid.endsWith('@g.us')) {
    return res.status(400).json({ error: 'jid must be a group JID (@g.us)' })
  }
  try {
    const meta = await sock.groupMetadata(req.params.jid)
    res.json({ description: meta.desc || '' })
  } catch (err) {
    console.error('Failed to fetch group metadata:', err.message)
    res.status(500).json({ error: err.message })
  }
})

export function startServer() {
  app.listen(PORT, () => {
    console.log(`Bridge server listening on port ${PORT}`)
  })
}
