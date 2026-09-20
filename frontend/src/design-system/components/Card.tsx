import type { HTMLAttributes } from "react";

type CardProps = HTMLAttributes<HTMLDivElement>;

export function Card({ className = "", ...props }: CardProps) {
  return (
    <div
      className={["bg-[var(--color-bg-default)] rounded-lg shadow-md", "p-[var(--space-5)]", className].join(" ")}
      {...props}
    />
  );
}
