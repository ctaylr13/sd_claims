import { useEffect, useState } from 'react';
import { errorMessage, request } from './api';

export const useResource = <T>(path: string) => {
  const [resource, setResource] = useState<{ path: string; data: T | null; error: string | null } | null>(null);
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    request<T>(path, { signal: controller.signal }).then(data => setResource({ path, data, error: null }))
      .catch(error => { if (!controller.signal.aborted) setResource({ path, data: null, error: errorMessage(error) }); });
    return () => controller.abort();
  }, [path, version]);
  const current = resource?.path === path ? resource : null;
  return { data: current?.data ?? null, error: current?.error ?? null, reload: () => { setResource(null); setVersion(value => value + 1); } };
};
