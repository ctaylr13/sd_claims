import { useRef, useState } from 'react';
import { errorMessage, request } from './api';
import type { LookupResult } from './types';
import { parseTrackingNumbers } from './trackingNumbers';

export const useLookup = () => {
  const [input, setInput] = useState('');
  const [lookupId, setLookupId] = useState(0);
  const [results, setResults] = useState<LookupResult[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const active = useRef(false);
  const lookup = async () => {
    if (active.current) return;
    let numbers: string[];
    try { numbers = parseTrackingNumbers(input); }
    catch (error) { setError(errorMessage(error)); return; }
    active.current = true;
    setBusy(true);
    setError(null);
    try {
      const data = await request<{ results: LookupResult[] }>('/api/claims/lookup', { method: 'POST', body: JSON.stringify({ tracking_numbers: numbers }) });
      setResults(data.results);
      setLookupId(value => value + 1);
      setSelected(data.results[0]?.tracking_number ?? null);
    } catch (error) { setError(errorMessage(error)); }
    finally { active.current = false; setBusy(false); }
  };
  return { input, setInput, results, selected, setSelected, error, busy, lookup, lookupId };
};
