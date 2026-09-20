import type { ButtonHTMLAttributes } from "react";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement>;

export function Button({ className = "", ...props }: ButtonProps) {
  return (
    <button
      className={[
        "bg-accent text-[var(--color-bg-default)] rounded-md shadow-sm",
        "px-[var(--space-4)] py-[var(--space-2)]",
        "hover:bg-accent-emphasis transition-colors",
        className,
      ].join(" ")}
      {...props}
    />
  );
}
