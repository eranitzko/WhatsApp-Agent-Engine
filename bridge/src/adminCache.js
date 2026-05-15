// Cache group admin lists to avoid fetching metadata on every message.
// TTL: 5 minutes. Invalidated on any admin-change event.

const cache = new Map() // jid -> { admins: Set<string>, expiry: number }
const CACHE_TTL_MS = 5 * 60 * 1000

export async function isGroupAdmin(sock, groupJid, senderJid) {
  const now = Date.now()
  const entry = cache.get(groupJid)

  if (entry && entry.expiry > now) {
    return entry.admins.has(senderJid)
  }

  try {
    const metadata = await sock.groupMetadata(groupJid)
    const admins = new Set(
      metadata.participants
        .filter((p) => p.admin === 'admin' || p.admin === 'superadmin')
        .map((p) => p.id)
    )
    cache.set(groupJid, { admins, expiry: now + CACHE_TTL_MS })
    return admins.has(senderJid)
  } catch (err) {
    console.error(`Failed to fetch group metadata for ${groupJid}:`, err.message)
    return false
  }
}

export function invalidateGroup(groupJid) {
  cache.delete(groupJid)
}
