import { useState } from 'react';
import { label } from './api';
import { useBatch } from './useBatch';
import { Evidence } from './Evidence';
import { WorkflowProgress } from './WorkflowProgress';
import type { Batch } from './types';
import { Badge, Button, Code, Columns, Copy, Details, Empty, Field, Grid, Heading, Input, Item, List, Notice, Panel, Row, Secondary, Small, Spread, Stack, Stat, StatValue, Summary, Textarea } from './styles';

const BatchResults = ({ batch }: { batch: Batch }) => {
  const [selected, setSelected] = useState(0);
  const current = batch.results[selected];
  return <Stack>
    <Panel><Spread><Heading>Batch progress</Heading><Badge data-state={batch.state}>{label(batch.state)}</Badge></Spread><WorkflowProgress label="Claims attempted" completed={batch.completed} total={batch.total} detail={`${batch.succeeded} succeeded · ${batch.failed} failed. Claims run sequentially; completed batches can contain failures.`} /><Code>Batch ID: {batch.batch_id}</Code>{batch.error && <Notice role="alert">{label(batch.error)}. Review individual claim states and resubmit manifests as needed.</Notice>}</Panel>
    <Grid><Stat><Small>Total claims</Small><StatValue>{batch.total}</StatValue></Stat><Stat><Small>Extraction succeeded</Small><StatValue>{batch.succeeded}</StatValue></Stat><Stat><Small>Extraction failed</Small><StatValue>{batch.failed}</StatValue></Stat><Stat><Small>Awaiting completion</Small><StatValue>{batch.total - batch.completed}</StatValue></Stat></Grid>
    <Columns><Panel><Heading>Claims</Heading><List>{batch.results.map((result, index) => <Item key={result.claim_id} aria-pressed={selected === index} onClick={() => setSelected(index)}><Spread><strong>{result.claim_id}</strong><Badge data-state={result.state}>{label(result.state)}</Badge></Spread>{result.error && <Small>{label(result.error)}</Small>}</Item>)}</List></Panel>
      {current?.state === 'succeeded' ? <Evidence key={`${batch.batch_id}-${selected}`} batchId={batch.batch_id} index={selected} /> : <Panel><Heading>{current?.claim_id ?? 'Select a claim'}</Heading>{current?.state === 'failed' ? <><Notice role="alert">{label(current.error ?? 'Extraction failed')}</Notice><Copy>Inspect the source paths and manifest. To retry a failed, interrupted, or damaged extraction, resubmit its manifest with explicit retry enabled.</Copy></> : <Copy>Extraction {current?.state ?? 'unavailable'}. The report will appear when this claim finishes. You can inspect completed claims while processing continues.</Copy>}</Panel>}
    </Columns>
  </Stack>;
};
export const BatchReview = () => {
  const { manifest, setManifest, retry, setRetry, batchId, batch, busy, error, submit, resumeId, setResumeId, resume, refresh, close } = useBatch();
  const running = batch?.state === 'running' || batch?.state === 'queued';
  return <Stack>
    <Panel><Heading>Review document batches</Heading><Copy>Submit local claim manifests to extract facts, inspect row outcomes, and trace observations to retained PDFs.</Copy>
      <Details open={!batch}><Summary>Submit a batch · up to 20 claims</Summary><form onSubmit={event => { event.preventDefault(); void submit(); }}><Stack>
        <Field>Claim manifests (JSON)<Textarea value={manifest} onChange={event => setManifest(event.target.value)} required spellCheck={false} placeholder={'[{"claim_id": "your-claim", "documents": [{"document_id": "ledger", "kind": "ledger", "path": "/absolute/path/ledger.pdf"}]}]'} /></Field>
        <Small>Paths refer to files on the API machine. Supported kinds: ledger, move_out_statement, lease, renewal.</Small>
        <Row><input id="retry" type="checkbox" checked={retry} onChange={event => setRetry(event.target.checked)} /><label htmlFor="retry">Explicitly retry failed, interrupted, or damaged extraction jobs</label></Row>
        <Row><Button type="submit" disabled={busy || running}>{busy ? 'Submitting…' : 'Start extraction →'}</Button>{running && <Small>A batch is active. Wait for completion before submitting another.</Small>}</Row>
      </Stack></form></Details>
      <Details><Summary>Resume an existing batch</Summary><form onSubmit={event => { event.preventDefault(); resume(); }}><Stack><Field>Batch ID<Input value={resumeId} onChange={event => setResumeId(event.target.value)} placeholder="32-character batch ID" required /></Field><Row><Secondary type="submit">Load batch</Secondary></Row></Stack></form><Small>Progress survives API restarts. Interrupted processing requires explicit resubmission; loading a batch does not restart it.</Small></Details>
      {batchId && <Row><Secondary onClick={close}>Close batch view</Secondary><Small>Closing the view does not cancel extraction. Save the batch ID to resume.</Small></Row>}
      {busy && <WorkflowProgress label="Submitting extraction batch" completed={null} total={1} />}
      {error && <Notice role="alert"><Stack><Copy>{error}</Copy><Row><Secondary onClick={refresh}>Refresh batch progress</Secondary></Row></Stack></Notice>}
    </Panel>
    {batch ? <BatchResults key={batch.batch_id} batch={batch} /> : <Empty><Heading>Follow every claim from source to result</Heading><Copy>Submit a manifest or resume a batch to see live progress, verified results, and cited evidence.</Copy></Empty>}
  </Stack>;
};
