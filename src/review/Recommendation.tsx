import { label, money } from './api';
import type { RecommendationState } from './types';
import { WorkflowProgress } from './WorkflowProgress';
import { Badge, Button, Code, Copy, Definition, Details, Heading, Notice, Panel, Row, Secondary, Small, Stack, Stat, StatValue, Summary, Term, Value } from './styles';

export const Recommendation = ({ state, onGenerate, disabled }: { state: RecommendationState | null; onGenerate: () => void; disabled: boolean }) => {
  const data = state?.state === 'succeeded' ? state.data : null;
  const error = state?.state === 'error' ? state.error : null;
  const busy = state?.state === 'loading';
  return <Panel>
    <Row><Heading>Recommendation</Heading><Badge>Research only</Badge></Row>
    <Copy>Verify the source documents and governing policy before making a claim decision.</Copy>
    {busy && <WorkflowProgress label="Generating this recommendation" completed={null} total={1} detail="Checking the live inputs and running the research model." />}
    {error && <Notice role="alert">{error}</Notice>}
    {data && <Stack>
      <Stat><Small>Conditional approved ceiling</Small><StatValue>{money(data.conditional_ceiling)}</StatValue><Copy>{data.ceiling_condition}</Copy></Stat>
      <Notice>Suggested decision: {label(data.research_decision)}. This does not establish coverage or a payment amount.</Notice>
      {data.withholding_reasons.length > 0 && <Copy>Estimate withheld: {data.withholding_reasons.map(label).join('; ')}.</Copy>}
      <Details><Summary>What needs review · {data.review_reasons.length}</Summary><Stack>{data.review_reasons.map((reason, index) => <Copy key={index}>{label(reason)}</Copy>)}</Stack></Details>
      <Details><Summary>Estimate details and benchmarks</Summary><Stack><Copy>Ceiling: {label(data.ceiling_state)} · Bound: {label(data.ceiling_bound_state)}. Per-claim confidence is unavailable; this model is not calibrated for individual claims.</Copy><Definition><Term>Majority decision</Term><Value>{label(data.benchmarks.majority_decision)}</Value><Term>Training median ceiling</Term><Value>{money(data.benchmarks.training_median_ceiling)}</Value><Term>Recorded limit</Term><Value>{money(data.benchmarks.recorded_limit)}</Value><Term>Minimum request / limit</Term><Value>{money(data.benchmarks.minimum_request_limit)}</Value></Definition></Stack></Details>
      <Details><Summary>Inputs used and provenance</Summary><Stack>{data.inputs.map(input => <Stack key={input.column}><Definition><Term>{input.column}</Term><Value>{input.value || 'Missing'} · {label(input.availability)}</Value></Definition><Code>{input.source_reference}</Code></Stack>)}<Code>Model SHA-256: {data.model_sha256}</Code></Stack></Details>
    </Stack>}
    <Row>{data ? <Secondary onClick={onGenerate} disabled={disabled}>Regenerate recommendation</Secondary> : <Button onClick={onGenerate} disabled={disabled}>{busy ? 'Generating…' : 'Generate recommendation'}</Button>}</Row>
  </Panel>;
};
