import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { KvTable } from "./kv-table";
import { researchStubView } from "../lib/research";

export function ResearchStub() {
  const view = researchStubView();
  return (
    <Card aria-label="RESEARCH">
      <CardHeader>
        <CardTitle>RESEARCH / hypotheses</CardTitle>
        <CardDescription>{view.reason}</CardDescription>
      </CardHeader>
      <CardContent>
        <KvTable
          rows={[
            { label: "Status", value: view.status, tone: "warn" },
            { label: "Hypothesis", value: view.hypothesis, tone: "warn" },
            { label: "Universe", value: view.universe, tone: "warn" },
            { label: "Train / val / OOS", value: view.trainValidationOos, tone: "warn" },
          ]}
        />
      </CardContent>
    </Card>
  );
}
