// Path parts that reach the shared prototype instead of the object's own
// properties. Walking into one of these and assigning pollutes every object in
// the realm, so both helpers below reject them outright. Mirrors the guards in
// apps/desktop/src/app/settings/helpers.ts — keep the two in sync.
const POLLUTING_PATH_PARTS = new Set(["__proto__", "constructor", "prototype"]);

function isSafePart(part: string): boolean {
  return part.length > 0 && !POLLUTING_PATH_PARTS.has(part);
}

function configPathParts(path: string): string[] {
  const parts = path.split(".");

  if (!parts.every(isSafePart)) {
    throw new Error(`Unsafe config path: ${path}`);
  }

  return parts;
}

function safeSet(target: Record<string, unknown>, key: string, value: unknown): void {
  if (!isSafePart(key)) {
    throw new Error(`Unsafe config key: ${key}`);
  }

  Object.defineProperty(target, key, {
    value,
    writable: true,
    enumerable: true,
    configurable: true,
  });
}

export function getNestedValue(obj: Record<string, unknown>, path: string): unknown {
  const parts = configPathParts(path);
  let cur: unknown = obj;
  for (const p of parts) {
    if (cur == null || typeof cur !== "object") return undefined;
    // Own properties only — an inherited hit would leak prototype state into
    // what callers treat as config.
    if (!Object.prototype.hasOwnProperty.call(cur, p)) return undefined;
    cur = (cur as Record<string, unknown>)[p];
  }
  return cur;
}

export function setNestedValue(obj: Record<string, unknown>, path: string, value: unknown): Record<string, unknown> {
  const clone = structuredClone(obj);
  const parts = configPathParts(path);
  let cur: Record<string, unknown> = clone;
  for (let i = 0; i < parts.length - 1; i++) {
    const part = parts[i];
    const existing = Object.prototype.hasOwnProperty.call(cur, part) ? cur[part] : undefined;

    if (existing == null || typeof existing !== "object") {
      safeSet(cur, part, {});
    }

    cur = cur[part] as Record<string, unknown>;
  }
  safeSet(cur, parts[parts.length - 1], value);
  return clone;
}
