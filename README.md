# Security deposit claims assistant

Given a tracking number, review the claim evidence, estimate approval and payout,
and compare the recommendation with the historical decision.

The repository currently contains a React/TypeScript shell and a local CSV audit.
Evidence extraction, rules, trained models, and the claim review screen are still
on the roadmap. See [the implementation roadmap in Notion](https://app.notion.com/p/3ff7a370410d8071bbc1f13c68b994c9).

## Audit the local data

Requires Python 3; no additional packages are needed.

```sh
python3 scripts/audit_claims.py 'Security Deposit Claims - Security Deposit Claims.csv' --output data/claims-audit.json
```

The audit reports aggregate counts, missing values, status/payout combinations,
and payouts above the recorded claim amount or policy limit. It does not export
individual claim records. The source CSV and generated `data/` directory are
ignored by Git.

## Frontend

```sh
npm install
npm run dev
```

`npm run build` typechecks and builds the frontend; `npm run lint` runs ESLint.
