import { useState } from 'react';
import { Lookup } from './review/Lookup';
import { BatchReview } from './review/BatchReview';
import { Metrics } from './review/Metrics';
import { Brand, Copy, Eyebrow, Header, Main, Shell, Small, Tab, Tabs, Title } from './review/styles';

type Screen = 'lookup' | 'batches' | 'metrics';
const screens: { id: Screen; title: string }[] = [
  { id: 'lookup', title: 'Claim lookup' }, { id: 'batches', title: 'Document review' }, { id: 'metrics', title: 'Evaluation metrics' },
];
const App = () => {
  const [screen, setScreen] = useState<Screen>('lookup');
  return <Shell>
    <Header><Brand>Claims workspace<span aria-hidden="true"> ↗</span></Brand><Small>Security deposit claims · Local research</Small></Header>
    <Main><Eyebrow>Review workspace</Eyebrow><Title>A clearer path through every claim.</Title><Copy>Bring the claim, the evidence, and the open questions into one place. Review the source before making the call.</Copy>
      <Tabs aria-label="Review workspace">{screens.map(item => <Tab key={item.id} aria-current={screen === item.id ? 'page' : undefined} onClick={() => setScreen(item.id)}>{item.title}</Tab>)}</Tabs>
      <div hidden={screen !== 'lookup'}><Lookup /></div>
      <div hidden={screen !== 'batches'}><BatchReview /></div>
      {screen === 'metrics' && <Metrics />}
    </Main>
  </Shell>;
};
export default App;
