import makeWASocket, {
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
  downloadMediaMessage,
  isJidGroup,
} from '@whiskeysockets/baileys'
import axios from 'axios'
import P from 'pino'
import qrcode from 'qrcode-terminal'
import sharp from 'sharp'
import { readdir, rm } from 'fs/promises'
import { join } from 'path'
import { isGroupAdmin, invalidateGroup } from './adminCache.js'
import { forwardToBackend } from './forwarder.js'
import { extractInbound } from './messageExtract.js'

const AUTH_PATH = process.env.AUTH_PATH || './auth'
const RECONNECT_DELAY_MS = 3000

// Watchdog: Baileys occasionally hits an internal query timeout (e.g. inside
// executeInitQueries) that gets caught and logged by its own logger without
// ever firing our 'connection.update'/close handler — the socket is left in
// a silently-dead state (process alive, WhatsApp traffic stopped) that the
// normal reconnect logic above never sees. Detect this independently by
// periodically probing the connection and exiting if it keeps failing —
// Docker's `restart: unless-stopped` policy then restarts the process and
// reconnects cleanly, mirroring the existing loggedOut recovery path below.
//
// The probe was originally groupFetchAllParticipating (a bulk metadata
// fetch) on a 2-minute interval. Over ~3 hours of continuous polling this
// got WhatsApp to start returning "rate-overlimit" on the probe itself,
// which the watchdog correctly treated as a failure and restarted on — but
// the *reconnect* was then also rate-limited, cascading into a connection
// that failed every single handshake attempt (code 408) for 9+ days
// straight, never reaching 'open' again, invisible to this watchdog since
// it only runs once connectionOpen is true. sendPresenceUpdate is the same
// lightweight, single-target call already used once per connect() below —
// unlike a bulk group-metadata fetch, presence updates are core, extremely
// frequent traffic in normal WhatsApp usage and much less likely to look
// like abuse. Interval/threshold widened too, as extra margin.
const WATCHDOG_CHECK_INTERVAL_MS = 5 * 60 * 1000
const WATCHDOG_PROBE_TIMEOUT_MS = 20 * 1000
const WATCHDOG_STALE_THRESHOLD_MS = 15 * 60 * 1000

let lastAliveAt = Date.now()
let connectionOpen = false
let watchdogTimer = null
// Baileys refreshes an unscanned QR every ~20-30s; emailing every refresh
// burned through Gmail's daily sending limit within minutes during an
// extended outage, blocking the one channel meant to help. Throttle to at
// most one email per QR_EMAIL_MIN_INTERVAL_MS instead — frequent enough
// that a fresh QR is always in the inbox within a reasonable wait, far
// below the daily quota even if an episode runs for hours.
let lastQrEmailAt = 0
const QR_EMAIL_MIN_INTERVAL_MS = 10 * 60 * 1000

// Prevent Baileys internal bad-request / unhandled rejections from crashing the process
process.on('unhandledRejection', (reason) => {
  console.error('Unhandled rejection (non-fatal):', reason?.message || reason)
})

// Whitelist of group JIDs to process. If empty, ALL groups are processed.
// Set ALLOWED_GROUPS=120363xxxxxxx@g.us,120363yyyyyyy@g.us in env.
const ALLOWED_GROUPS = process.env.ALLOWED_GROUPS
  ? process.env.ALLOWED_GROUPS.split(',').map(j => j.trim()).filter(Boolean)
  : []

// Suppress Baileys' verbose internal logs
const logger = P({ level: 'warn' })

let sock = null

export function getSocket() {
  return sock
}

function startWatchdog() {
  if (watchdogTimer) return  // already running — connect() may be called again on reconnect
  watchdogTimer = setInterval(async () => {
    if (!sock || !connectionOpen) return  // reconnect already in progress; let it finish

    try {
      await Promise.race([
        sock.sendPresenceUpdate('unavailable'),
        new Promise((_, reject) =>
          setTimeout(() => reject(new Error('watchdog probe timed out')), WATCHDOG_PROBE_TIMEOUT_MS)
        ),
      ])
      lastAliveAt = Date.now()
    } catch (err) {
      console.warn('Watchdog probe failed:', err.message)
    }

    const staleFor = Date.now() - lastAliveAt
    if (staleFor > WATCHDOG_STALE_THRESHOLD_MS) {
      console.error(
        `Watchdog: no successful liveness probe in ${Math.round(staleFor / 1000)}s — ` +
        `connection appears stuck despite believing it's open. Exiting so Docker can restart and reconnect cleanly.`
      )
      process.exit(1)
    }
  }, WATCHDOG_CHECK_INTERVAL_MS)
}

export async function connect() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_PATH)
  const { version } = await fetchLatestBaileysVersion()

  sock = makeWASocket({
    version,
    auth: state,
    logger,
    printQRInTerminal: false,
    markOnlineOnConnect: false,
    shouldSyncHistoryMessage: () => false,
  })

  sock.ev.on('creds.update', saveCreds)

  sock.ev.on('connection.update', async (update) => {
    const { connection, lastDisconnect, qr } = update

    if (qr) {
      console.log('\n📱 Scan this QR code with WhatsApp:\n')
      qrcode.generate(qr, { small: true })
      const sinceLastEmail = Date.now() - lastQrEmailAt
      if (sinceLastEmail >= QR_EMAIL_MIN_INTERVAL_MS) {
        lastQrEmailAt = Date.now()
        const qrHeaders = { 'Content-Type': 'application/json' }
        if (process.env.WEBHOOK_SECRET) {
          qrHeaders['Authorization'] = `Bearer ${process.env.WEBHOOK_SECRET}`
        }
        // Fire-and-forget: notify backend to email QR to admin
        axios.post(`${process.env.BACKEND_URL}/internal/qr-notify`, { qr }, { headers: qrHeaders }).catch((err) => {
          console.warn('Could not send QR notification:', err.message)
        })
      }
    }

    if (connection === 'close') {
      connectionOpen = false
      const statusCode = lastDisconnect?.error?.output?.statusCode
      const loggedOut = statusCode === DisconnectReason.loggedOut

      if (loggedOut) {
        // Without this, the stale auth files on disk survive the restart —
        // useMultiFileAuthState reloads the same now-invalid credentials,
        // WhatsApp logs us out again immediately, and the process crash-loops
        // forever without ever showing a fresh QR. Clearing them here is what
        // actually makes "exiting so Docker can restart and show a fresh QR"
        // true, instead of requiring someone to do this by hand on the server.
        console.error('Logged out from WhatsApp. Clearing stale auth files and exiting so Docker can restart and show a fresh QR.')
        try {
          // AUTH_PATH itself is a mounted volume directory — remove its
          // contents, not the mount point (rm'ing the directory itself
          // fails with EBUSY since it's an active mount).
          const entries = await readdir(AUTH_PATH)
          await Promise.all(
            entries.map((entry) => rm(join(AUTH_PATH, entry), { recursive: true, force: true }))
          )
        } catch (err) {
          console.error('Failed to clear auth files:', err.message)
        }
        process.exit(1)
      } else {
        console.log(`Connection closed (code ${statusCode}). Reconnecting in ${RECONNECT_DELAY_MS}ms...`)
        setTimeout(connect, RECONNECT_DELAY_MS)
      }
    } else if (connection === 'open') {
      console.log('✅ Connected to WhatsApp')
      connectionOpen = true
      lastAliveAt = Date.now()
      startWatchdog()
      // Appear offline — don't show the bot as "online" to contacts
      try {
        await sock.sendPresenceUpdate('unavailable')
      } catch (err) {
        console.warn('Could not set presence to unavailable:', err.message)
      }
    }
  })

  // Invalidate admin cache whenever group membership or roles change
  sock.ev.on('group-participants.update', async ({ id, participants, action }) => {
    invalidateGroup(id)
    if (action === 'add' || action === 'remove' || action === 'leave') {
      try {
        await forwardToBackend({
          type: 'participant_update',
          jid: id,
          sender: '',
          messageId: '',
          isAdmin: false,
          action,
          participants,
        })
      } catch (err) {
        console.error('Failed to forward participant update:', err.message)
      }
    }
  })

  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    // 'notify' = live messages; 'append' = messages synced after reconnect
    // Process images from both (dedup handles duplicates); skip text on 'append'
    // to avoid replaying commands that were sent while offline.
    if (type !== 'notify' && type !== 'append') return

    for (const msg of messages) {
      const inbound = extractInbound(msg, type)
      if (!inbound) continue

      const { jid, sender, messageId, text, directImage, quotedImageMessage, quotedText, quotedRef } = inbound
      if (!isJidGroup(jid)) continue

      // Whitelist check — skip groups not in ALLOWED_GROUPS (if list is configured).
      // Do this BEFORE any read receipts or presence updates so the bot stays invisible
      // to non-allowed chats.
      if (ALLOWED_GROUPS.length > 0 && !ALLOWED_GROUPS.includes(jid)) continue

      try {
        const isAdmin = await isGroupAdmin(sock, jid, sender)

        // A reply that quotes an image (e.g. replying "לאשר"/"approve" to a
        // receipt photo) is handled exactly like sending that image again
        // with the reply text as its caption — otherwise the quoted photo
        // is invisible to the agent and "approve" has nothing to attach to.
        const imageToDownload = directImage || quotedImageMessage
        const downloadSource = directImage ? msg : quotedRef

        if (imageToDownload && downloadSource) {
          // Fire-and-forget: detach image download + forward so the message
          // loop continues immediately and isn't stalled by a slow download.
          ;(async () => {
            try {
              const rawBuffer = await downloadMediaMessage(
                downloadSource,
                'buffer',
                {},
                { logger, reuploadRequest: sock.updateMediaMessage }
              )

              // Resize to max 1920px on the longest edge and convert to JPEG q85
              // before base64-encoding. Keeps the JSON payload small and prevents OOM.
              const compressedBuffer = await sharp(rawBuffer)
                .resize(1920, 1920, { fit: 'inside', withoutEnlargement: true })
                .jpeg({ quality: 85 })
                .toBuffer()

              await forwardToBackend({
                type: 'image',
                jid,
                sender,
                messageId,
                isAdmin,
                pushName: msg.pushName || '',
                imageBase64: compressedBuffer.toString('base64'),
                mimeType: 'image/jpeg',
                caption: directImage ? (imageToDownload.caption || '') : text,
              })
            } catch (err) {
              console.error(`Error processing image ${messageId}:`, err.message)
            }
          })()
        } else if (text && type === 'notify') {
          // Skip replaying text commands from offline sync
          await forwardToBackend({
            type: 'text',
            jid,
            sender,
            messageId,
            isAdmin,
            pushName: msg.pushName || '',
            text,
            quotedText,
          })
        }
      } catch (err) {
        console.error(`Error processing message ${messageId}:`, err.message)
      }
    }
  })
}
