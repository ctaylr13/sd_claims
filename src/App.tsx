import { useState } from 'react';
import { SourceConnection } from './review/SourceConnection';
import { BatchReview } from './review/BatchReview';
import { Metrics } from './review/Metrics';
import { Brand, Header, Main, Shell, Small, Tab, Tabs } from './review/styles';

type Screen = 'lookup' | 'batches' | 'metrics';
const screens: { id: Screen; title: string }[] = [
  { id: 'lookup', title: 'Claims' }, { id: 'batches', title: 'Document review' }, { id: 'metrics', title: 'Model evaluation' },
];
const App = () => {
  const [screen, setScreen] = useState<Screen>('lookup');
  return <Shell>
    <Header><Brand>Claims workspace</Brand><Small>Security deposits · Research only</Small></Header>
    <Main>
      <Tabs aria-label="Review workspace">{screens.map(item => <Tab key={item.id} aria-current={screen === item.id ? 'page' : undefined} onClick={() => setScreen(item.id)}>{item.title}</Tab>)}</Tabs>
      <div hidden={screen !== 'lookup'}><SourceConnection /></div>
      <div hidden={screen !== 'batches'}><BatchReview /></div>
      {screen === 'metrics' && <Metrics />}
    </Main>
  </Shell>;
};
export default App;
