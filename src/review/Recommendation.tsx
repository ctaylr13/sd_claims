import { label, money } from './api';
import type { RecommendationState } from './types';
import { WorkflowProgress } from './WorkflowProgress';
import { Badge, Button, Code, Copy, Definition, Details, Heading, Notice, Panel, Row, Small, Stack, Stat, StatValue, Summary, Term, Value } from './styles';

export const Recommendation = ({ state, onGenerate, disabled }: { state: RecommendationState | null; onGenerate: () => void; disabled: boolean }) => {
  const data = state?.state === 'succeeded' ? state.data : null;
  const error = state?.state === 'error' ? state.error : null;
  const busy = state?.state === 'loading';
  return <Panel>
    <Row><Heading>Recommendation</Heading><Badge>Research only</Badge><Badge data-state="Needs review">Needs review</Badge></Row>
    <Copy>Governing policy and decision-time availability are unverified. A research estimate requires independent review before any claim decision.</Copy>
    {busy && <WorkflowProgress label="Generating this recommendation" completed={null} total={1} detail="Checking the live inputs and running the research model." />}
    {error && <Notice role="alert">{error}</Notice>}
    {data && <Stack>
      <Stat><Small>Conditional approved ceiling</Small><StatValue>{money(data.conditional_ceiling)}</StatValue><Copy>{data.ceiling_condition}</Copy></Stat>
      <Notice>Historical-pattern suggestion: {label(data.research_decision)}. This does not establish coverage or a payment amount. Ceiling: {label(data.ceiling_state)}; bound: {label(data.ceiling_bound_state)}.</Notice>
      {data.withholding_reasons.length > 0 && <Copy>Estimate withheld: {data.withholding_reasons.map(label).join('; ')}.</Copy>}
      <Copy>Per-claim confidence: unavailable. This model has not been calibrated to estimate the probability that an individual prediction is correct.</Copy>
      <Details open><Summary>What needs review · {data.review_reasons.length}</Summary><Stack>{data.review_reasons.map((reason, index) => <Copy key={index}>{label(reason)}</Copy>)}</Stack></Details>
      <Details><Summary>Comparison benchmarks</Summary><Definition><Term>Majority decision</Term><Value>{label(data.benchmarks.majority_decision)}</Value><Term>Training median ceiling</Term><Value>{money(data.benchmarks.training_median_ceiling)}</Value><Term>Recorded limit</Term><Value>{money(data.benchmarks.recorded_limit)}</Value><Term>Minimum request / limit</Term><Value>{money(data.benchmarks.minimum_request_limit)}</Value></Definition></Details>
      <Details><Summary>Inputs used and provenance</Summary><Stack>{data.inputs.map(input => <Stack key={input.column}><Definition><Term>{input.column}</Term><Value>{input.value || 'Missing'} · {label(input.availability)}</Value></Definition><Code>{input.source_reference}</Code></Stack>)}<Code>Model SHA-256: {data.model_sha256}</Code></Stack></Details>
    </Stack>}
    <Row><Button onClick={onGenerate} disabled={disabled}>{busy ? 'Generating…' : 'Generate research recommendation'}</Button></Row>
  </Panel>;
};
