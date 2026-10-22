import { styled } from '@linaria/react';

export const Shell = styled.div`
  :global(*) { box-sizing: border-box; }
  :global(body) { margin: 0; background: #f5f5f0; color: #243d39; font-family: Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; font-size: 14px; }
  :global(button), :global(input), :global(textarea) { font: inherit; }
  :global(button:focus-visible), :global(a:focus-visible), :global(input:focus-visible), :global(textarea:focus-visible), :global(summary:focus-visible) { outline: 3px solid #b58232; outline-offset: 3px; }
  :global(button) { cursor: pointer; }
  :global(button:disabled) { cursor: wait; opacity: .55; }
  :global(a) { color: #236b60; }
  :global(h1), :global(h2), :global(h3), :global(p) { margin: 0; }
  min-height: 100vh;
`;
export const Header = styled.header`display: flex; justify-content: space-between; align-items: center; gap: 20px; padding: 22px 5vw; background: #193e37; color: #f5f5f0;`;
export const Brand = styled.h1`font-family: Georgia, serif; font-weight: 400; font-size: 24px; letter-spacing: -.5px;`;
export const Small = styled.span`font-size: 12px; opacity: .75; line-height: 1.6;`;
export const Main = styled.main`max-width: 1440px; margin: auto; padding: 0 5vw 48px;`;
export const Copy = styled.p`color: #68776f; line-height: 1.65; max-width: 760px;`;
export const Heading = styled.h2`font-family: Georgia, serif; font-weight: 400; font-size: 23px;`;
export const Subheading = styled.h3`font-size: 14px; font-weight: 650;`;
export const Row = styled.div`display: flex; align-items: center; flex-wrap: wrap; gap: 12px;`;
export const Spread = styled.div`display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 16px;`;
export const Stack = styled.div`display: grid; grid-template-columns: minmax(0, 1fr); gap: 16px; min-width: 0;`;
export const Grid = styled.div`display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 14px;`;
export const Columns = styled.div`display: grid; grid-template-columns: minmax(250px, .75fr) minmax(0, 2fr); gap: 24px; margin-top: 24px; align-items: start; @media(max-width: 850px) { grid-template-columns: 1fr; }`;
export const Panel = styled.section`background: #fff; border: 1px solid #dce1d9; border-radius: 12px; padding: 24px; display: grid; grid-template-columns: minmax(0, 1fr); gap: 16px; min-width: 0;`;
export const Tabs = styled.nav`display: flex; gap: 26px; border-bottom: 1px solid #d7ded5; margin: 30px 0 26px; overflow-x: auto;`;
export const Tab = styled.button`border: 0; border-bottom: 2px solid transparent; background: transparent; color: #718077; padding: 0 0 15px; white-space: nowrap; &[aria-current='page'] { color: #224b41; border-color: #224b41; font-weight: 650; }`;
export const Button = styled.button`background: #245548; color: white; border: 1px solid #245548; border-radius: 6px; padding: 11px 17px; font-weight: 600; &:hover { background: #193e37; }`;
export const Secondary = styled.button`background: white; color: #36534a; border: 1px solid #d2dcd2; border-radius: 6px; padding: 10px 14px; &:hover { background: #f0f4ed; } &[aria-pressed='true'] { background: #e8f1e9; border-color: #3d6d58; }`;
export const Field = styled.label`display: grid; gap: 9px; font-weight: 600; line-height: 1.5;`;
export const Input = styled.input`border: 1px solid #cbd5ca; border-radius: 6px; padding: 11px 12px; min-width: 0; width: 100%; color: #243d39; background: #fff;`;
export const Textarea = styled.textarea`width: 100%; border: 1px solid #cbd5ca; border-radius: 7px; padding: 14px; min-height: 76px; resize: vertical; &[data-size="large"] { min-height: 160px; } color: #243d39; line-height: 1.6;`;
export const Notice = styled.div`border: 1px solid #e6d9b8; background: #fcf9ef; border-radius: 7px; padding: 14px 16px; color: #7b6331; line-height: 1.6; font-size: 13px; &[role='alert'] { background: #fff4f0; border-color: #edcfc2; color: #934d34; }`;
export const Badge = styled.span`display: inline-block; border-radius: 4px; background: #eff1ec; color: #657168; padding: 5px 8px; font-size: 11px; font-weight: 650; white-space: nowrap; &[data-state='found'], &[data-state='succeeded'], &[data-state='Extracted'], &[data-state='present'], &[data-state='passed'] { background: #e8f1e8; color: #376b48; } &[data-state='failed'], &[data-state='error'], &[data-state='conflicting'] { background: #fbece6; color: #9c543c; } &[data-state='Needs review'], &[data-state='running'] { background: #f7eedb; color: #8f6c29; }`;
export const Stat = styled.div`padding: 19px; border: 1px solid #dce1d9; border-radius: 9px; background: #fafbf7; display: grid; gap: 8px;`;
export const StatValue = styled.strong`font-family: Georgia, serif; font-size: 28px; font-weight: 400; overflow-wrap: anywhere;`;
export const List = styled.div`display: grid; gap: 8px; max-height: 570px; overflow: auto;`;
export const Item = styled.button`display: grid; gap: 8px; text-align: left; border: 1px solid #e2e7df; background: #fff; color: #29483e; padding: 16px; border-radius: 7px; width: 100%; &[aria-pressed='true'] { background: #eff5ed; border-color: #4c745d; }`;
export const Empty = styled.div`padding: 44px 20px; text-align: center; display: grid; justify-items: center; gap: 12px; border: 1px dashed #cdd9cc; border-radius: 10px; background: #f9faf6;`;
export const Details = styled.details`border-top: 1px solid #e4e9e0; padding-top: 16px; min-width: 0;`;
export const Summary = styled.summary`cursor: pointer; font-weight: 600; padding-bottom: 14px; line-height: 1.6;`;
export const Definition = styled.dl`display: grid; grid-template-columns: minmax(120px, 1fr) minmax(0, 1.5fr); gap: 14px 20px; margin: 0; font-size: 13px; line-height: 1.6;`;
export const Term = styled.dt`color: #768077;`;
export const Value = styled.dd`margin: 0; overflow-wrap: anywhere;`;
export const Link = styled.a`font-size: 13px; text-underline-offset: 3px; overflow-wrap: anywhere;`;
export const Quote = styled.blockquote`margin: 12px 0; padding: 10px 14px; border-left: 3px solid #98b294; color: #52624e; background: #f7f9f3; line-height: 1.7; white-space: pre-wrap; overflow-wrap: anywhere;`;
export const Code = styled.code`font-size: 11px; overflow-wrap: anywhere; color: #718077;`;
export const Progress = styled.progress`
  appearance: none;
  display: block;
  width: 100%;
  height: 9px;
  border: 0;
  border-radius: 99px;
  overflow: hidden;
  background: #e7eee3;
  accent-color: #426f52;
  &::-webkit-progress-bar { background: #e7eee3; border-radius: 99px; }
  &::-webkit-progress-value { background: #426f52; border-radius: 99px; transition: width .25s ease; }
  &::-moz-progress-bar { background: #426f52; border-radius: 99px; }
  &:indeterminate {
    background: linear-gradient(90deg, #e7eee3 20%, #87a88b 50%, #e7eee3 80%);
    background-size: 200% 100%;
    animation: pending-progress 1.5s linear infinite;
  }
  &:indeterminate::-webkit-progress-bar, &:indeterminate::-moz-progress-bar { background: transparent; }
  @keyframes pending-progress { from { background-position: 200% 0; } to { background-position: 0 0; } }
  @media (prefers-reduced-motion: reduce) { animation: none; &::-webkit-progress-value { transition: none; } }
`;
export const PageCanvas = styled.svg`width: 100%; height: auto; border: 1px solid #d6dfd2; border-radius: 6px; background: #f4f5ef;`;
export const SourceImage = styled.image`width: 100%; height: 100%;`;
export const RegionHighlight = styled.rect`fill: #efbd36; fill-opacity: .25; stroke: #a07813; stroke-width: 1.5; pointer-events: none;`;
export const TableWrap = styled.div`overflow-x: auto;`;
export const Table = styled.table`width: 100%; border-collapse: collapse; text-align: left; font-size: 13px;`;
export const Th = styled.th`padding: 14px 12px; border-bottom: 1px solid #dbe3d7; font-weight: 600; color: #6a796c; white-space: nowrap;`;
export const Td = styled.td`padding: 15px 12px; border-bottom: 1px solid #e8ece4; line-height: 1.6;`;
export const ComparisonScroll = styled.div`overflow: auto; max-height: 620px; min-width: 0; th { position: sticky; top: 0; background: #f7f9f3; z-index: 1; } table { min-width: 720px; } td { vertical-align: top; }`;
export const ComparisonRow = styled.tr`&[data-selected='true'] { background: #eef5eb; }`;

export const ClaimAnchor = styled.div`min-width: 0; scroll-margin-top: 20px;`;
