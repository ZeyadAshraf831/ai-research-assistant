import { request, ApiError } from './client.js';

export async function getHealth(opts = {}) {
  const raw = await request('/api/health', opts);
  if (!raw || typeof raw !== 'object') throw new ApiError('Unexpected response from the health API.', { kind: 'invalid_response' });
  return raw;
}

const has = (v) => v !== null && v !== undefined;

/** Turns the raw /api/health payload into display rows. Never claims health that the payload does not state. */
export function describeHealth(raw = {}) {
  const c = [];
  const add = (key, label, state, detail) => c.push({ key, label, state, detail });
  add('api', 'API', 'ok', 'Reachable. This only means the web server answered; see the other checks for readiness.');

  const o = raw.ollama;
  add('ollama', 'Ollama (answer generation)', o === 'up' ? 'ok' : o === 'unreachable' || o === 'error' ? 'bad' : 'unknown',
    o === 'up' ? 'Reachable' : o === 'unreachable' ? 'Not reachable. Start Ollama.' : o === 'error' ? 'Returned an error' : 'Not reported');
  const cm = raw.chat_model;
  add('chat_model', 'Chat model', cm === 'available' ? 'ok' : cm === 'missing' ? 'bad' : 'unknown',
    `${raw.chat_model_name ?? 'unknown model'} — ${cm === 'available' ? 'installed' : cm === 'missing' ? 'not installed (run: ollama pull ' + (raw.chat_model_name ?? 'MODEL') + ')' : 'status unknown'}`);

  const em = raw.embedding_model;
  const emState = { loaded: 'ok', cached: 'ok', not_cached: 'warn', unavailable: 'bad' }[em] ?? 'unknown';
  const emText = { loaded: 'loaded in memory', cached: 'cached on disk (loads on first use)', not_cached: 'not downloaded yet (first use will download it)', unavailable: 'unavailable (packages missing?)' }[em] ?? 'status not reported';
  const emBits = [raw.embedding_model_name, raw.embedding_provider, has(raw.embedding_dimension) ? `${raw.embedding_dimension} dims` : null, raw.embedding_device ? `device: ${raw.embedding_device}` : null].filter(Boolean);
  add('embedding', 'Embedding model', emState, `${emBits.join(' · ') || 'unknown'} — ${emText}`);

  const db = raw.database;
  add('database', 'Database', db === 'up' ? 'ok' : db === 'down' ? 'bad' : 'unknown', db === 'up' ? 'Connected' : db === 'down' ? 'Not reachable' : 'Not reported');
  const pv = raw.pgvector;
  add('pgvector', 'pgvector extension', pv === 'installed' ? 'ok' : pv === 'missing' ? 'bad' : 'unknown', pv ?? 'Not reported');

  if (!has(raw.schema_dimension)) add('schema', 'Vector column size', 'unknown', 'Unknown (tables missing or not migrated yet?)');
  else if (raw.schema_dimension === raw.embedding_dimension) add('schema', 'Vector column size', 'ok', `${raw.schema_dimension} — matches the embedding model`);
  else add('schema', 'Vector column size', 'bad', `Database column is ${raw.schema_dimension}, model produces ${raw.embedding_dimension ?? '?'}. Run migrations.`);

  const p = raw.chunks_pending_reindex;
  add('reindex', 'Chunks pending re-index', !has(p) ? 'unknown' : p === 0 ? 'ok' : 'warn',
    !has(p) ? 'Unknown' : p === 0 ? 'None' : `${p} chunk(s) are not searchable until re-indexing finishes`);

  const bad = c.some((x) => x.state === 'bad'), warn = c.some((x) => x.state === 'warn');
  const overall = raw.status === 'ok' && !bad && !warn ? 'ready' : 'degraded';
  return { overall, components: c, maxPdfMb: Number.isInteger(raw.max_pdf_mb) ? raw.max_pdf_mb : null };
}
