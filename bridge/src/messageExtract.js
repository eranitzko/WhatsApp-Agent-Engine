// Pure extraction of the fields the bridge forwards to the backend, kept
// separate from connection.js so it can be unit-tested without a live
// Baileys socket.
//
// Root cause this exists for: WhatsApp's "reply" (quote) feature attaches
// the quoted message under contextInfo.quotedMessage, but the bridge only
// ever read msg.message.conversation / extendedTextMessage.text — the
// quoted content was silently discarded. A user replying "לאשר" (approve)
// to an earlier message/image therefore reached the agent as a bare,
// context-free "לאשר" with nothing to approve.

function textOf(message) {
  if (!message) return ''
  return message.conversation || message.extendedTextMessage?.text || ''
}

export function extractInbound(msg, type) {
  if (!msg.message) return null
  if (msg.key.fromMe) return null

  const jid = msg.key.remoteJid
  const sender = msg.key.participant || jid
  const messageId = msg.key.id

  const directImage = msg.message.imageMessage || null
  const text = textOf(msg.message).trim()

  const contextInfo = msg.message.extendedTextMessage?.contextInfo || null
  const quotedMessage = contextInfo?.quotedMessage || null
  const quotedImageMessage = quotedMessage?.imageMessage || null
  const quotedText = textOf(quotedMessage).trim()

  let quotedRef = null
  if (contextInfo?.stanzaId && quotedMessage) {
    quotedRef = {
      key: {
        remoteJid: jid,
        id: contextInfo.stanzaId,
        fromMe: false,
        participant: contextInfo.participant,
      },
      message: quotedMessage,
    }
  }

  return {
    jid,
    sender,
    messageId,
    type,
    text,
    directImage,
    quotedImageMessage,
    quotedText,
    quotedRef,
  }
}
