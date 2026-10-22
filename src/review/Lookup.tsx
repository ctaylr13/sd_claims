import { Fragment } from 'react';
import { driveUrl, label, money } from './api';
import { Comparison, ModelAgreement, RecordedSummary } from './Comparison';
import { amountDifference, decisionMatch } from './comparisonFormatting';
import { useRecommendations } from './useRecommendations';
import { WorkflowProgress } from './WorkflowProgress';
import { useLookup } from './useLookup';
import { Recommendation } from './Recommendation';
import type { Claim, LookupResult, RecommendationState } from './types';
import { Badge, Button, Code, Copy, Definition, Details, Divider, Empty, Field, Grid, Heading, Link, List, Notice, Panel, Row, Small, Spread, Stack, Stat, StatValue, Summary, Term, Textarea, Value, Secondary, ComparisonScroll, ComparisonRow, ClaimAnchor, Table, Th, Td } from './styles';

const ClaimReview = ({ claim, recommendation, onGenerate, busy }: { claim: Claim; recommendation: RecommendationState | null; onGenerate: () => void; busy: boolean }) => <Stack>
  <Panel>
    <Spread><Heading>Claim {claim.tracking_number}</Heading><Badge data-state="Needs review">Needs review</Badge></Spread>
    <Notice>Recorded inputs are unverified at decision time. Evidence presence does not mean the evidence is complete or approved.</Notice>
    <Definition>{Object.entries(claim.prediction_inputs).map(([key, field]) => <Fragment key={key}><Term>{label(key)}</Term><Value>{field.recorded_value || 'Missing'}</Value></Fragment>)}</Definition>
    <Divider /><Small>Source: {claim.claims_source.name ?? claim.claims_source.id ?? 'Claims register'} · Read {claim.claims_source.read_at ? new Date(claim.claims_source.read_at).toLocaleString() : 'time unavailable'}</Small>
    <Code>Source ID: {claim.claims_source.id ?? 'Unavailable'} · Version: {claim.claims_source.version ?? 'Unavailable'}</Code>
  </Panel>
  {recommendation?.state === 'succeeded' ? <Comparison comparison={recommendation.data.historical_comparison} /> : <Panel><Heading>Recorded outcome</Heading><RecordedSummary claim={claim} /></Panel>}
  <Recommendation state={recommendation} onGenerate={onGenerate} disabled={busy} />
  <Panel>
    <Spread><Heading>Evidence</Heading><Badge>{claim.evidence.items.length} items</Badge></Spread>
    {claim.evidence.state === 'missing' && <Notice>No evidence folder was found. Request source documents before reviewing this claim.</Notice>}
    {claim.evidence.folder && <Link href={`https://drive.google.com/drive/folders/${encodeURIComponent(claim.evidence.folder.id)}`} target="_blank" rel="noreferrer">Open evidence folder ↗</Link>}
    {!claim.evidence.items.length && claim.evidence.state !== 'missing' && <Copy>The evidence folder is empty.</Copy>}
    <List>{claim.evidence.items.map(item => <Row key={item.id}><Link href={driveUrl(item.id)} target="_blank" rel="noreferrer">{item.name} ↗</Link><Small>{item.mimeType?.split('/').at(-1)}</Small></Row>)}</List>
  </Panel>
  <Panel><Details><Summary>Historical outcome · reference only</Summary><Notice>These recorded outcomes are historical context. They are not passed into the recommendation model and do not establish an independently verified answer.</Notice><Definition>{Object.entries(claim.historical_outcome).map(([key, value]) => <Fragment key={key}><Term>{key}</Term><Value>{value || 'Not recorded'}</Value></Fragment>)}</Definition></Details></Panel>
</Stack>;

const LookupResults = ({ results, selected, onSelect }: { results: LookupResult[]; selected: string | null; onSelect: (number: string) => void }) => {
  const { states, busy, generate } = useRecommendations();
  const found = results.filter(result => result.state === 'found');
  const current = results.find(result => result.tracking_number === selected);
  const completed = found.flatMap(result => {
    const state = states.get(result.tracking_number);
    return state?.state === 'succeeded' ? [state.data] : [];
  });
  const failures = found.filter(result => states.get(result.tracking_number)?.state === 'error').length;
  const pending = found.filter(result => states.get(result.tracking_number)?.state === 'loading').length;
  const processed = completed.length + failures;
  const scored = completed.filter(item => item.historical_comparison.decision_match !== null);
  const matched = scored.filter(item => item.historical_comparison.decision_match);
  return <Stack>
    <Panel><Spread><Stack><Heading>Results · compare predictions</Heading><Copy>Generate recommendations here to compare each research prediction with its recorded outcome. Open a claim for evidence and the full explanation.</Copy></Stack><Button onClick={() => void generate(found.map(result => result.tracking_number))} disabled={busy || !found.length}>{busy ? 'Generating recommendations…' : 'Generate all recommendations'}</Button></Spread>
      <ModelAgreement />
      {found.length > 0 && <WorkflowProgress
        label={busy ? 'Generating recommendations' : 'Recommendation progress'}
        completed={busy ? null : processed}
        total={found.length}
        detail={busy ? `Processing ${pending} ${pending === 1 ? 'claim' : 'claims'}. Results appear when the request finishes.` : `${completed.length} ready · ${failures} failed · ${found.length - processed} not generated`}
      />}
      <Small>{matched.length} of {scored.length} scored recorded decisions match in this lookup</Small>
      <Copy>Lookup comparisons may include training claims. These matches do not measure validation accuracy or per-claim confidence. Ceiling differences apply only to scoreable approved outcomes.</Copy>
      <ComparisonScroll><Table><thead><tr><Th>Claim</Th><Th>Research decision</Th><Th>Recorded status</Th><Th>Decision match</Th><Th>Ceiling if approved</Th><Th>Recorded approved amount</Th><Th>Ceiling difference</Th><Th>Actions</Th></tr></thead><tbody>{results.map(result => {
        const number = result.tracking_number;
        const state = states.get(number);
        const data = state?.state === 'succeeded' ? state.data : null;
        const comparison = data?.historical_comparison;
        const claim = result.state === 'found' ? result.claim : null;
        const status = comparison ? comparison.recorded_status : claim?.historical_outcome.Status;
        const amount = comparison ? comparison.recorded_amount_raw : claim?.historical_outcome['Approved Benefit Amount'];
        return <ComparisonRow key={number} data-selected={selected === number}>
          <Td><Stack><strong>{number}</strong><Small>{claim ? `${claim.evidence.items.length} evidence items` : 'Lookup failed'}</Small></Stack></Td>
          <Td>{data ? label(data.research_decision) : <Badge>{state?.state === 'loading' ? 'Generating…' : 'Not generated'}</Badge>}{state?.state === 'error' && <Copy role="alert">{state.error}</Copy>}{result.state === 'error' && <Copy>{result.error.message}</Copy>}</Td>
          <Td>{status || 'Missing'}</Td>
          <Td><Badge data-state={comparison?.decision_match ? 'passed' : 'Needs review'}>{comparison ? decisionMatch(comparison.decision_match) : 'Not compared'}</Badge></Td>
          <Td>{data ? money(data.conditional_ceiling) : '—'}</Td>
          <Td>{amount || 'Missing'}</Td>
          <Td>{comparison ? amountDifference(comparison.difference_dollars) : '—'}</Td>
          <Td><Row><Link href="#claim-details" aria-current={selected === number ? 'true' : undefined} onClick={() => onSelect(number)}>Open {number}</Link>{claim && <Secondary disabled={busy} aria-label={`Generate recommendation for claim ${number}`} onClick={() => void generate([number])}>{data ? 'Regenerate' : 'Generate'}</Secondary>}</Row></Td>
        </ComparisonRow>;
      })}</tbody></Table></ComparisonScroll>
    </Panel>
    <ClaimAnchor id="claim-details" tabIndex={-1} aria-label="Selected claim details">{current?.state === 'found' ? <ClaimReview claim={current.claim} recommendation={states.get(current.tracking_number) ?? null} onGenerate={() => void generate([current.tracking_number])} busy={busy} /> : <Panel><Heading>Lookup needs attention</Heading><Notice role="alert">{current?.state === 'error' ? current.error.message : 'Select a claim.'}</Notice><Copy>Correct the tracking number or retry the lookup. Other claims remain available.</Copy></Panel>}</ClaimAnchor>
  </Stack>;
};

export const Lookup = () => {
  const { input, setInput, results, selected, setSelected, error, busy, lookup, lookupId } = useLookup();
  const found = results.filter(result => result.state === 'found').length;
  return <Stack>
    <Panel>
      <Heading>Find claims</Heading><Copy>Look up one claim or review a list from the live claims register.</Copy>
      <form onSubmit={event => { event.preventDefault(); void lookup(); }}><Stack>
        <Field>Tracking numbers<Textarea value={input} onChange={event => setInput(event.target.value)} placeholder={'Enter tracking numbers or a range, e.g. 14-25'} required aria-describedby="lookup-help" /></Field>
        <Spread><Small id="lookup-help">Separate numbers or numeric ranges with commas or new lines. Up to 100 unique claims.</Small><Button disabled={busy} type="submit">{busy ? 'Looking up claims…' : 'Look up claims →'}</Button></Spread>
      </Stack></form>
      {busy && <WorkflowProgress label="Looking up claims" completed={null} total={1} detail="Reading claim records and supporting evidence from live sources." />}
      {error && <Notice role="alert">{error}</Notice>}
    </Panel>
    {results.length > 0 ? <>
      <Grid><Stat><Small>Claims requested</Small><StatValue>{results.length}</StatValue></Stat><Stat><Small>Found in register</Small><StatValue>{found}</StatValue></Stat><Stat><Small>Lookup errors</Small><StatValue>{results.length - found}</StatValue></Stat></Grid>
      <LookupResults key={lookupId} results={results} selected={selected} onSelect={setSelected} />
    </> : <Empty><Heading>Your review starts here</Heading><Copy>Enter tracking numbers above to see claim details, supporting documents, and research recommendations.</Copy></Empty>}
  </Stack>;
};
