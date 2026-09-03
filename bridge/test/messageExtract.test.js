import { test } from 'node:test'
import assert from 'node:assert/strict'
import { extractInbound } from '../src/messageExtract.js'

const JID = '120363428765743232@g.us'
const SENDER = '972501234567@lid'

function baseKey(overrides = {}) {
  return { remoteJid: JID, participant: SENDER, id: 'MSG1', fromMe: false, ...overrides }
}

test('plain text message has no quoted content', () => {
  const msg = { key: baseKey(), message: { conversation: 'hello' } }
  const out = extractInbound(msg, 'notify')
  assert.equal(out.text, 'hello')
  assert.equal(out.quotedText, '')
  assert.equal(out.quotedImageMessage, null)
})

test('reply quoting a plain text message surfaces the quoted text', () => {
  const msg = {
    key: baseKey(),
    message: {
      extendedTextMessage: {
        text: 'לאשר',
        contextInfo: {
          stanzaId: 'QUOTED1',
          participant: '972509999999@lid',
          quotedMessage: { conversation: 'העברתי 570 לעדן' },
        },
      },
    },
  }
  const out = extractInbound(msg, 'notify')
  assert.equal(out.text, 'לאשר')
  assert.equal(out.quotedText, 'העברתי 570 לעדן')
  assert.equal(out.quotedImageMessage, null)
})

test('reply quoting an image message surfaces the quoted image and a download ref', () => {
  const quotedImageMessage = { mimetype: 'image/jpeg', caption: 'קבלה' }
  const msg = {
    key: baseKey(),
    message: {
      extendedTextMessage: {
        text: 'לאשר',
        contextInfo: {
          stanzaId: 'QUOTED2',
          participant: '972509999999@lid',
          quotedMessage: { imageMessage: quotedImageMessage },
        },
      },
    },
  }
  const out = extractInbound(msg, 'notify')
  assert.equal(out.text, 'לאשר')
  assert.equal(out.quotedImageMessage, quotedImageMessage)
  assert.deepEqual(out.quotedRef, {
    key: { remoteJid: JID, id: 'QUOTED2', fromMe: false, participant: '972509999999@lid' },
    message: { imageMessage: quotedImageMessage },
  })
})

test('direct image message is unaffected by quoted-context extraction', () => {
  const imageMessage = { mimetype: 'image/jpeg', caption: 'invoice' }
  const msg = { key: baseKey(), message: { imageMessage } }
  const out = extractInbound(msg, 'notify')
  assert.equal(out.directImage, imageMessage)
  assert.equal(out.quotedImageMessage, null)
  assert.equal(out.quotedText, '')
})

test('messages sent by the bot itself are ignored', () => {
  const msg = { key: baseKey({ fromMe: true }), message: { conversation: 'hi' } }
  assert.equal(extractInbound(msg, 'notify'), null)
})

test('messages with no content are ignored', () => {
  const msg = { key: baseKey(), message: null }
  assert.equal(extractInbound(msg, 'notify'), null)
})
