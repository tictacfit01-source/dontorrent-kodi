/*
 * MejorWolf - Series.ly Cookie Sync
 *
 * Escucha cambios en la cookie 'seriesly_session' de series.ly
 * y la sube automaticamente a Supabase para que el addon Kodi
 * la use en los Android TV Boxes.
 *
 * Flujo:
 *   1. El usuario hace login en series.ly en Chrome (una vez)
 *   2. Esta extension detecta la cookie automaticamente
 *   3. La sube a Supabase via PATCH
 *   4. El addon Kodi en el TV Box la lee de Supabase
 */

const SUPABASE_URL = "https://yddgjpjyldgvuswcsxci.supabase.co";
const SUPABASE_ANON_KEY =
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9." +
  "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InlkZGdqcGp5bGRndnVzd2NzeGNpIiwi" +
  "cm9sZSI6ImFub24iLCJpYXQiOjE3NzgyNTIwMzAsImV4cCI6MjA5MzgyODAzMH0." +
  "bpIkjXUowHhhJKz_HVFkGj1WogD5dpyi_JGL2yLOYl0";

const COOKIE_NAME = "seriesly_session";
const DOMAIN = "series.ly";

// --- Upload cookie to Supabase ---
async function pushToSupabase(cookieValue) {
  const url = `${SUPABASE_URL}/rest/v1/mw_config?key=eq.seriesly_cookie`;
  try {
    const resp = await fetch(url, {
      method: "PATCH",
      headers: {
        apikey: SUPABASE_ANON_KEY,
        Authorization: `Bearer ${SUPABASE_ANON_KEY}`,
        "Content-Type": "application/json",
        Prefer: "return=minimal",
      },
      body: JSON.stringify({ value: { cookie: cookieValue } }),
    });
    if (resp.ok) {
      console.log(
        `[MejorWolf] Cookie synced to Supabase (${cookieValue.length} chars)`
      );
      return true;
    }
    console.error(`[MejorWolf] Supabase PATCH error: ${resp.status}`);
    return false;
  } catch (err) {
    console.error(`[MejorWolf] Supabase error: ${err.message}`);
    return false;
  }
}

// --- Read cookie from Chrome's cookie jar ---
async function readSessionCookie() {
  try {
    const cookie = await chrome.cookies.get({
      url: `https://${DOMAIN}`,
      name: COOKIE_NAME,
    });
    return cookie ? cookie.value : null;
  } catch (err) {
    console.error(`[MejorWolf] Error reading cookie: ${err.message}`);
    return null;
  }
}

// --- Sync: read cookie and push if valid ---
let lastSyncedValue = null;

async function syncCookie() {
  const value = await readSessionCookie();
  if (!value) {
    console.log("[MejorWolf] No seriesly_session cookie found");
    return;
  }
  // Only push if the cookie value changed
  if (value === lastSyncedValue) {
    return;
  }
  console.log(
    `[MejorWolf] Cookie changed (${value.length} chars), syncing...`
  );
  const ok = await pushToSupabase(value);
  if (ok) {
    lastSyncedValue = value;
  }
}

// --- Listen for cookie changes on series.ly ---
chrome.cookies.onChanged.addListener((changeInfo) => {
  const { cookie, removed } = changeInfo;
  if (
    cookie.name === COOKIE_NAME &&
    cookie.domain.includes(DOMAIN) &&
    !removed
  ) {
    console.log(
      `[MejorWolf] Cookie '${COOKIE_NAME}' changed, syncing to Supabase...`
    );
    syncCookie();
  }
});

// --- Also sync on extension startup (in case cookie already exists) ---
chrome.runtime.onInstalled.addListener(() => {
  console.log("[MejorWolf] Extension installed, checking for existing cookie...");
  syncCookie();
});

chrome.runtime.onStartup.addListener(() => {
  console.log("[MejorWolf] Chrome started, checking cookie...");
  syncCookie();
});

// Initial check
syncCookie();
