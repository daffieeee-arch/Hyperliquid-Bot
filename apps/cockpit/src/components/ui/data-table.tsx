"use client";

import {
  createColumnHelper,
  createSortedRowModel,
  rowSortingFeature,
  sortFn_alphanumeric,
  sortFn_text,
  tableFeatures,
  useTable,
  type ColumnDef,
  type RowData,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export const dataTableFeatures = tableFeatures({
  rowSortingFeature,
  sortedRowModel: createSortedRowModel(),
  sortFns: { alphanumeric: sortFn_alphanumeric, text: sortFn_text },
});

export type DataTableFeatures = typeof dataTableFeatures;

export type DataTableColumns<TRow extends RowData> = ColumnDef<DataTableFeatures, TRow, unknown>[];

export function dataTableColumnHelper<TRow extends RowData>() {
  return createColumnHelper<DataTableFeatures, TRow>();
}

/**
 * Sortable, horizontally scrollable table.
 *
 * `numericColumns` right-aligns tabular figures; `cellClassName` lets a screen
 * tone individual cells without re-implementing the table shell.
 */
export function DataTable<TRow extends RowData>({
  columns,
  data,
  caption,
  numericColumns = [],
  monoColumns = [],
  cellClassName,
  emptyLabel = "No rows.",
  nowrap = false,
}: {
  columns: DataTableColumns<TRow>;
  data: TRow[];
  caption?: ReactNode;
  numericColumns?: readonly string[];
  monoColumns?: readonly string[];
  cellClassName?: (columnId: string, row: TRow) => string | undefined;
  emptyLabel?: string;
  /** Keep cells on one line and let `.table-scroll` scroll horizontally instead. */
  nowrap?: boolean;
}) {
  const table = useTable({
    features: dataTableFeatures,
    columns,
    data,
    enableSortingRemoval: false,
  });

  if (data.length === 0) {
    return <p className="empty">{emptyLabel}</p>;
  }

  return (
    <div className="table-scroll">
      <table className={nowrap ? "dt dt-nowrap" : "dt"}>
        {caption === undefined ? null : <caption>{caption}</caption>}
        <thead>
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id}>
              {group.headers.map((header) => {
                const sorted = header.column.getIsSorted();
                const canSort = header.column.getCanSort();
                return (
                  <th
                    key={header.id}
                    className={cn(numericColumns.includes(header.column.id) && "num")}
                    aria-sort={
                      sorted === "asc"
                        ? "ascending"
                        : sorted === "desc"
                          ? "descending"
                          : canSort
                            ? "none"
                            : undefined
                    }
                  >
                    {header.isPlaceholder ? null : canSort ? (
                      <button type="button" onClick={header.column.getToggleSortingHandler()}>
                        <table.FlexRender header={header} />
                        {sorted === "asc" ? (
                          <ArrowUp size={11} aria-hidden="true" />
                        ) : sorted === "desc" ? (
                          <ArrowDown size={11} aria-hidden="true" />
                        ) : (
                          <ChevronsUpDown size={11} aria-hidden="true" opacity={0.45} />
                        )}
                      </button>
                    ) : (
                      <table.FlexRender header={header} />
                    )}
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id}>
              {row.getAllCells().map((cell) => (
                <td
                  key={cell.id}
                  className={cn(
                    numericColumns.includes(cell.column.id) && "num",
                    monoColumns.includes(cell.column.id) && "cell-id",
                    cellClassName?.(cell.column.id, row.original),
                  )}
                >
                  <table.FlexRender cell={cell} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
