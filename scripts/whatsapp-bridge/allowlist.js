import path from 'path';
import { existsSync, readFileSync } from 'fs';

export function normalizeWhatsAppIdentifier(value) {
  return String(value || '')
    .trim()
    .replace(/:.*@/, '@')
    .replace(/@.*/, '')
    .replace(/^\+/, '');
}

export function parseAllowedUsers(rawValue) {
  return new Set(
    String(rawValue || '')
      .split(',')
      .map((value) => normalizeWhatsAppIdentifier(value))
      .filter(Boolean)
  );
}

// A WhatsApp identifier is a phone number or a LID — always a bare token,
// never a path. normalizeWhatsAppIdentifier() strips the @-suffix, the
// :-prefix and a leading +, but it does not touch separators or `..`, so
// without this guard `../../../etc/passwd` survives normalization and
// path.join() resolves it to /etc/passwd.json: the `lid-mapping-` prefix is
// itself consumed as a path segment. Anchored here rather than in the
// normalizer so the invariant sits next to the filesystem call and a future
// caller cannot route around it.
const SAFE_IDENTIFIER = /^[A-Za-z0-9_-]+$/;

function readMappingFile(sessionDir, identifier, suffix = '') {
  if (!SAFE_IDENTIFIER.test(identifier)) {
    return null;
  }

  const filePath = path.join(sessionDir, `lid-mapping-${identifier}${suffix}.json`);
  if (!existsSync(filePath)) {
    return null;
  }

  try {
    const parsed = JSON.parse(readFileSync(filePath, 'utf8'));
    const normalized = normalizeWhatsAppIdentifier(parsed);
    return normalized || null;
  } catch {
    return null;
  }
}

export function expandWhatsAppIdentifiers(identifier, sessionDir) {
  const normalized = normalizeWhatsAppIdentifier(identifier);
  if (!normalized) {
    return new Set();
  }

  // Walk both phone->LID and LID->phone mapping files so allowlists can use
  // either form transparently in bot mode.
  const resolved = new Set();
  const queue = [normalized];

  while (queue.length > 0) {
    const current = queue.shift();
    if (!current || resolved.has(current)) {
      continue;
    }

    resolved.add(current);

    for (const suffix of ['', '_reverse']) {
      const mapped = readMappingFile(sessionDir, current, suffix);
      if (mapped && !resolved.has(mapped)) {
        queue.push(mapped);
      }
    }
  }

  return resolved;
}

export function matchesAllowedUser(senderId, allowedUsers, sessionDir) {
  // Empty allowlist = NO ONE allowed (secure default, #8389).  Operators
  // who want an open bot must set ``WHATSAPP_ALLOWED_USERS=*`` explicitly.
  // Previous behaviour (empty → return true) let any stranger DM the
  // bridge and trigger a Python-side pairing-code reply.
  if (!allowedUsers || allowedUsers.size === 0) {
    return false;
  }

  // "*" means allow everyone (consistent with SIGNAL_GROUP_ALLOWED_USERS)
  if (allowedUsers.has('*')) {
    return true;
  }

  const aliases = expandWhatsAppIdentifiers(senderId, sessionDir);
  for (const alias of aliases) {
    if (allowedUsers.has(alias)) {
      return true;
    }
  }

  return false;
}
