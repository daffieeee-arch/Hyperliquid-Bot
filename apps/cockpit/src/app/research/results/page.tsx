import { HypothesisResultsScreen } from "../../../components/research/hypothesis-results-screen";
import { listHypothesisResults } from "../../../lib/hypothesis-results";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export default function HypothesisResultsPage() {
  const initial = listHypothesisResults(process.env);
  return <HypothesisResultsScreen initial={initial} />;
}
