import type { ComponentProps, ReactNode } from "react";

import { cn } from "@/lib/utils";

function Card({ className, ...props }: ComponentProps<"section">) {
  return <section data-slot="card" className={cn("card", className)} {...props} />;
}

function CardHeader({
  title,
  description,
  actions,
  className,
  ...props
}: ComponentProps<"div"> & {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div data-slot="card-header" className={cn("card-head", className)} {...props}>
      <div className="card-head-text">
        <h2 className="card-title">{title}</h2>
        {description === undefined ? null : <p className="card-desc">{description}</p>}
      </div>
      {actions === undefined ? null : <div className="card-actions">{actions}</div>}
    </div>
  );
}

function CardBody({
  flush = false,
  className,
  ...props
}: ComponentProps<"div"> & { flush?: boolean }) {
  return (
    <div
      data-slot="card-body"
      className={cn("card-body", flush && "card-body-flush", className)}
      {...props}
    />
  );
}

function CardFooter({ className, ...props }: ComponentProps<"div">) {
  return <div data-slot="card-footer" className={cn("card-foot", className)} {...props} />;
}

/** Technical detail stays collapsed until the operator asks for it. */
function CardDisclosure({
  summary,
  children,
  className,
  ...props
}: ComponentProps<"details"> & { summary: string }) {
  return (
    <details data-slot="card-disclosure" className={cn("disclosure", className)} {...props}>
      <summary>{summary}</summary>
      <div className="disclosure-body">{children}</div>
    </details>
  );
}

export { Card, CardBody, CardDisclosure, CardFooter, CardHeader };
