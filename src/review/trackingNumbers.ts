const MAX_CLAIMS = 100;

export const parseTrackingNumbers = (input: string): string[] => {
  const numbers = new Set<string>();
  const add = (number: string) => {
    if (number.length > 128) throw new Error('Each tracking number must be no more than 128 characters.');
    numbers.add(number);
    if (numbers.size > MAX_CLAIMS) throw new Error('Look up no more than 100 unique claims at a time.');
  };
  for (const token of input.split(/[,\n]/).map(value => value.trim()).filter(Boolean)) {
    const range = /^(\d+)\s*[-–—]\s*(\d+)$/.exec(token);
    if (!range) { add(token); continue; }
    const first = Number(range[1]);
    const last = Number(range[2]);
    if (!Number.isSafeInteger(first) || !Number.isSafeInteger(last)) {
      throw new Error('Numeric range endpoints are too large. Enter individual tracking numbers instead.');
    }
    if (first > last) throw new Error(`Range ${token} is reversed. Enter the smaller number first.`);
    if (last - first >= MAX_CLAIMS) throw new Error('A range can contain no more than 100 claims.');
    const width = range[1].startsWith('0') ? range[1].length : 0;
    for (let number = first; number <= last; number++) add(String(number).padStart(width, '0'));
  }
  if (!numbers.size) throw new Error('Enter tracking numbers or a numeric range, such as 14-25.');
  return [...numbers];
};
