import type { Driver } from "@/lib/types";
import { prettyFeature } from "@/lib/ui";

// Plain-English translation for the model's SHAP drivers. The model speaks in
// finance jargon (earnings_yield, profit_margin…); this turns each driver into
// a sentence a non-expert can read, and stitches the strongest +/- into a
// single "here's the gist" line. Deterministic and free — no LLM call — so it
// shows for every stock, every time.

type Copy = {
  label: string; // friendly name
  what: string; // what it measures, in plain words
  plus: string; // phrase used when it is PUSHING the score up (shap >= 0)
  minus: string; // phrase used when it is DRAGGING the score down (shap < 0)
  posShort: string; // short noun phrase for the one-line gist, when helping
  negShort: string; // short noun phrase for the one-line gist, when hurting
};

const GLOSSARY: Record<string, Copy> = {
  profit_margin: {
    label: "Profit margin",
    what: "how much of each sales dollar the company keeps as profit.",
    plus: "Its healthy profitability is helping the score.",
    minus:
      "It's very profitable — but the model is discounting that here, usually because such quality is already baked into an expensive price.",
    posShort: "its strong profitability",
    negShort: "quality that's already priced in",
  },
  earnings_yield: {
    label: "Earnings yield",
    what: "the company's yearly profit compared with its price (the flip side of the P/E ratio) — higher means cheaper.",
    plus: "It looks reasonably priced for the profit you get — a plus.",
    minus:
      "It looks expensive — you're paying a lot for each dollar of profit, which historically means less room to climb.",
    posShort: "its reasonable price",
    negShort: "how expensive it looks",
  },
  fcf_yield: {
    label: "Free-cash-flow yield",
    what: "the actual spare cash the business throws off, compared with its price — higher means more cash for your money.",
    plus: "You're getting solid cash flow for the price — a plus.",
    minus: "You're paying up relative to the cash it actually generates — a negative.",
    posShort: "the cash it generates",
    negShort: "how little cash you get for the price",
  },
  roe: {
    label: "Return on equity",
    what: "how much profit it squeezes out of shareholders' money.",
    plus: "Strong returns on capital are helping.",
    minus: "The model is treating its returns-on-capital as a negative here.",
    posShort: "its strong returns on capital",
    negShort: "its returns on capital",
  },
  debt_ratio: {
    label: "Debt load",
    what: "how much debt it carries relative to its size.",
    plus: "Its debt looks manageable — a plus.",
    minus: "Its heavier debt load is read as a risk.",
    posShort: "its manageable debt",
    negShort: "its heavier debt load",
  },
  trend_200: {
    label: "Long-term trend",
    what: "whether it's trading above its 200-day (long-term) average.",
    plus: "It's in a healthy long-term uptrend — a plus.",
    minus: "It's slipping below its long-term trend — a negative.",
    posShort: "its long-term uptrend",
    negShort: "its weakening long-term trend",
  },
  mom_12_1: {
    label: "1-year momentum",
    what: "how much the price has climbed over the past year (ignoring the last month).",
    plus: "Its year-long climb is helping the score.",
    minus: "Its softer one-year trend is holding the score back.",
    posShort: "its year-long climb",
    negShort: "its soft one-year trend",
  },
  mom_6_1: {
    label: "6-month momentum",
    what: "how much the price has climbed over the past six months.",
    plus: "Its recent six-month climb is helping.",
    minus: "Its weaker six-month trend is holding it back.",
    posShort: "its recent six-month climb",
    negShort: "its weak six-month trend",
  },
  vol_3m: {
    label: "Volatility",
    what: "how jumpy the price has been over the last three months.",
    plus: "Its steadier price action is a small plus.",
    minus: "Its choppier, more volatile price is a negative.",
    posShort: "its steady price action",
    negShort: "how jumpy the price is",
  },
  rsi_14: {
    label: "Overbought/oversold",
    what: "a gauge of whether it's run up too fast (overbought) or been beaten down (oversold) lately.",
    plus: "It isn't overbought, leaving room to run — a plus.",
    minus: "It looks stretched/overbought recently — a negative.",
    posShort: "room left to run",
    negShort: "how stretched it looks",
  },
  sentiment: {
    label: "News sentiment",
    what: "the overall tone of recent news about the company.",
    plus: "Recent news skews positive — a small plus.",
    minus: "Recent news skews negative — a small drag.",
    posShort: "its positive news tone",
    negShort: "its negative news tone",
  },
};

export type DriverPlain = {
  feature: string;
  label: string;
  pos: boolean;
  text: string;
};

function isPos(d: Driver): boolean {
  return Number.isFinite(d.shap) ? d.shap >= 0 : d.direction === "+";
}

/** One plain-English line per driver (what it means + which way it's pushing). */
export function explainDrivers(drivers: Driver[]): DriverPlain[] {
  return drivers.map((d) => {
    const pos = isPos(d);
    const copy = GLOSSARY[d.feature];
    if (!copy) {
      return {
        feature: d.feature,
        label: prettyFeature(d.feature),
        pos,
        text: pos
          ? "The model reads this as a plus here."
          : "The model reads this as a negative here.",
      };
    }
    return {
      feature: d.feature,
      label: copy.label,
      pos,
      text: `${copy.what} ${pos ? copy.plus : copy.minus}`,
    };
  });
}

function cap(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** A single "here's the gist" sentence from the strongest + and − driver. */
export function driverLead(drivers: Driver[]): string | null {
  const scored = drivers.filter((d) => Number.isFinite(d.shap));
  if (scored.length === 0) return null;

  const pos = scored
    .filter((d) => d.shap >= 0)
    .sort((a, b) => b.shap - a.shap)[0];
  const neg = scored
    .filter((d) => d.shap < 0)
    .sort((a, b) => a.shap - b.shap)[0];

  const posShort = pos ? GLOSSARY[pos.feature]?.posShort : undefined;
  const negShort = neg ? GLOSSARY[neg.feature]?.negShort : undefined;

  if (posShort && negShort) {
    return cap(`the model likes ${posShort}, but it's wary of ${negShort}.`);
  }
  if (posShort) {
    return cap(`the model is mainly encouraged by ${posShort}.`);
  }
  if (negShort) {
    return cap(`the model is mainly held back by ${negShort}.`);
  }
  return null;
}
