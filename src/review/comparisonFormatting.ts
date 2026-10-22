import { money } from './api';

export const decisionMatch = (value: boolean | null) => value === null ? 'Unscored' : value ? 'Matches' : 'Differs';
export const amountDifference = (value: number | null) => {
  if (value === null) return 'Unscored';
  if (value === 0) return '$0.00';
  return `${value > 0 ? '+' : '−'}${money(Math.abs(value))}`;
};

