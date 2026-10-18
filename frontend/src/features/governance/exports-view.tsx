"use client";

import { ROUTES } from "@/lib/routes";
import { useDemoState } from "@/lib/store/hooks";
import { ButtonLink } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/misc";
import { DataTable, Td, Th } from "@/components/ui/table";

export function ExportsView() {
  const exports = useDemoState().exports;
  return (
    <Card className="overflow-hidden">
      <div className="px-5 py-4">
        <CardHeader
          title="Evidence exports"
          description="Manifest and bundle hashes for tamper-evidence checks."
          actions={
            <ButtonLink href={ROUTES.case("CASE-0412", "report")} size="sm">
              Open report builder
            </ButtonLink>
          }
        />
      </div>
      {exports.length === 0 ? (
        <EmptyState title="No exports yet." />
      ) : (
        <DataTable caption="Evidence exports">
          <thead>
            <tr>
              <Th>Export</Th>
              <Th>Case</Th>
              <Th>Created by</Th>
              <Th>Created at</Th>
              <Th>Narrative</Th>
              <Th>Bundle hash</Th>
            </tr>
          </thead>
          <tbody>
            {exports.map((record) => (
              <tr key={record.id}>
                <Td className="font-mono">{record.id}</Td>
                <Td className="font-mono">{record.caseId}</Td>
                <Td>{record.by}</Td>
                <Td className="text-xs text-muted">{record.at}</Td>
                <Td className="font-mono">{record.revision}</Td>
                <Td className="font-mono text-xs text-muted">sha256:{record.hash}… (simulated)</Td>
              </tr>
            ))}
          </tbody>
        </DataTable>
      )}
    </Card>
  );
}
