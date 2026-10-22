export const request = async <T>(path: string, options: RequestInit = {}): Promise<T> => {
  const response = await fetch(path, { ...options, headers: { 'Content-Type': 'application/json', ...options.headers } });
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new Error(data?.error?.message ?? `Request failed (${response.status}). Try again.`);
  if (data === null) throw new Error('The API returned an unreadable response. Check that the backend is running.');
  return data as T;
};
export const errorMessage = (error: unknown) => error instanceof Error ? error.message : 'Something went wrong. Try again.';
export const label = (value: string) => value.replaceAll('_', ' ').replace(/^./, value => value.toUpperCase());
export const money = (value: number | string | null) => value === null ? 'Unavailable' : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(Number(value));
export const percentage = (value: number | null) => value === null ? 'Unavailable' : `${(value * 100).toFixed(1)}%`;
export const driveUrl = (id: string) => `https://drive.google.com/file/d/${encodeURIComponent(id)}/view`;
export const documentUrl = (batchId: string, index: number, documentIndex: number) => `/api/batches/${encodeURIComponent(batchId)}/claims/${index}/documents/${documentIndex}`;
