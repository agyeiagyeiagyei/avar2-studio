/**
 * Session persistence — the uploaded workspace in IndexedDB.
 *
 * One record (`current`) holds the live uploadDataset's serializable
 * state (source, font bytes, authoring CSV, sidecars, zip entries), so
 * a reload auto-restores where the designer left off WITHOUT a
 * recompile. Persistence is best-effort: quota/IDB failures warn and
 * editing continues; a version mismatch or corrupt record wipes and
 * boots as if nothing was stored.
 *
 * Snapshots/examples are never persisted — they reload free from
 * static files, and loading one clears the stored session.
 */

const DB_NAME = 'avar2-studio';
const STORE = 'session';
const KEY = 'current';
export const SESSION_VERSION = 1;

const openDb = () => new Promise((resolve, reject) => {
  const req = indexedDB.open(DB_NAME, 1);
  req.onupgradeneeded = () => req.result.createObjectStore(STORE);
  req.onsuccess = () => resolve(req.result);
  req.onerror = () => reject(req.error);
});

const tx = async (mode, fn) => {
  const db = await openDb();
  try {
    return await new Promise((resolve, reject) => {
      const t = db.transaction(STORE, mode);
      const out = fn(t.objectStore(STORE));
      t.oncomplete = () => resolve(out.result ?? undefined);
      t.onerror = () => reject(t.error);
      t.onabort = () => reject(t.error);
    });
  } finally {
    db.close();
  }
};

export const saveSession = (record) =>
  tx('readwrite', store => store.put(record, KEY));

export const loadSession = () =>
  tx('readonly', store => store.get(KEY));

export const clearSession = () =>
  tx('readwrite', store => store.delete(KEY));
