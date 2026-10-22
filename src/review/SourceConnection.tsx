import { Lookup } from './Lookup';
import { useResource } from './useResource';
import { Badge, Code, Copy, Details, Heading, Notice, Panel, Secondary, Spread, Stack, Summary } from './styles';

type SourceStatus = { mode: 'drive' | 'local' | 'disconnected'; name: string | null };

export const SourceConnection = () => {
  const { data, error, reload } = useResource<SourceStatus>('/api/source');
  return <Stack>
    <Panel><Stack>
      <Spread><Heading>Claim source</Heading>{data && <Badge>{data.mode === 'drive' ? 'Google Drive' : data.mode === 'local' ? 'Local files' : 'Setup needed'}</Badge>}</Spread>
      {!data && !error && <Copy>Checking the claim source…</Copy>}
      {data?.mode === 'drive' && <Copy>Using your authorized Google Drive account for claims and evidence.</Copy>}
      {data?.mode === 'local' && <Copy>Using {data.name} and the local evidence folder.</Copy>}
      {data && data.mode !== 'drive' && <Details open={data.mode === 'disconnected'}><Summary>Where to place downloaded files</Summary><Stack>
        <Copy>Google Drive is not connected. Place downloaded files in the repository’s local data folder. Unzip folder downloads first.</Copy>
        <pre><Code>{`data/
  claims.csv
  evidence/
    1/
      lease.pdf
      ledger.pdf
    2/
      itemization.pdf`}</Code></pre>
        <Copy>Export the claims spreadsheet as CSV, keep its original headers including Tracking Number, and save it as data/claims.csv. Name each evidence folder for the matching tracking number. PDFs can be inside nested folders.</Copy>
        <Copy>Click Check connection after placing the files, then look up a tracking number from the CSV. No restart is needed. Files stay on your computer.</Copy>
      </Stack></Details>}
      {error && <Notice role="alert">{error}</Notice>}
      <div><Secondary onClick={reload}>Check connection</Secondary></div>
    </Stack></Panel>
    {data && data.mode !== 'disconnected' && <Lookup key={data.mode} />}
  </Stack>;
};
