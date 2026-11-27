/* =====================================================================================
 * The ARCHIVE's database: a read-only stand-in for supabase-js over one JSON file.
 *
 * tools/build_archive.py copies the finished site into site/archive/<version>/ and points that
 * copy's js/supabase.js at this module instead of the CDN client, so the archived pages keep
 * working after the live project is wiped or replaced. It answers the query shapes the site
 * uses (select / eq / in / neq / is / order / limit / range / single), refuses every write,
 * and nobody can sign in.
 * ===================================================================================== */

const DATA_URL = new URL('../data/archive-db.json', import.meta.url);
let _db = null;
const db = () => (_db ||= fetch(DATA_URL).then((r) => r.json()));

const READ_ONLY = { message: 'This is the archived Cheezeyverse - it is read-only.' };

function cmp(a, b) {
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  return a < b ? -1 : a > b ? 1 : 0;
}

class Query {
  constructor(table) {
    this.table = table;
    this.filters = [];
    this.orders = [];
    this.lim = null;
    this.rng = null;
    this.one = null;
    this.write = false;
    this.head = false;
  }
  select(_cols, opts) { if (opts && opts.head) this.head = true; return this; }
  insert() { this.write = true; return this; }
  update() { this.write = true; return this; }
  upsert() { this.write = true; return this; }
  delete() { this.write = true; return this; }
  eq(k, v) { this.filters.push((r) => String(r[k]) === String(v)); return this; }
  neq(k, v) { this.filters.push((r) => String(r[k]) !== String(v)); return this; }
  in(k, vs) { const s = new Set([].concat(vs).map(String)); this.filters.push((r) => s.has(String(r[k]))); return this; }
  is(k, v) { this.filters.push((r) => (v === null ? r[k] == null : r[k] === v)); return this; }
  gt(k, v) { this.filters.push((r) => cmp(r[k], v) > 0); return this; }
  gte(k, v) { this.filters.push((r) => cmp(r[k], v) >= 0); return this; }
  lt(k, v) { this.filters.push((r) => cmp(r[k], v) < 0); return this; }
  lte(k, v) { this.filters.push((r) => cmp(r[k], v) <= 0); return this; }
  match(obj) { for (const [k, v] of Object.entries(obj || {})) this.eq(k, v); return this; }
  not() { return this; }
  or() { return this; }
  filter() { return this; }
  order(k, opts = {}) { this.orders.push([k, opts.ascending !== false]); return this; }
  limit(n) { this.lim = n; return this; }
  range(a, b) { this.rng = [a, b]; return this; }
  single() { this.one = 'single'; return this; }
  maybeSingle() { this.one = 'maybe'; return this; }
  async run() {
    if (this.write) return { data: null, error: READ_ONLY };
    const all = (await db())[this.table] || [];
    let rows = all.filter((r) => this.filters.every((f) => f(r)));
    for (const [k, asc] of [...this.orders].reverse()) {
      rows = [...rows].sort((a, b) => cmp(a[k], b[k]) * (asc ? 1 : -1));
    }
    if (this.rng) rows = rows.slice(this.rng[0], this.rng[1] + 1);
    if (this.lim != null) rows = rows.slice(0, this.lim);
    if (this.head) return { data: null, count: rows.length, error: null };
    if (this.one) {
      const row = rows[0] || null;
      return { data: row, error: !row && this.one === 'single' ? { message: 'not found' } : null };
    }
    return { data: rows, count: rows.length, error: null };
  }
  then(resolve, reject) { return this.run().then(resolve, reject); }
}

export function createClient() {
  return {
    from: (table) => new Query(table),
    rpc: async () => ({ data: null, error: READ_ONLY }),
    auth: {
      getSession: async () => ({ data: { session: null }, error: null }),
      getUser: async () => ({ data: { user: null }, error: null }),
      onAuthStateChange: () => ({ data: { subscription: { unsubscribe() {} } } }),
      signInWithOAuth: async () => ({ error: READ_ONLY }),
      signOut: async () => ({ error: null }),
    },
  };
}

/* A ribbon on every archived page, so nobody mistakes it for the live universe. */
function ribbon() {
  if (document.getElementById('cv-archive-ribbon')) return;
  const label = (window.CV_CONFIG && window.CV_CONFIG.archiveLabel) || 'Archive';
  const bar = document.createElement('div');
  bar.id = 'cv-archive-ribbon';
  bar.textContent = label;
  bar.style.cssText = 'position:sticky;top:0;z-index:9999;padding:6px 16px;text-align:center;'
    + 'font:600 13px/1.4 system-ui,sans-serif;background:#3b2f12;color:#ffd970;letter-spacing:.02em';
  document.body.prepend(bar);
}
if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ribbon);
  else ribbon();
}
