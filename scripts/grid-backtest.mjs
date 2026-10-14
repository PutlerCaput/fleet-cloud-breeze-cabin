const symbol = process.argv[2] || "DOGE_USDT";
const fee = 0.0002;
const fundingPerBar = 0.0001 / 32;

const response = await fetch(`https://api.mexc.com/api/v1/contract/kline/${symbol}?interval=Min15`);
const body = await response.json();
const close = (body.data?.close ?? []).map(Number).filter((price) => price > 0);
if (close.length < 40) {
  console.error("Мало свечей");
  process.exit(1);
}

function run(prices, step = 0.008) {
  let cash = 320;
  let qty = 0;
  let avg = 0;
  let trades = 0;
  let wins = 0;
  let peak = 320;
  let stopped = false;
  let anchor = prices[0];
  for (const price of prices) {
    if (stopped) break;
    if (qty > 0) cash -= qty * price * fundingPerBar;
    if (qty === 0 && price < anchor * (1 - step) && cash > 40) {
      const notional = Math.min(38, cash * 0.12);
      const add = notional / price;
      avg = price;
      qty = add;
      cash -= notional * fee;
      anchor = price;
    }
    if (qty > 0 && price > avg * (1 + step)) {
      const pnl = (price - avg) * qty - qty * price * fee;
      cash += pnl;
      trades += 1;
      if (pnl >= 0) wins += 1;
      qty = 0;
      avg = 0;
      anchor = price;
    }
    const equity = cash + (qty > 0 ? (price - avg) * qty : 0);
    peak = Math.max(peak, equity);
    if (peak - equity > 320 * 0.08 || (qty > 0 && price < avg * 0.88)) {
      if (qty > 0) {
        const pnl = (price - avg) * qty - qty * price * fee;
        cash += pnl;
        trades += 1;
        if (pnl >= 0) wins += 1;
        qty = 0;
      }
      stopped = true;
    }
  }
  const equity = cash + (qty > 0 ? (prices.at(-1) - avg) * qty : 0);
  return { equity, trades, wins, stopped };
}

function resample(prices) {
  const returns = [];
  for (let i = 1; i < prices.length; i += 1) returns.push(prices[i] / prices[i - 1] - 1);
  const path = [prices[0]];
  for (let i = 0; i < returns.length; i += 1) {
    const shock = returns[Math.floor(Math.random() * returns.length)];
    path.push(Math.max(0.0001, path[path.length - 1] * (1 + shock)));
  }
  return path;
}

const base = run(close);
const samples = Array.from({ length: 400 }, () => run(resample(close)).equity).sort((a, b) => a - b);
const pct = (p) => samples[Math.floor((samples.length - 1) * p)];
console.log(
  JSON.stringify(
    {
      symbol,
      bars: close.length,
      equity: Number(base.equity.toFixed(2)),
      trades: base.trades,
      wins: base.wins,
      stopped: base.stopped,
      monteCarloP5: Number(pct(0.05).toFixed(2)),
      monteCarloP50: Number(pct(0.5).toFixed(2)),
      cleared: pct(0.05) > 320,
    },
    null,
    2,
  ),
);
