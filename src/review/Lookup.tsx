import { Fragment } from 'react';
import { driveUrl, label, money } from './api';
import { Comparison, RecordedSummary } from './Comparison';
import { useRecommendations } from './useRecommendations';
import { WorkflowProgress } from './WorkflowProgress';
import { useLookup } from './useLookup';
import { Recommendation } from './Recommendation';
import type { Claim, LookupResult, RecommendationState } from './types';
import { Badge, Button, Code, Copy, Definition, Details, Field, Heading, Link, List, Notice, Panel, Row, Small, Spread, Stack, Summary, Term, Textarea, Value, ComparisonScroll, ComparisonRow, ClaimAnchor, Table, Th, Td } from './styles';

const ClaimReview = ({ claim, recommendation, onGenerate, busy }: { claim: Claim; recommendation: RecommendationState | null; onGenerate: () => void; busy: boolean }) => <Stack>
  <Panel>
    <Spread><Heading>Claim {claim.tracking_number}</Heading><Badge data-state="Needs review">Needs review</Badge></Spread>
    <Details><Summary>Recorded claim details</Summary><Stack>
      <Copy>These recorded inputs have not been independently verified.</Copy>
      <Definition>{Object.entries(claim.prediction_inputs).map(([key, field]) => <Fragment key={key}><Term>{label(key)}</Term><Value>{field.recorded_value || 'Missing'}</Value></Fragment>)}</Definition>
      <Small>Source: {claim.claims_source.name ?? claim.claims_source.id ?? 'Claims register'} · Read {claim.claims_source.read_at ? new Date(claim.claims_source.read_at).toLocaleString() : 'time unavailable'}</Small>
      <Code>Source ID: {claim.claims_source.id ?? 'Unavailable'} · Version: {claim.claims_source.version ?? 'Unavailable'}</Code>
    </Stack></Details>
  </Panel>
  <Recommendation state={recommendation} onGenerate={onGenerate} disabled={busy} />
  <Panel>
    <Spread><Heading>Evidence</Heading><Badge>{claim.evidence.items.length} items</Badge></Spread>
    {claim.evidence.state === 'missing' && <Notice>No evidence folder was found. Request source documents before reviewing this claim.</Notice>}
    {claim.evidence.folder && <Link href={`https://drive.google.com/drive/folders/${encodeURIComponent(claim.evidence.folder.id)}`} target="_blank" rel="noreferrer">Open evidence folder ↗</Link>}
    {!claim.evidence.items.length && claim.evidence.state !== 'missing' && <Copy>The evidence folder is empty.</Copy>}
    {claim.evidence.items.length > 0 && <Details><Summary>Documents · {claim.evidence.items.length}</Summary><List>{claim.evidence.items.map(item => <Row key={item.id}><Link href={driveUrl(item.id)} target="_blank" rel="noreferrer">{item.name} ↗</Link><Small>{item.mimeType?.split('/').at(-1)}</Small></Row>)}</List></Details>}
  </Panel>
  <Panel><Details>
    <Summary>Recorded outcome and comparison</Summary>
    <Stack>
      <Copy>Historical reference only. These outcomes are not model inputs or independently verified answers.</Copy>
      {recommendation?.state === 'succeeded'
        ? <Comparison comparison={recommendation.data.historical_comparison} />
        : <RecordedSummary claim={claim} />}
      <Details>
        <Summary>All historical fields</Summary>
        <Definition>{Object.entries(claim.historical_outcome).map(([key, value]) => <Fragment key={key}>
          <Term>{key}</Term><Value>{value || 'Not recorded'}</Value>
        </Fragment>)}</Definition>
      </Details>
    </Stack>
  </Details></Panel>
</Stack>;

const LookupResultRow = ({ result, recommendation, selected, onSelect }: {
  result: LookupResult;
  recommendation: RecommendationState | undefined;
  selected: boolean;
  onSelect: (number: string) => void;
}) => {
  const number = result.tracking_number;
  const data = recommendation?.state === 'succeeded' ? recommendation.data : null;
  const claim = result.state === 'found' ? result.claim : null;
  const status = data ? data.historical_comparison.recorded_status : claim?.historical_outcome.Status;
  const pendingLabel = recommendation?.state === 'loading' ? 'Generating…' : 'Not generated';
  return <ComparisonRow data-selected={selected}>
    <Td><Stack>
      <strong>{number}</strong>
      <Small>{claim ? `${claim.evidence.items.length} evidence items` : 'Lookup failed'}</Small>
    </Stack></Td>
    <Td>
      {data && label(data.research_decision)}
      {claim && !data && recommendation?.state !== 'error' && <Badge>{pendingLabel}</Badge>}
      {recommendation?.state === 'error' && <Copy role="alert">{recommendation.error}</Copy>}
      {result.state === 'error' && <Copy>{result.error.message}</Copy>}
    </Td>
    <Td>{status || 'Missing'}</Td>
    <Td>{data ? money(data.conditional_ceiling) : '—'}</Td>
    <Td><Link href="#claim-details" aria-current={selected ? 'true' : undefined} onClick={() => onSelect(number)}>Open {number}</Link></Td>
  </ComparisonRow>;
};

const summarizeRecommendations = (results: LookupResult[], states: ReadonlyMap<string, RecommendationState>) => {
  const foundNumbers: string[] = [];
  let completed = 0;
  let failures = 0;
  for (const result of results) {
    if (result.state !== 'found') continue;
    foundNumbers.push(result.tracking_number);
    const state = states.get(result.tracking_number);
    if (state?.state === 'succeeded') completed += 1;
    if (state?.state === 'error') failures += 1;
  }
  return { foundNumbers, completed, failures };
};

const LookupResults = ({ results, selected, onSelect }: { results: LookupResult[]; selected: string | null; onSelect: (number: string) => void }) => {
  const { states, busy, generate } = useRecommendations();
  const { foundNumbers, completed, failures } = summarizeRecommendations(results, states);
  const current = results.find(result => result.tracking_number === selected);
  return <Stack>
    {results.length > 1 && <Panel><Spread><Stack><Heading>Claims · {results.length}</Heading><Small>{foundNumbers.length} found · {results.length - foundNumbers.length} unavailable</Small></Stack><Button onClick={() => void generate(foundNumbers)} disabled={busy || !foundNumbers.length}>{busy ? 'Generating…' : 'Generate all recommendations'}</Button></Spread>
      {completed + failures > 0 && !busy && <Small>{completed} recommendations ready · {failures} failed</Small>}
      {busy && <WorkflowProgress label="Generating recommendations" completed={null} total={foundNumbers.length} />}
      <ComparisonScroll><Table><thead><tr><Th>Claim</Th><Th>Research decision</Th><Th>Recorded status</Th><Th>Ceiling if approved</Th><Th>Actions</Th></tr></thead><tbody>{results.map(result => <LookupResultRow
        key={result.tracking_number}
        result={result}
        recommendation={states.get(result.tracking_number)}
        selected={selected === result.tracking_number}
        onSelect={onSelect}
      />)}</tbody></Table></ComparisonScroll>
    </Panel>}
    <ClaimAnchor id="claim-details" tabIndex={-1} aria-label="Selected claim details">{current?.state === 'found' ? <ClaimReview key={current.tracking_number} claim={current.claim} recommendation={states.get(current.tracking_number) ?? null} onGenerate={() => void generate([current.tracking_number])} busy={busy} /> : <Panel><Heading>Lookup needs attention</Heading><Notice role="alert">{current?.state === 'error' ? current.error.message : 'Select a claim.'}</Notice><Copy>Correct the tracking number or retry the lookup. Other claims remain available.</Copy></Panel>}</ClaimAnchor>
  </Stack>;
};

export const Lookup = () => {
  const { input, setInput, results, selected, setSelected, error, busy, lookup, lookupId } = useLookup();
  return <Stack>
    <Panel>
      <Heading>Find a claim</Heading>
      <form onSubmit={event => { event.preventDefault(); void lookup(); }}><Stack>
        <Field>Tracking numbers<Textarea value={input} onChange={event => setInput(event.target.value)} rows={2} placeholder={'e.g. 14, 18, or 20-25'} required aria-describedby="lookup-help" /></Field>
        <Spread><Small id="lookup-help">Separate numbers or numeric ranges with commas or new lines. Up to 100 unique claims.</Small><Button disabled={busy} type="submit">{busy ? 'Looking up claims…' : 'Find claims'}</Button></Spread>
      </Stack></form>
      {busy && <WorkflowProgress label="Looking up claims" completed={null} total={1} detail="Reading claim records and supporting evidence from live sources." />}
      {error && <Notice role="alert">{error}</Notice>}
    </Panel>
    {results.length > 0 && <LookupResults key={lookupId} results={results} selected={selected} onSelect={setSelected} />}
  </Stack>;
};
