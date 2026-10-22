import { useEffect, useRef, useState } from 'react';
import { documentUrl, label } from './api';
import { useResource } from './useResource';
import type { Citation, Fact, Report } from './types';
import { Badge, Code, Copy, Details, Empty, PageCanvas, SourceImage, RegionHighlight, Heading, Link, Notice, Panel, Quote, Row, Secondary, Small, Spread, Stack, Subheading, Summary, Table, TableWrap, Td, Th } from './styles';

type EvidenceLocation = { documentIndex: number; page: number; citation: Citation | null };
const FactReview = ({ name, fact, onCitation }: { name: string; fact: Fact; onCitation: (citation: Citation) => void }) => <Details>
  <Summary>{label(name)} <Badge data-state={fact.state}>{label(fact.state)}</Badge></Summary>
  <Stack>{fact.reason && <Copy>{fact.reason}</Copy>}{fact.observations.map((observation, index) => <Stack key={index}>
    <Copy>Observed: {observation.raw_value || 'Unreadable'} · Normalized: {observation.value ?? 'Unknown'}</Copy>
    {observation.limitations.map((limitation, index) => <Small key={index}>{limitation}</Small>)}
    <Row>{observation.citations.map((citation, index) => <Secondary key={index} onClick={() => onCitation(citation)}>{citation.document_id ?? citation.source_id} · {citation.page ? `page ${citation.page}` : `row ${citation.row}`}</Secondary>)}</Row>
  </Stack>)}</Stack>
</Details>;

const SourcePage = ({ source, page, citation }: { source: string; page: { number: number; width: number; height: number }; citation: Citation | null }) => {
  const [failed, setFailed] = useState(false);
  if (failed) return <Notice role="alert">The verified page image is unavailable. Reload the report or open the retained PDF.</Notice>;
  const bounds = citation?.bbox;
  return <PageCanvas viewBox={`0 0 ${page.width} ${page.height}`} role="img" aria-label={`Retained source page ${page.number}${bounds ? ', cited region highlighted' : ''}`}>
    <SourceImage href={source} width={page.width} height={page.height} onError={() => setFailed(true)} />
    {bounds && <RegionHighlight x={bounds[0]} y={bounds[1]} width={bounds[2] - bounds[0]} height={bounds[3] - bounds[1]} />}
  </PageCanvas>;
};

const ReportReview = ({ report, batchId, index }: { report: Report; batchId: string; index: number }) => {
  const [location, setLocation] = useState<EvidenceLocation | null>(null);
  const viewer = useRef<HTMLElement>(null);
  useEffect(() => {
    if (location && viewer.current) {
      viewer.current.focus({ preventScroll: true });
      viewer.current.scrollIntoView({ block: 'start' });
    }
  }, [location]);
  const openCitation = (citation: Citation) => {
    const documentIndex = report.documents.findIndex(item => item.document.document_id === citation.document_id);
    setLocation({ documentIndex, page: citation.page ?? 1, citation });
  };
  const source = location && location.documentIndex >= 0 ? documentUrl(batchId, index, location.documentIndex) : null;
  const selectedDocument = location && location.documentIndex >= 0 ? report.documents[location.documentIndex]?.document : null;
  const page = selectedDocument?.pages.find(page => page.number === location?.page);
  const rows = report.validation.documents.flatMap(document => document.rows);
  const needsReview = rows.filter(row => row.status === 'Needs review').length;
  return <Stack>
    <Panel><Spread><Heading>Claim {report.claim_id}</Heading><Badge data-state="Needs review">Needs review</Badge></Spread>
      <Copy>{rows.length} row outcomes · {needsReview} need review · {report.documents.length} documents</Copy>
      <Notice>Extraction completion does not establish accuracy or approval. OCR observations require visual review. Unknown amounts stay unknown.</Notice>
      <FactReview name="Effective monthly rent" fact={report.effective_monthly_rent} onCitation={openCitation} />
      <FactReview name="Requested insurer reimbursement" fact={report.requested_insurer_reimbursement} onCitation={openCitation} />
      <Details><Summary>Extraction limitations</Summary><Stack>{report.limitations.map((limitation, index) => <Copy key={index}>{limitation}</Copy>)}<Small>{report.parser_version} · {new Date(report.created_at).toLocaleString()}</Small></Stack></Details>
    </Panel>
    {location && <Panel ref={viewer} tabIndex={-1} aria-label="Source evidence viewer">
      <Spread><Heading>Source evidence</Heading><Secondary onClick={() => setLocation(null)}>Close viewer</Secondary></Spread>
      {location.citation && <Stack><Quote>{location.citation.quote}</Quote><Small>{location.citation.method ? label(location.citation.method) : 'Recorded source cell'} · {location.citation.bbox ? `Region: ${location.citation.bbox.map(value => value.toFixed(1)).join(', ')} (page points, top-left origin)` : `Row ${location.citation.row}, ${location.citation.column}`}</Small><Code>SHA-256: {location.citation.sha256 ?? 'Source cell; see source reference'}</Code></Stack>}
      {source ? <><Link href={`${source}#page=${location.page}`} target="_blank" rel="noreferrer">Open retained PDF · page {location.page} ↗</Link>{page ? <Stack><Row><Secondary disabled={location.page <= 1} onClick={() => setLocation({ ...location, page: location.page - 1, citation: null })}>Previous page</Secondary><Small>Page {location.page} of {selectedDocument?.pages.length} · {label(page.method)} · {label(page.state)}</Small><Secondary disabled={location.page >= (selectedDocument?.pages.length ?? 0)} onClick={() => setLocation({ ...location, page: location.page + 1, citation: null })}>Next page</Secondary></Row><SourcePage key={`${source}-${location.page}`} source={`${source}/pages/${location.page}`} page={page} citation={location.citation} /></Stack> : <Notice>Page image unavailable. Open the retained PDF to inspect the original.</Notice>}</> : <Notice>This citation refers to a recorded source cell. Source: {location.citation?.source_id ?? 'Unavailable'}. A retained PDF is not available for this citation.</Notice>}
    </Panel>}
    {report.documents.map((item, documentIndex) => {
      const document = item.document;
      const validation = report.validation.documents.find(item => item.document_id === document.document_id);
      return <Panel key={document.document_id}>
        <Spread><Stack><Heading>{document.document_id}</Heading><Small>{label(document.kind)}</Small></Stack><Secondary onClick={() => setLocation({ documentIndex, page: 1, citation: null })}>View retained PDF ↗</Secondary></Spread>
        <Code>SHA-256: {document.sha256 ?? 'Source unavailable'}</Code>
        {document.errors.map((error, index) => <Notice key={index} role="alert">{label(error)}</Notice>)}
        {item.limitations.map((limitation, index) => <Copy key={index}>{limitation}</Copy>)}
        <Subheading>Extracted fields</Subheading>
        {Object.entries(item.fields).map(([name, fact]) => <FactReview key={name} name={name} fact={fact} onCitation={openCitation} />)}
        <Details open><Summary>Row outcomes · {validation?.rows.length ?? 0}</Summary><Stack>{validation?.rows.map((row, index) => <Stack key={`${row.row_id}-${index}`}>
          <Spread><Subheading>{row.row_id}</Subheading><Badge data-state={row.status}>{row.status}</Badge></Spread>
          <Copy>Amount: {row.amount ?? 'Unknown'}{row.reasons.length > 0 ? ` · ${row.reasons.map(label).join('; ')}` : ''}</Copy>
          <Row>{row.citations.map((citation, index) => <Secondary key={index} onClick={() => openCitation(citation)}>Source · page {citation.page}</Secondary>)}</Row>
        </Stack>)}{!validation?.rows.length && <Copy>No row outcomes recovered. Review the document and extracted fields.</Copy>}</Stack></Details>
        <Details><Summary>Automated checks and issues</Summary><Stack><Copy>Passing checks apply only within their stated scope; they do not establish visual accuracy.</Copy><TableWrap><Table><thead><tr><Th>Check</Th><Th>Result</Th><Th>Reason</Th></tr></thead><tbody>{validation?.checks.map((check, index) => <tr key={index}><Td>{label(check.check)}</Td><Td><Badge data-state={check.status}>{label(check.status)}</Badge></Td><Td>{check.reason ?? '—'}</Td></tr>)}</tbody></Table></TableWrap>{validation?.issues.map((issue, index) => <Copy key={index}>{label(issue.code)}{issue.field ? ` · ${issue.field}` : ''}{issue.reason ? ` · ${issue.reason}` : ''}</Copy>)}</Stack></Details>
      </Panel>;
    })}
    <Empty><Heading>Next: independent review</Heading><Copy>Check the cited evidence, resolve conflicting or unreadable facts, and verify the governing policy. Extraction reports do not generate claim approvals.</Copy></Empty>
  </Stack>;
};
export const Evidence = ({ batchId, index }: { batchId: string; index: number }) => {
  const { data, error, reload } = useResource<Report>(`/api/batches/${encodeURIComponent(batchId)}/claims/${index}/result`);
  if (error) return <Panel><Notice role="alert">{error}</Notice><Secondary onClick={reload}>Reload result</Secondary></Panel>;
  if (!data) return <Panel><Copy role="status">Loading verified extraction report…</Copy></Panel>;
  return <ReportReview key={`${batchId}-${index}`} report={data} batchId={batchId} index={index} />;
};
