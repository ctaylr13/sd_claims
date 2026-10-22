import { styled } from '@linaria/react';
import { Progress, Small, Spread } from './styles';

const ProgressGroup = styled.div`display: grid; gap: 10px; min-width: 0;`;

type WorkflowProgressProps = {
  label: string;
  completed: number | null;
  total: number;
  detail?: string;
};

export const WorkflowProgress = ({ label, completed, total, detail }: WorkflowProgressProps) => {
  const description = completed === null ? 'In progress' : `${completed} of ${total} · ${Math.round(completed / total * 100)}%`;
  return <ProgressGroup>
    <Spread role="status"><strong>{label}</strong><Small>{description}</Small></Spread>
    <Progress value={completed ?? undefined} max={total} aria-label={label} aria-valuetext={description} />
    {detail && <Small>{detail}</Small>}
  </ProgressGroup>;
};
