import makeWASocket, {
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
  downloadMediaMessage,
  isJidGroup,
} from '@whiskeysockets/baileys'
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
// Raw QR pairing string for the currently-displayed code, if any — set on
// every 'qr' update, cleared once connected. Lets /qr (server.js) hand back
// the freshest code on demand instead of only via the throttled email (at
// most once per QR_EMAIL_MIN_INTERVAL_MS) or having to catch it in the logs
// before Baileys rotates it (every ~20-30s while unscanned).
let currentQr = null

export function getSocket() {
  return sock
}

export function getCurrentQr() {
  return currentQr
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
      currentQr = qr
      console.log('\n📱 Scan this QR code with WhatsApp:\n')
      qrcode.generate(qr, { small: true })
      // No longer emails a fresh QR on every ~20-30s rotation (was, even
      // throttled to once per 10 minutes, hundreds of emails over an
      // extended outage — to an inbox nobody was watching, so it just
      // burned through send quota for nothing). The orchestrator's
      // _check_bridge_health job (app/scheduler.py) now owns notification:
      // one SMS after a grace period, repeating at most once per 24h until
      // reconnected or dismissed. An operator pulls the live code on demand
      // via GET /qr (this same currentQr) from the admin panel instead of
      // waiting for one to arrive passively.
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
      currentQr = null
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
