import { useEffect, useRef, useState } from 'react';
import { errorMessage, request } from './api';
import type { RecommendationResponse, RecommendationState } from './types';

export const useRecommendations = () => {
  const [states, setStates] = useState<ReadonlyMap<string, RecommendationState>>(new Map());
  const [busy, setBusy] = useState(false);
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => pending.current?.abort(), []);
  const generate = async (numbers: string[]) => {
    if (pending.current || !numbers.length) return;
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    setStates(previous => {
      const next = new Map(previous);
      for (const number of numbers) next.set(number, { state: 'loading' });
      return next;
    });
    try {
      const response = await request<RecommendationResponse>('/api/claims/recommendations', {
        method: 'POST', body: JSON.stringify({ tracking_numbers: numbers }), signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      setStates(previous => {
        const next = new Map(previous);
        const received = new Set<string>();
        for (const result of response.results) {
          received.add(result.tracking_number);
          next.set(result.tracking_number, result.state === 'succeeded'
            ? { state: 'succeeded', data: result.recommendation }
            : { state: 'error', error: result.error.message });
        }
        for (const number of numbers) {
          if (!received.has(number)) next.set(number, { state: 'error', error: 'No recommendation returned. Retry this claim.' });
        }
        return next;
      });
    } catch (error) {
      if (!controller.signal.aborted) setStates(previous => {
        const next = new Map(previous);
        for (const number of numbers) next.set(number, { state: 'error', error: errorMessage(error) });
        return next;
      });
    } finally { pending.current = null; setBusy(false); }
  };
  return { states, busy, generate };
};
