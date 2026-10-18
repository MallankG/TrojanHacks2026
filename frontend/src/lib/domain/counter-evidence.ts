import { whyFlagged, type CitedPoint } from "@/lib/domain/alert-insights";
import { ROUTES } from "@/lib/routes";
import type { ScoredAlert, Tone } from "@/lib/types";

/**
 * "Why this alert?" paired with "Why might it be benign?" (PRD v3.3 section 20.1). Each positive
 * signal is an observation; it carries its strongest counter-evidence, the data that is missing,
 * a competing explanation and the next verification step. Hypotheses are never shown as findings,
 * and the system never states a conclusion: that is the reviewer's decision.
 */

export type CounterStrength = "Strong" | "Moderate" | "Weak" | "None found";

export interface SignalPair {
  signal: CitedPoint;
  counter: CitedPoint | null;
  strength: CounterStrength;
  missing: CitedPoint[];
  competing: string;
  nextStep: { text: string; cites: string[]; href?: string };
}

export function strengthTone(strength: CounterStrength): Tone {
  switch (strength) {
    case "Strong":
      return "ok";
    case "Moderate":
      return "info";
    case "Weak":
      return "mute";
    default:
      return "warn";
  }
}

const p = (text: string, cites: string[]): CitedPoint => ({ text, cites });

export function signalPairs(alert: ScoredAlert, fixed: boolean): SignalPair[] {
  const signals = whyFlagged(alert, fixed);
  if (alert.id === "AL-1027") {
    const [rapid, newAccount, path, device] = signals;
    return [
      {
        signal: rapid,
        counter: p("Treasury sweeps move funds just as fast. CASE-0301 had this shape and was closed as benign.", ["CASE-0301"]),
        strength: "Moderate",
        missing: [p("No invoice or payment instruction for the $58,200.00 ACH.", ["DQ-GAP-INV"])],
        competing: "ACC-1104 may have been tricked into paying a new supplier, so the funds could be scam proceeds rather than layering.",
        nextStep: { text: "Ask ACC-1104's relationship manager whether the payment was authorized and for what.", cites: ["TX-88201"], href: ROUTES.entity("ACC-1104") },
      },
      {
        signal: newAccount,
        counter: p("Salary deposits from a known employer since Sep 30 show some ordinary use of the account.", ["ACC-2208"]),
        strength: "Weak",
        missing: [p("No source-of-funds statement for the one-off $58,200.00 credit.", [])],
        competing: "A new customer receiving a legitimate one-off payment, such as a property sale or loan proceeds.",
        nextStep: { text: "Compare observed activity with the expected-activity form on the entity profile.", cites: ["ACC-2208"], href: ROUTES.entity("ACC-2208") },
      },
      fixed
        ? {
            signal: path,
            counter: p("The cross-border beneficiary is synthetic metadata only, so its purpose cannot be assessed.", ["TX-88266"]),
            strength: "Moderate",
            missing: [p("No beneficiary details for the exit wire.", ["TX-88266"])],
            competing: "Balances are mixed at each hop; similar amounts may be coincidence rather than the same funds.",
            nextStep: { text: "Request beneficiary details for the cross-border wire through the correspondent.", cites: ["TX-88266"] },
          }
        : {
            signal: path,
            counter: null,
            strength: "None found",
            missing: [p("The ACC-3315 to ACC-4471 hop is in a quarantined batch. The path cannot be judged either way.", ["DQ-1042"])],
            competing: "The funds may have stopped at ACC-3315. Missing data is not evidence of either outcome.",
            nextStep: { text: "Resolve the data hold so the path can be evaluated.", cites: ["DQ-1042"], href: ROUTES.data },
          },
      {
        signal: device,
        counter: p("Households and shared phones also share device tokens; an address token also matches (fuzzy, 0.83).", ["DV-77"]),
        strength: "Moderate",
        missing: [p("No household or relationship data on file for ACC-2208 and ACC-3391.", [])],
        competing: "A family member using the same phone, with an unrelated $9,600.00 transfer.",
        nextStep: { text: "Review linked alert AL-1029 and the approved entity links before relying on the device signal.", cites: ["AL-1029"], href: ROUTES.riskAlert("AL-1029") },
      },
    ];
  }
  if (alert.id === "AL-1033") {
    const [burst, volume] = signals;
    return [
      {
        signal: burst,
        counter: p("The same 214 recipients are paid every 2 weeks for 18 months.", ["DF-1027"]),
        strength: "Strong",
        missing: [p("The payroll file itself is not attached to the alert.", [])],
        competing: "Scheduled payroll by an established business.",
        nextStep: { text: "Match the recipients against the business payroll file and close if they agree.", cites: ["DF-1027"] },
      },
      {
        signal: volume,
        counter: p("Periodicity pulls the model score down; CASE-0301 shows a similar pattern closed as benign.", ["MR-2291", "CASE-0301"]),
        strength: "Strong",
        missing: [],
        competing: "Seasonal bonus run on top of normal payroll.",
        nextStep: { text: "Confirm the amount against the prior four payroll runs.", cites: ["MR-2291"] },
      },
    ];
  }
  return signals.map((signal, i) => ({
    signal,
    counter: null,
    strength: "None found",
    missing: alert.dataQuality === "Complete" ? [] : [p(`${alert.dataQuality}. Missing data is not treated as low risk.`, [])],
    competing: i === 0 ? "Activity consistent with the customer's business that the baseline has not seen yet." : "A score above threshold ranks a lead for review; it is not a finding.",
    nextStep: { text: "Check expected activity and prior reviewed cases before deciding.", cites: [] },
  }));
}

export interface ReasoningLadder {
  observations: CitedPoint[];
  hypotheses: string[];
  conclusion: string;
}

export function reasoningLadder(alert: ScoredAlert, fixed: boolean): ReasoningLadder {
  const pairs = signalPairs(alert, fixed);
  const lead = alert.benign ? "Scheduled business activity that resembles the typology." : `${alert.typology}, as ranked by the detectors.`;
  return {
    observations: pairs.map((pair) => pair.signal),
    hypotheses: [lead, ...pairs.map((pair) => pair.competing)],
    conclusion: "None drawn by the system. A human reviewer weighs these and records the disposition.",
  };
}
