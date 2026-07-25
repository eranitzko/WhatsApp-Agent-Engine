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
import { isGroupAdmin, invalidateGroup } from './adminCache.js'
import { forwardToBackend } from './forwarder.js'

const AUTH_PATH = process.env.AUTH_PATH || './auth'
const RECONNECT_DELAY_MS = 3000

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
      console.log('RAW_QR_DATA:' + qr)
      // Fire-and-forget: notify backend to email QR to admin
      axios.post(`${process.env.BACKEND_URL}/internal/qr-notify`, { qr }).catch((err) => {
        console.warn('Could not send QR notification:', err.message)
      })
    }

    if (connection === 'close') {
      const statusCode = lastDisconnect?.error?.output?.statusCode
      const loggedOut = statusCode === DisconnectReason.loggedOut

      if (loggedOut) {
        console.error('Logged out from WhatsApp. Exiting so watchdog can restart and show a fresh QR.')
        process.exit(1)
      } else {
        console.log(`Connection closed (code ${statusCode}). Reconnecting in ${RECONNECT_DELAY_MS}ms...`)
        setTimeout(connect, RECONNECT_DELAY_MS)
      }
    } else if (connection === 'open') {
      console.log('✅ Connected to WhatsApp')
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
      if (!msg.message) continue
      if (msg.key.fromMe) continue

      const jid = msg.key.remoteJid
      if (!isJidGroup(jid)) continue

      // Whitelist check — skip groups not in ALLOWED_GROUPS (if list is configured).
      // Do this BEFORE any read receipts or presence updates so the bot stays invisible
      // to non-allowed chats.
      if (ALLOWED_GROUPS.length > 0 && !ALLOWED_GROUPS.includes(jid)) continue

      const sender = msg.key.participant || jid
      const messageId = msg.key.id

      try {
        const isAdmin = await isGroupAdmin(sock, jid, sender)
        const imageMessage = msg.message?.imageMessage
        const text =
          msg.message?.conversation ||
          msg.message?.extendedTextMessage?.text ||
          ''

        if (imageMessage) {
          // Fire-and-forget: detach image download + forward so the message
          // loop continues immediately and isn't stalled by a slow download.
          ;(async () => {
            try {
              const rawBuffer = await downloadMediaMessage(
                msg,
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
                caption: imageMessage.caption || '',
              })
            } catch (err) {
              console.error(`Error processing image ${messageId}:`, err.message)
            }
          })()
        } else if (text.trim() && type === 'notify') {
          // Skip replaying text commands from offline sync
          await forwardToBackend({
            type: 'text',
            jid,
            sender,
            messageId,
            isAdmin,
            pushName: msg.pushName || '',
            text: text.trim(),
          })
        }
      } catch (err) {
        console.error(`Error processing message ${messageId}:`, err.message)
      }
    }
  })
}
