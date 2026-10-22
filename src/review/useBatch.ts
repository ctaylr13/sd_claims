import { useEffect, useRef, useState } from 'react';
import { errorMessage, request } from './api';
import type { Batch } from './types';

const savedBatch = () => {
  try { return localStorage.getItem('claims.batch'); } catch { return null; }
};
const rememberBatch = (id: string | null) => {
  try {
    if (id) localStorage.setItem('claims.batch', id);
    else localStorage.removeItem('claims.batch');
  } catch { /* Storage can be disabled. */ }
};
export const useBatch = () => {
  const [manifest, setManifest] = useState('');
  const [retry, setRetry] = useState(false);
  const [batchId, setBatchId] = useState<string | null>(savedBatch);
  const [resumeId, setResumeId] = useState('');
  const [batch, setBatch] = useState<Batch | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pollVersion, setPollVersion] = useState(0);
  const submitting = useRef(false);
  useEffect(() => {
    if (!batchId) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const progress = await request<Batch>(`/api/batches/${encodeURIComponent(batchId)}`, { signal: controller.signal });
        setBatch(progress);
        setError(null);
        if (progress.state === 'queued' || progress.state === 'running') timer = setTimeout(poll, 1500);
      } catch (error) { if (!controller.signal.aborted) setError(errorMessage(error)); }
    };
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [batchId, pollVersion]);
  const submit = async () => {
    if (submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setError(null);
    try {
      const parsed: unknown = JSON.parse(manifest);
      const body = Array.isArray(parsed) ? { claims: parsed, retry } : { ...parsed as object, retry };
      const created = await request<Batch>('/api/batches', { method: 'POST', body: JSON.stringify(body) });
      setBatch(created);
      setBatchId(created.batch_id);
      rememberBatch(created.batch_id);
      setPollVersion(value => value + 1);
    } catch (error) { setError(error instanceof SyntaxError ? 'Enter valid manifest JSON: an array of claims or an object with a claims array.' : errorMessage(error)); }
    finally { submitting.current = false; setBusy(false); }
  };
  const resume = () => {
    const id = resumeId.trim();
    if (!/^[a-f0-9]{32}$/.test(id)) { setError('Enter a valid 32-character batch ID.'); return; }
    setBatch(null);
    setError(null);
    setBatchId(id);
    rememberBatch(id);
    setPollVersion(value => value + 1);
  };
  const refresh = () => { setError(null); setPollVersion(value => value + 1); };
  const close = () => { setBatchId(null); setBatch(null); setError(null); rememberBatch(null); };
  return { manifest, setManifest, retry, setRetry, batchId, batch, busy, error, submit, resumeId, setResumeId, resume, refresh, close };
};
